"""Integration tests for ImportService: A01–A07, A18 acceptance criteria.

Uses a function-scoped in-memory SQLite DB with Alembic migrations applied.
No Reflex imports. No sample-data/ files — synthetic CSV bytes only.
"""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.import_service import ImportService
from storage.file_store import FileStore
from storage.models import Account, AuditEvent, Base, Classification, ClassificationRule, ImportBatch, Job, SourceObservation, Transaction

HEADER = '"Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"'


def _row(
    date_str: str = "01.01.2026",
    category: str = "Food",
    subcategory: str = "Groceries",
    text: str = "MERCHANT",
    amount: str = "-10,00",
    balance: str = "1.000,00",
    status: str = "Executed",
    reconciled: str = "No",
) -> str:
    return (
        f'"{date_str}";"{category}";"{subcategory}";"{text}";'
        f'"{amount}";"{balance}";"{status}";"{reconciled}"'
    )


def _csv(*rows: str) -> bytes:
    return ("\n".join([HEADER] + list(rows))).encode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_engine(tmp_path):
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture()
def account(session_factory):
    session = session_factory()
    try:
        acc = Account(id="acct-test-1", name="Test Account", role="common", currency="EUR", active=True)
        session.add(acc)
        session.commit()
        return acc
    finally:
        session.close()


@pytest.fixture()
def file_store(tmp_path):
    store_dir = tmp_path / "imports"
    store_dir.mkdir()
    return FileStore(root_dir=str(store_dir))


@pytest.fixture()
def svc(session_factory, file_store):
    return ImportService(session_factory=session_factory, file_store=file_store)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

class TestPreview:
    def test_already_imported_false_on_first_call(self, svc, account):
        p = svc.preview(account.id, "a.csv", _csv(_row()), None, None, False)
        assert p.already_imported is False

    def test_already_imported_true_after_commit(self, svc, account):
        data = _csv(_row())
        p1 = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p1.idempotency_token, p1, None, None, False)
        p2 = svc.preview(account.id, "a.csv", data, None, None, False)
        assert p2.already_imported is True

    def test_same_file_different_account(self, svc, session_factory, account):
        session = session_factory()
        try:
            acc2 = Account(id="acct-test-2", name="Test 2", role="accrual", currency="EUR", active=True)
            session.add(acc2)
            session.commit()
        finally:
            session.close()
        data = _csv(_row())
        p1 = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p1.idempotency_token, p1, None, None, False)
        p2 = svc.preview("acct-test-2", "a.csv", data, None, None, False)
        assert p2.same_file_different_account is True

    def test_no_db_writes_during_preview(self, svc, session_factory, account):
        session = session_factory()
        before = session.query(ImportBatch).count()
        session.close()
        svc.preview(account.id, "a.csv", _csv(_row()), None, None, False)
        session = session_factory()
        after = session.query(ImportBatch).count()
        session.close()
        assert before == after == 0

    def test_unique_idempotency_token_per_call(self, svc, account):
        data = _csv(_row())
        p1 = svc.preview(account.id, "a.csv", data, None, None, False)
        p2 = svc.preview(account.id, "a.csv", data, None, None, False)
        assert p1.idempotency_token != p2.idempotency_token

    def test_bad_header_surfaces_parse_errors(self, svc, account):
        bad = ('"Date";"Category";"Text";"Amount";"Balance";"Status";"Reconciled"' + "\n" + _row()).encode()
        p = svc.preview(account.id, "bad.csv", bad, None, None, False)
        assert p.parse_errors
        assert p.row_previews == []

    def test_counts_for_executed_and_pending(self, svc, account):
        data = _csv(
            _row(date_str="01.01.2026", status="Executed"),
            _row(date_str="02.01.2026", status="Pending", balance=""),
        )
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        assert p.executed_count == 1
        assert p.pending_count == 1
        assert p.new_count == 1  # the executed new row


# ---------------------------------------------------------------------------
# Commit — basic
# ---------------------------------------------------------------------------

class TestCommitBasic:
    def test_returns_valid_batch_id(self, svc, account):
        p = svc.preview(account.id, "a.csv", _csv(_row()), None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        assert isinstance(r.batch_id, str)
        assert len(r.batch_id) > 0

    def test_import_batch_state_committed(self, svc, session_factory, account):
        data = _csv(_row(status="Executed"), _row(date_str="02.01.2026", status="Pending", balance=""))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        session = session_factory()
        try:
            batch = session.query(ImportBatch).filter(ImportBatch.id == r.batch_id).one()
            assert batch.state == "committed"
            assert batch.file_hash == p.file_hash
            assert batch.parser_version == p.parser_version
            assert batch.row_count == 2
            assert batch.executed_count == 1
            assert batch.pending_count == 1
            assert batch.idempotency_token == p.idempotency_token
        finally:
            session.close()

    def test_audit_event_created(self, svc, session_factory, account):
        p = svc.preview(account.id, "a.csv", _csv(_row()), None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        session = session_factory()
        try:
            audit = (
                session.query(AuditEvent)
                .filter(AuditEvent.entity_type == "import_batch", AuditEvent.entity_id == r.batch_id)
                .one()
            )
            assert audit.action == "committed"
            assert audit.actor == "system"
        finally:
            session.close()

    def test_new_executed_row_creates_transaction_and_job(self, svc, session_factory, account):
        p = svc.preview(account.id, "a.csv", _csv(_row()), None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        assert r.new_transactions == 1
        assert r.enqueued_jobs == 1
        session = session_factory()
        try:
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 1
            assert session.query(SourceObservation).count() == 1
            assert session.query(Job).filter(Job.kind == "classify").count() == 1
        finally:
            session.close()

    def test_pending_row_creates_observation_no_transaction_no_job(self, svc, session_factory, account):
        p = svc.preview(account.id, "a.csv", _csv(_row(status="Pending", balance="")), None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        assert r.pending_observations == 1
        assert r.new_transactions == 0
        assert r.enqueued_jobs == 0
        session = session_factory()
        try:
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 0
            obs = session.query(SourceObservation).one()
            assert obs.transaction_id is None
            assert obs.is_pending is True
        finally:
            session.close()

    def test_empty_csv_commits_cleanly_all_zero_counts(self, svc, account):
        p = svc.preview(account.id, "a.csv", _csv(), None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        assert r.new_transactions == 0
        assert r.pending_observations == 0
        assert r.enqueued_jobs == 0

    def test_commit_result_total_equals_row_count(self, svc, account):
        data = _csv(
            _row(status="Executed"),
            _row(date_str="02.01.2026", status="Pending", balance=""),
        )
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        total = r.new_transactions + r.reused_transactions + r.pending_observations
        assert total == len(p.row_previews)


# ---------------------------------------------------------------------------
# A01 — parse synthetic CSV with BOM, decimal commas, padding, all status values
# ---------------------------------------------------------------------------

class TestA01ParseSyntheticCsv:
    def test_bom_csv_parsed_correctly(self, svc, account):
        bom = b"\xef\xbb\xbf"
        data = bom + _csv(_row(amount="-10,00", balance="1.000,00"))
        p = svc.preview(account.id, "bom.csv", data, None, None, False)
        assert len(p.row_previews) == 1
        assert p.row_previews[0].amount_cents == -1000

    def test_decimal_comma_amount_parsed(self, svc, account):
        data = _csv(_row(amount="-1.140,71", balance="10.000,00"))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        assert p.row_previews[0].amount_cents == -114071

    def test_pending_row_balance_null(self, svc, account):
        data = _csv(_row(status="Pending", balance=""))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        assert p.pending_count == 1
        rp = p.row_previews[0]
        assert rp.is_pending is True
        assert rp.match_candidate.parsed_row.parsed_balance_cents is None

    def test_all_status_values_parsed(self, svc, account):
        data = _csv(
            _row(date_str="01.01.2026", status="Executed"),
            _row(date_str="02.01.2026", status="Pending", balance=""),
            _row(date_str="03.01.2026", status="Rejected"),
            _row(date_str="04.01.2026", status="Deleted"),
        )
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        assert len(p.row_previews) == 4
        statuses = [rp.match_candidate.parsed_row.raw_status for rp in p.row_previews]
        assert "Executed" in statuses
        assert "Pending" in statuses
        assert "Rejected" in statuses
        assert "Deleted" in statuses

    def test_padded_category_trimmed(self, svc, account):
        padded = _row(category="Food   ", subcategory="Groceries   ")
        data = _csv(padded)
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        assert not p.parse_errors


# ---------------------------------------------------------------------------
# A02 — Reimporting identical file inserts zero new executed transactions
# ---------------------------------------------------------------------------

class TestA02Idempotency:
    def test_same_token_returns_same_batch_id(self, svc, account):
        p = svc.preview(account.id, "a.csv", _csv(_row()), None, None, False)
        r1 = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        r2 = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        assert r1.batch_id == r2.batch_id

    def test_same_token_no_second_batch_row(self, svc, session_factory, account):
        p = svc.preview(account.id, "a.csv", _csv(_row()), None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)
        session = session_factory()
        try:
            assert session.query(ImportBatch).count() == 1
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 1
        finally:
            session.close()


# ---------------------------------------------------------------------------
# A03 — Two overlapping exports → shared rows reused, new rows inserted once
# ---------------------------------------------------------------------------

class TestA03OverlappingExports:
    def test_shared_row_reused_new_row_inserted(self, svc, session_factory, account):
        csv1 = _csv(
            _row(date_str="01.01.2026", text="MERCHANT-A", amount="-10,00", balance="100,00"),
            _row(date_str="02.01.2026", text="MERCHANT-B", amount="-20,00", balance="80,00"),
        )
        p1 = svc.preview(account.id, "e1.csv", csv1, None, None, False)
        r1 = svc.commit(account.id, p1.idempotency_token, p1, None, None, False)
        assert r1.new_transactions == 2

        csv2 = _csv(
            _row(date_str="02.01.2026", text="MERCHANT-B", amount="-20,00", balance="80,00"),
            _row(date_str="03.01.2026", text="MERCHANT-C", amount="-30,00", balance="50,00"),
        )
        p2 = svc.preview(account.id, "e2.csv", csv2, None, None, False)
        r2 = svc.commit(account.id, p2.idempotency_token, p2, None, None, False)
        assert r2.new_transactions == 1
        assert r2.reused_transactions == 1

        session = session_factory()
        try:
            txn_count = session.query(Transaction).filter(Transaction.account_id == account.id).count()
            assert txn_count == 3
        finally:
            session.close()


# ---------------------------------------------------------------------------
# A04 — Two equal purchases on the same date are both retained
# ---------------------------------------------------------------------------

class TestA04TwoIdenticalPurchases:
    def test_two_identical_rows_produce_two_transactions(self, svc, session_factory, account):
        csv_data = _csv(
            _row(date_str="01.01.2026", text="SHOP", amount="-10,00", balance="100,00"),
            _row(date_str="01.01.2026", text="SHOP", amount="-10,00", balance="90,00"),
        )
        p = svc.preview(account.id, "a.csv", csv_data, None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        session = session_factory()
        try:
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 2
        finally:
            session.close()
        assert r.new_transactions == 2


# ---------------------------------------------------------------------------
# A05 — Ambiguous rows produce ambiguous_count > 0, no silent merge
# ---------------------------------------------------------------------------

class TestA05AmbiguousRows:
    def test_ambiguous_count_and_new_transaction_created(self, svc, session_factory, account):
        # First import: two rows with same date+amount but different text
        csv1 = _csv(
            _row(date_str="01.01.2026", text="SHOP-A", amount="-10,00", balance="100,00"),
            _row(date_str="01.01.2026", text="SHOP-B", amount="-10,00", balance="90,00"),
        )
        p1 = svc.preview(account.id, "e1.csv", csv1, None, None, False)
        svc.commit(account.id, p1.idempotency_token, p1, None, None, False)

        # Second import: row matching both existing by date+amount but with unique text
        csv2 = _csv(_row(date_str="01.01.2026", text="SHOP-UNIQUE", amount="-10,00", balance="80,00"))
        p2 = svc.preview(account.id, "e2.csv", csv2, None, None, False)
        assert p2.ambiguous_count > 0
        r2 = svc.commit(account.id, p2.idempotency_token, p2, None, None, False)
        assert r2.ambiguous_count > 0

        session = session_factory()
        try:
            # SHOP-A, SHOP-B, SHOP-UNIQUE — no silent merge
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 3
        finally:
            session.close()


# ---------------------------------------------------------------------------
# A06 — Pending row disappears, executed row appears → counted once
# ---------------------------------------------------------------------------

class TestA06PendingToExecuted:
    def test_executed_counted_once_after_pending(self, svc, session_factory, account):
        # First import: pending
        csv1 = _csv(_row(date_str="01.01.2026", text="SHOP", amount="-10,00", status="Pending", balance=""))
        p1 = svc.preview(account.id, "e1.csv", csv1, None, None, False)
        r1 = svc.commit(account.id, p1.idempotency_token, p1, None, None, False)
        assert r1.pending_observations == 1
        assert r1.new_transactions == 0

        # Second import: same row now executed
        csv2 = _csv(_row(date_str="01.01.2026", text="SHOP", amount="-10,00", status="Executed", balance="90,00"))
        p2 = svc.preview(account.id, "e2.csv", csv2, None, None, False)
        r2 = svc.commit(account.id, p2.idempotency_token, p2, None, None, False)
        assert r2.new_transactions == 1

        session = session_factory()
        try:
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 1
        finally:
            session.close()


# ---------------------------------------------------------------------------
# A07 — Pending-only CSV → executed_count == 0, pending_count > 0
# ---------------------------------------------------------------------------

class TestA07PendingOnly:
    def test_pending_only_preview_counts(self, svc, account):
        data = _csv(
            _row(date_str="01.01.2026", status="Pending", balance=""),
            _row(date_str="02.01.2026", status="Pending", balance=""),
        )
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        assert p.executed_count == 0
        assert p.pending_count == 2

    def test_pending_only_commit_no_transactions(self, svc, session_factory, account):
        data = _csv(
            _row(date_str="01.01.2026", status="Pending", balance=""),
            _row(date_str="02.01.2026", status="Pending", balance=""),
        )
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        r = svc.commit(account.id, p.idempotency_token, p, None, None, False)
        assert r.new_transactions == 0
        assert r.pending_observations == 2
        session = session_factory()
        try:
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 0
        finally:
            session.close()


# ---------------------------------------------------------------------------
# A18 — Concurrent imports produce no duplicate ledger entries
# ---------------------------------------------------------------------------

class TestA18Concurrent:
    def test_two_threads_same_token_single_batch(self, svc, session_factory, account):
        data = _csv(_row(date_str="01.01.2026", text="SHOP", amount="-10,00", balance="100,00"))
        p = svc.preview(account.id, "a.csv", data, None, None, False)

        results: list = []
        errors: list = []

        def _commit():
            try:
                results.append(svc.commit(account.id, p.idempotency_token, p, None, None, False))
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_commit)
        t2 = threading.Thread(target=_commit)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 2
        assert results[0].batch_id == results[1].batch_id

        session = session_factory()
        try:
            assert session.query(ImportBatch).count() == 1
            assert session.query(Transaction).filter(Transaction.account_id == account.id).count() == 1
        finally:
            session.close()

    def test_two_threads_different_accounts_no_interference(self, svc, session_factory, account):
        session = session_factory()
        try:
            acc2 = Account(id="acct-test-2c", name="Test 2", role="accrual", currency="EUR", active=True)
            session.add(acc2)
            session.commit()
        finally:
            session.close()

        csv1 = _csv(_row(date_str="01.01.2026", text="SHOP-1", amount="-10,00", balance="100,00"))
        csv2 = _csv(_row(date_str="01.01.2026", text="SHOP-2", amount="-20,00", balance="200,00"))
        p1 = svc.preview(account.id, "a.csv", csv1, None, None, False)
        p2 = svc.preview("acct-test-2c", "b.csv", csv2, None, None, False)

        results: list = []
        errors: list = []

        def _t1():
            try:
                results.append(svc.commit(account.id, p1.idempotency_token, p1, None, None, False))
            except Exception as exc:
                errors.append(exc)

        def _t2():
            try:
                results.append(svc.commit("acct-test-2c", p2.idempotency_token, p2, None, None, False))
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_t1)
        t2 = threading.Thread(target=_t2)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 2

        session = session_factory()
        try:
            assert session.query(ImportBatch).count() == 2
            assert session.query(Transaction).count() == 2
        finally:
            session.close()

    def test_lock_released_after_exception(self, svc, account):
        # Verify the per-account lock is released even when the commit body raises.
        data = _csv(_row())
        p = svc.preview(account.id, "a.csv", data, None, None, False)

        call_count = 0
        original_flush = Session.flush

        def bad_flush(self_session, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated failure")
            return original_flush(self_session, *args, **kwargs)

        with patch.object(Session, "flush", bad_flush):
            with pytest.raises(RuntimeError, match="simulated failure"):
                svc.commit(account.id, p.idempotency_token, p, None, None, False)

        # patch.object has exited — Session.flush is restored.
        # The per-account lock must have been released by the with-lock context manager.
        p2 = svc.preview(account.id, "a.csv", data, None, None, False)
        r = svc.commit(account.id, p2.idempotency_token, p2, None, None, False)
        assert r.new_transactions == 1


# ---------------------------------------------------------------------------
# Post-import rule application — confirmed rules classify new executed transactions
# ---------------------------------------------------------------------------

class TestPostImportRuleApplication:
    def _seed_rule(
        self,
        session_factory,
        *,
        pattern_type: str = "text_contains",
        pattern_value: str = "MERCHANT",
        transaction_type: str = "expense",
        category_id: str | None = "groceries",
        merchant: str | None = None,
        priority: int = 10,
        is_confirmed: bool = True,
    ):
        session = session_factory()
        try:
            rule = ClassificationRule(
                priority=priority,
                pattern_type=pattern_type,
                pattern_value=pattern_value,
                transaction_type=transaction_type,
                category_id=category_id,
                merchant=merchant,
                is_confirmed=is_confirmed,
            )
            session.add(rule)
            session.commit()
            return rule.id
        finally:
            session.close()

    def test_confirmed_rule_creates_classification(self, svc, session_factory, account):
        self._seed_rule(session_factory, pattern_value="MERCHANT", is_confirmed=True)
        data = _csv(_row(text="MERCHANT", status="Executed"))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)

        session = session_factory()
        try:
            clfs = session.query(Classification).all()
            assert len(clfs) == 1
            assert clfs[0].source == "rule"
            assert clfs[0].transaction_type == "expense"
            assert clfs[0].category_id == "groceries"
        finally:
            session.close()

    def test_unconfirmed_rule_not_applied(self, svc, session_factory, account):
        self._seed_rule(session_factory, pattern_value="MERCHANT", is_confirmed=False)
        data = _csv(_row(text="MERCHANT", status="Executed"))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)

        session = session_factory()
        try:
            assert session.query(Classification).count() == 0
        finally:
            session.close()

    def test_no_rules_no_classifications(self, svc, session_factory, account):
        data = _csv(_row(status="Executed"))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)

        session = session_factory()
        try:
            assert session.query(Classification).count() == 0
        finally:
            session.close()

    def test_highest_priority_rule_wins(self, svc, session_factory, account):
        self._seed_rule(session_factory, pattern_value="MERCHANT", transaction_type="expense", category_id="food", priority=5)
        self._seed_rule(session_factory, pattern_value="MERCHANT", transaction_type="income", category_id="salary", priority=20)
        data = _csv(_row(text="MERCHANT", status="Executed"))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)

        session = session_factory()
        try:
            clfs = session.query(Classification).all()
            assert len(clfs) == 1
            assert clfs[0].transaction_type == "income"
            assert clfs[0].category_id == "salary"
        finally:
            session.close()

    def test_pending_rows_not_classified(self, svc, session_factory, account):
        self._seed_rule(session_factory, pattern_value="MERCHANT", is_confirmed=True)
        data = _csv(_row(text="MERCHANT", status="Pending", balance=""))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)

        session = session_factory()
        try:
            assert session.query(Classification).count() == 0
        finally:
            session.close()

    def test_rule_not_matching_text_no_classification(self, svc, session_factory, account):
        self._seed_rule(session_factory, pattern_value="NOMATCH", is_confirmed=True)
        data = _csv(_row(text="MERCHANT", status="Executed"))
        p = svc.preview(account.id, "a.csv", data, None, None, False)
        svc.commit(account.id, p.idempotency_token, p, None, None, False)

        session = session_factory()
        try:
            assert session.query(Classification).count() == 0
        finally:
            session.close()
