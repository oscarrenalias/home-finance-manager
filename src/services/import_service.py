"""Service-layer types and ImportService for the CSV import pipeline."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from domain.balance_check import BalanceCheckResult, check_balances
from domain.classification import match_rules
from domain.matching import ExistingRecord, MatchCandidate, match_rows
from domain.parser import parse_csv
from storage.file_store import FileStore
from storage.models import AuditEvent, Classification, ClassificationRule, ImportBatch, Job, SourceObservation, Transaction

# Per-account locks to serialize concurrent imports (A18).
# SQLite has no row-level locking; a threading lock is sufficient for single-process deployment.
_account_locks_meta: threading.Lock = threading.Lock()
_account_locks: dict[str, threading.Lock] = {}


def _get_account_lock(account_id: str) -> threading.Lock:
    """Return (creating if needed) the per-account commit lock.

    _account_locks_meta guards the map itself; _account_locks[id] guards
    the commit critical section for that account.  Two accounts never
    contend on each other's lock.
    """
    with _account_locks_meta:
        if account_id not in _account_locks:
            _account_locks[account_id] = threading.Lock()
        return _account_locks[account_id]


@dataclass
class RowPreview:
    row_number: int
    display_text: str
    parsed_date: date
    amount_cents: int
    is_pending: bool
    match_candidate: MatchCandidate | None = None


@dataclass
class ImportPreview:
    """Read-only snapshot returned by ImportService.preview().

    idempotency_token is a UUID generated at preview time and must be passed
    unchanged to commit().  It is not persisted until commit() succeeds; a
    second preview() call for the same file produces a different token and a
    different commit transaction.
    """

    filename: str
    file_hash: str
    parser_version: str
    idempotency_token: str
    already_imported: bool
    same_file_different_account: bool
    executed_count: int
    pending_count: int
    new_count: int
    exact_match_count: int
    probable_match_count: int
    ambiguous_count: int
    error_count: int
    row_previews: list[RowPreview] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    balance_check: BalanceCheckResult | None = None


@dataclass
class CommitResult:
    batch_id: str
    new_transactions: int
    reused_transactions: int
    pending_observations: int
    enqueued_jobs: int
    ambiguous_count: int


class ImportService:
    """Coordinates CSV parsing, duplicate detection, and (later) commit to the ledger."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        file_store: FileStore,
    ) -> None:
        self._session_factory = session_factory
        self._file_store = file_store

    def preview(
        self,
        account_id: str,
        filename: str,
        data: bytes,
        coverage_start: date | None,
        coverage_end: date | None,
        completeness: bool,
    ) -> ImportPreview:
        """Parse and match without writing to the database.

        Steps: save file → check duplicates → parse → load existing → match → balance check.
        """
        # 1. Persist the raw file bytes and obtain its SHA-256 hash.
        _, file_hash = self._file_store.save(account_id, filename, data)

        # 2. Check for previously committed batches with the same hash.
        already_imported = False
        same_file_different_account = False
        existing_records: list[ExistingRecord] = []

        session = self._session_factory()
        try:
            same_account = (
                session.query(ImportBatch)
                .filter(
                    ImportBatch.file_hash == file_hash,
                    ImportBatch.account_id == account_id,
                    ImportBatch.state == "committed",
                )
                .first()
            )
            already_imported = same_account is not None

            diff_account = (
                session.query(ImportBatch)
                .filter(
                    ImportBatch.file_hash == file_hash,
                    ImportBatch.account_id != account_id,
                    ImportBatch.state == "committed",
                )
                .first()
            )
            same_file_different_account = diff_account is not None

            # 4. Load existing transactions for the account to build the match pool.
            txns = (
                session.query(Transaction)
                .filter(Transaction.account_id == account_id)
                .all()
            )
            existing_records = [
                ExistingRecord(
                    transaction_id=t.id,
                    date=t.date,
                    amount_cents=t.amount_cents,
                    display_text=t.display_text,
                    balance_after=t.balance_after,
                    status=t.status,
                )
                for t in txns
            ]
        finally:
            session.close()

        # 3. Parse the CSV bytes (pure domain logic — no I/O).
        parse_result = parse_csv(data)

        # 5. Match parsed rows against the existing ledger pool.
        candidates = match_rows(parse_result.rows, existing_records)

        # 6. Validate balance arithmetic across consecutive executed rows.
        balance_check = check_balances(parse_result.rows)

        # 7. Generate a fresh idempotency token for the eventual commit call.
        idempotency_token = str(uuid4())

        # 8. Derive counts.  Pending rows are always confidence="new" from match_rows,
        #    but are tracked separately; new_count covers only executed rows.
        executed_count = sum(1 for r in parse_result.rows if not r.is_pending)
        pending_count = sum(1 for r in parse_result.rows if r.is_pending)

        new_count = sum(
            1 for c in candidates if c.confidence == "new" and not c.parsed_row.is_pending
        )
        exact_match_count = sum(1 for c in candidates if c.confidence == "exact")
        probable_match_count = sum(1 for c in candidates if c.confidence == "probable")
        ambiguous_count = sum(1 for c in candidates if c.confidence == "ambiguous")
        error_count = sum(1 for r in parse_result.rows if r.parse_errors)

        candidate_by_row = {c.parsed_row.row_number: c for c in candidates}
        row_previews = [
            RowPreview(
                row_number=r.row_number,
                display_text=r.display_text,
                parsed_date=r.parsed_date,
                amount_cents=r.parsed_amount_cents,
                is_pending=r.is_pending,
                match_candidate=candidate_by_row.get(r.row_number),
            )
            for r in parse_result.rows
        ]

        return ImportPreview(
            filename=filename,
            file_hash=file_hash,
            parser_version=parse_result.parser_version,
            idempotency_token=idempotency_token,
            already_imported=already_imported,
            same_file_different_account=same_file_different_account,
            executed_count=executed_count,
            pending_count=pending_count,
            new_count=new_count,
            exact_match_count=exact_match_count,
            probable_match_count=probable_match_count,
            ambiguous_count=ambiguous_count,
            error_count=error_count,
            row_previews=row_previews,
            parse_errors=parse_result.header_errors,
            balance_check=balance_check,
        )

    def commit(
        self,
        account_id: str,
        idempotency_token: str,
        preview: ImportPreview,
        coverage_start: date | None,
        coverage_end: date | None,
        completeness: bool,
    ) -> CommitResult:
        """Commit a previewed import to the ledger.

        Idempotency (A02/A18): if a committed ImportBatch with the same
        idempotency_token already exists, the original result is returned
        immediately without writing anything.  This check runs once before the
        lock (fast path to avoid contention) and once inside the lock (to handle
        the race where two callers passed the fast path simultaneously).

        Lock scope: the per-account threading lock covers the ledger read,
        all row inserts, and session.commit().  Nothing outside this window
        assumes exclusive access.  Two different accounts never contend because
        each account has its own lock (see _get_account_lock).

        Re-matching: match_rows is called again inside the lock against the
        current ledger state, not the state captured at preview() time, because
        another commit may have landed in between.
        """
        # Fast-path idempotency check outside the lock to avoid unnecessary contention.
        session = self._session_factory()
        try:
            existing = (
                session.query(ImportBatch)
                .filter(
                    ImportBatch.idempotency_token == idempotency_token,
                    ImportBatch.state == "committed",
                )
                .first()
            )
            if existing is not None:
                return _commit_result_from_batch(existing)
        finally:
            session.close()

        lock = _get_account_lock(account_id)
        with lock:
            session = self._session_factory()
            try:
                # Re-check inside the lock to handle the race where two callers passed
                # the fast-path simultaneously.
                existing = (
                    session.query(ImportBatch)
                    .filter(
                        ImportBatch.idempotency_token == idempotency_token,
                        ImportBatch.state == "committed",
                    )
                    .first()
                )
                if existing is not None:
                    return _commit_result_from_batch(existing)

                # Load current ledger state for re-matching inside the lock (A18: ledger
                # may have changed between preview() and commit()).
                txns = (
                    session.query(Transaction)
                    .filter(Transaction.account_id == account_id)
                    .all()
                )
                current_records = [
                    ExistingRecord(
                        transaction_id=t.id,
                        date=t.date,
                        amount_cents=t.amount_cents,
                        display_text=t.display_text,
                        balance_after=t.balance_after,
                        status=t.status,
                    )
                    for t in txns
                ]

                # Recover ParsedRow objects from the preview (match_candidate carries the
                # original parsed row from parse time; no re-parsing or file I/O needed).
                parsed_rows = [
                    rp.match_candidate.parsed_row
                    for rp in preview.row_previews
                    if rp.match_candidate is not None
                ]

                candidates = match_rows(parsed_rows, current_records)

                batch = ImportBatch(
                    account_id=account_id,
                    filename=preview.filename,
                    file_hash=preview.file_hash,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    completeness_declared=completeness,
                    state="committed",
                    parser_version=preview.parser_version,
                    row_count=preview.executed_count + preview.pending_count,
                    executed_count=preview.executed_count,
                    pending_count=preview.pending_count,
                    error_count=preview.error_count,
                    idempotency_token=idempotency_token,
                )
                session.add(batch)
                session.flush()  # populate batch.id before referencing it below

                new_transactions = 0
                reused_transactions = 0
                pending_observations = 0
                enqueued_jobs = 0
                ambiguous_count = 0
                new_executed_transactions: list[Transaction] = []

                for candidate in candidates:
                    row = candidate.parsed_row
                    if row.parse_errors:
                        # Row has sentinel values (date.min / amount 0) — skip entirely
                        continue
                    raw_balance = row.raw_balance if row.raw_balance else None

                    if row.is_pending:
                        obs = SourceObservation(
                            batch_id=batch.id,
                            transaction_id=None,
                            row_number=row.row_number,
                            raw_date=row.raw_date,
                            raw_category=row.raw_category,
                            raw_subcategory=row.raw_subcategory,
                            raw_text=row.raw_text,
                            raw_amount=row.raw_amount,
                            raw_balance=raw_balance,
                            raw_status=row.raw_status,
                            raw_reconciled=row.raw_reconciled,
                            parsed_date=row.parsed_date,
                            parsed_amount_cents=row.parsed_amount_cents,
                            parsed_balance_cents=row.parsed_balance_cents,
                            parsed_status=row.raw_status,
                            is_pending=True,
                        )
                        session.add(obs)
                        pending_observations += 1

                    elif candidate.confidence == "exact":
                        obs = SourceObservation(
                            batch_id=batch.id,
                            transaction_id=candidate.existing_transaction_id,
                            row_number=row.row_number,
                            raw_date=row.raw_date,
                            raw_category=row.raw_category,
                            raw_subcategory=row.raw_subcategory,
                            raw_text=row.raw_text,
                            raw_amount=row.raw_amount,
                            raw_balance=raw_balance,
                            raw_status=row.raw_status,
                            raw_reconciled=row.raw_reconciled,
                            parsed_date=row.parsed_date,
                            parsed_amount_cents=row.parsed_amount_cents,
                            parsed_balance_cents=row.parsed_balance_cents,
                            parsed_status=row.raw_status,
                            is_pending=False,
                        )
                        session.add(obs)
                        reused_transactions += 1

                    else:
                        # new, probable, ambiguous: new Transaction + SourceObservation + classify Job
                        if candidate.confidence == "ambiguous":
                            ambiguous_count += 1

                        txn_id = str(uuid4())
                        txn = Transaction(
                            id=txn_id,
                            account_id=account_id,
                            date=row.parsed_date,
                            amount_cents=row.parsed_amount_cents,
                            balance_after=row.parsed_balance_cents,
                            currency="EUR",
                            original_text=row.raw_text,
                            display_text=row.display_text,
                            status=row.raw_status,
                            transaction_type="unknown",
                        )
                        session.add(txn)

                        obs = SourceObservation(
                            batch_id=batch.id,
                            transaction_id=txn_id,
                            row_number=row.row_number,
                            raw_date=row.raw_date,
                            raw_category=row.raw_category,
                            raw_subcategory=row.raw_subcategory,
                            raw_text=row.raw_text,
                            raw_amount=row.raw_amount,
                            raw_balance=raw_balance,
                            raw_status=row.raw_status,
                            raw_reconciled=row.raw_reconciled,
                            parsed_date=row.parsed_date,
                            parsed_amount_cents=row.parsed_amount_cents,
                            parsed_balance_cents=row.parsed_balance_cents,
                            parsed_status=row.raw_status,
                            is_pending=False,
                        )
                        session.add(obs)

                        job = Job(
                            kind="classify",
                            state="pending",
                            inputs={"transaction_id": txn_id},
                        )
                        session.add(job)

                        new_executed_transactions.append(txn)
                        new_transactions += 1
                        enqueued_jobs += 1

                # Apply confirmed classification rules to newly inserted executed transactions.
                # Rules are loaded once per import and applied to the current batch only.
                if new_executed_transactions:
                    confirmed_rules = (
                        session.query(ClassificationRule)
                        .filter(ClassificationRule.is_confirmed.is_(True))
                        .order_by(ClassificationRule.priority.desc())
                        .all()
                    )
                    for txn in new_executed_transactions:
                        matched = match_rules(txn.display_text, txn.merchant, confirmed_rules)  # type: ignore[arg-type]
                        if matched is not None:
                            clf = Classification(
                                transaction_id=txn.id,
                                source="rule",
                                transaction_type=matched.transaction_type,
                                category_id=matched.category_id,
                                merchant=matched.merchant,
                            )
                            session.add(clf)

                audit = AuditEvent(
                    entity_type="import_batch",
                    entity_id=batch.id,
                    action="committed",
                    actor="system",
                    before_state=None,
                    after_state={
                        "state": "committed",
                        "file_hash": batch.file_hash,
                        "row_count": batch.row_count,
                        "executed_count": batch.executed_count,
                        "pending_count": batch.pending_count,
                        "new_transactions": new_transactions,
                        "reused_transactions": reused_transactions,
                        "pending_observations": pending_observations,
                        "enqueued_jobs": enqueued_jobs,
                    },
                )
                session.add(audit)
                session.commit()

                return CommitResult(
                    batch_id=batch.id,
                    new_transactions=new_transactions,
                    reused_transactions=reused_transactions,
                    pending_observations=pending_observations,
                    enqueued_jobs=enqueued_jobs,
                    ambiguous_count=ambiguous_count,
                )
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()


def _commit_result_from_batch(batch: ImportBatch) -> CommitResult:
    """Reconstruct a CommitResult from a previously committed ImportBatch (idempotency path).

    Transaction/observation counts are not stored on the batch; they will be derived
    by the observation-creation bead once that is integrated. Until then, counts default to 0.
    """
    return CommitResult(
        batch_id=batch.id,
        new_transactions=0,
        reused_transactions=0,
        pending_observations=0,
        enqueued_jobs=0,
        ambiguous_count=0,
    )
