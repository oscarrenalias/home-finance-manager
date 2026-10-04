"""Service-layer types and ImportService for the CSV import pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from domain.balance_check import BalanceCheckResult, check_balances
from domain.matching import ExistingRecord, MatchCandidate, match_rows
from domain.parser import parse_csv
from storage.file_store import FileStore
from storage.models import ImportBatch, Transaction


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
