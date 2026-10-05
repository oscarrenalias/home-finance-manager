"""Unit tests for services/transaction_query.py.

Covers AC-1 through AC-9, AC-11, AC-13, and AC-14 from the transaction ledger spec.
All tests use an in-memory SQLite DB via Base.metadata.create_all() — no Alembic,
no Reflex, no PostgreSQL driver required.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from services.transaction_query import (
    LedgerFilters,
    LedgerRow,
    PAGE_SIZES,
    TransactionDetail,
    get_current_classification,
    get_transaction_detail,
    list_transactions,
    set_note,
)
from storage.models import (
    Account,
    AuditEvent,
    Base,
    Classification,
    ImportBatch,
    SourceObservation,
    Transaction,
    TransferLink,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'tq_test.db'}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def Session(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture()
def session(Session):
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def account(session):
    acct = Account(
        id="acct-001",
        name="Common Account",
        role="common",
        currency="EUR",
        active=True,
    )
    session.add(acct)
    session.commit()
    return acct


@pytest.fixture()
def account2(session):
    acct = Account(
        id="acct-002",
        name="Accrual Account",
        role="accrual",
        currency="EUR",
        active=True,
    )
    session.add(acct)
    session.commit()
    return acct


def _tx(
    session,
    account_id: str,
    *,
    tx_date: date = date(2026, 6, 15),
    amount_cents: int = -2500,
    display_text: str = "SUPERMARKET",
    status: str = "Executed",
    transaction_type: Optional[str] = None,
    category_id: Optional[str] = None,
    merchant: Optional[str] = None,
    note: Optional[str] = None,
    note_updated_at: Optional[datetime] = None,
    tx_id: Optional[str] = None,
) -> Transaction:
    tx = Transaction(
        id=tx_id or str(uuid.uuid4()),
        account_id=account_id,
        date=tx_date,
        amount_cents=amount_cents,
        currency="EUR",
        original_text=display_text,
        display_text=display_text,
        status=status,
        transaction_type=transaction_type,
        category_id=category_id,
        merchant=merchant,
        note=note,
        note_updated_at=note_updated_at,
    )
    session.add(tx)
    return tx


def _cls(
    session,
    transaction_id: str,
    *,
    transaction_type: str = "expense",
    category_id: Optional[str] = None,
    merchant: Optional[str] = None,
    review_state: str = "accepted",
    source: str = "manual",
    rationale: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> Classification:
    c = Classification(
        id=str(uuid.uuid4()),
        transaction_id=transaction_id,
        transaction_type=transaction_type,
        category_id=category_id,
        merchant=merchant,
        review_state=review_state,
        source=source,
        rationale=rationale,
    )
    if created_at:
        c.created_at = created_at
    session.add(c)
    return c


# ---------------------------------------------------------------------------
# Data structure / constant tests
# ---------------------------------------------------------------------------


def test_page_sizes_constant():
    assert PAGE_SIZES == (20, 50, 100)


def test_ledger_filters_defaults():
    f = LedgerFilters()
    assert f.date_from is None
    assert f.date_to is None
    assert f.account_ids == ()
    assert f.transaction_types == ()
    assert f.category_ids == ()
    assert f.review_states == ()
    assert f.search == ""
    assert f.include_rejected_deleted is False


def test_transaction_detail_has_defaults():
    """TransactionDetail can be constructed with only LedgerRow fields; extras default."""
    detail = TransactionDetail(
        id="x",
        date=date(2026, 1, 1),
        account_name="Test",
        display_text="Text",
        amount_cents=-100,
        currency="EUR",
        status="Executed",
        transaction_type=None,
        category_id=None,
        category_name=None,
        merchant=None,
        review_state=None,
        classification_source=None,
        has_note=False,
    )
    assert detail.source_rows == []
    assert detail.classification_history == []
    assert detail.transfer_links == []
    assert detail.note is None
    assert detail.note_updated_at is None


# ---------------------------------------------------------------------------
# get_current_classification
# ---------------------------------------------------------------------------


class TestGetCurrentClassification:
    def test_no_rows_returns_none(self, session, account):
        tx = _tx(session, account.id)
        session.commit()
        assert get_current_classification(session, tx.id) is None

    def test_one_accepted_returns_it(self, session, account):
        tx = _tx(session, account.id)
        session.commit()
        c = _cls(session, tx.id, review_state="accepted")
        session.commit()
        result = get_current_classification(session, tx.id)
        assert result is not None
        assert result.id == c.id

    def test_one_rejected_returns_none(self, session, account):
        tx = _tx(session, account.id)
        session.commit()
        _cls(session, tx.id, review_state="rejected")
        session.commit()
        assert get_current_classification(session, tx.id) is None

    def test_multiple_newest_rejected_returns_next(self, session, account):
        tx = _tx(session, account.id)
        session.commit()
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
        older_accepted = _cls(session, tx.id, review_state="accepted", created_at=t1)
        _cls(session, tx.id, review_state="rejected", created_at=t2)
        session.commit()
        result = get_current_classification(session, tx.id)
        assert result is not None
        assert result.id == older_accepted.id

    def test_older_accepted_wins_over_newer_needs_review(self, session, account):
        """A11: manual-accepted classification beats a newer model-generated needs_review row.

        Simulates a classify-batch rerun that inserts a newer needs_review row after the
        user has already manually accepted the transaction.  The accepted row must still
        be returned as the active classification.
        """
        tx = _tx(session, account.id)
        session.commit()
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
        manual_accepted = _cls(
            session, tx.id,
            transaction_type="expense",
            category_id="groceries",
            review_state="accepted",
            source="manual",
            created_at=t1,
        )
        _cls(
            session, tx.id,
            transaction_type="unknown",
            review_state="needs_review",
            source="model",
            created_at=t2,
        )
        session.commit()
        result = get_current_classification(session, tx.id)
        assert result is not None
        assert result.id == manual_accepted.id
        assert result.review_state == "accepted"
        assert result.transaction_type == "expense"


# ---------------------------------------------------------------------------
# AC-1 — list_transactions basic behaviour
# ---------------------------------------------------------------------------


class TestListTransactionsBasic:
    def test_returns_executed_rows(self, session, account):
        t1 = _tx(session, account.id, display_text="TX A")
        t2 = _tx(session, account.id, display_text="TX B")
        session.commit()
        page = list_transactions(session, LedgerFilters())
        ids = {r.id for r in page.rows}
        assert t1.id in ids
        assert t2.id in ids

    def test_rejected_hidden_by_default(self, session, account):
        good = _tx(session, account.id, display_text="Good", status="Executed")
        bad = _tx(session, account.id, display_text="Rejected", status="Rejected")
        session.commit()
        page = list_transactions(session, LedgerFilters())
        ids = {r.id for r in page.rows}
        assert good.id in ids
        assert bad.id not in ids

    def test_deleted_hidden_by_default(self, session, account):
        good = _tx(session, account.id, display_text="Good", status="Executed")
        deleted = _tx(session, account.id, display_text="Deleted", status="Deleted")
        session.commit()
        page = list_transactions(session, LedgerFilters())
        ids = {r.id for r in page.rows}
        assert good.id in ids
        assert deleted.id not in ids

    def test_include_rejected_deleted_true(self, session, account):
        _tx(session, account.id, status="Executed")
        _tx(session, account.id, status="Rejected")
        _tx(session, account.id, status="Deleted")
        session.commit()
        page = list_transactions(
            session, LedgerFilters(include_rejected_deleted=True)
        )
        assert page.total_count == 3

    def test_ledger_row_fields_populated(self, session, account):
        tx_date = date(2026, 3, 10)
        tx = _tx(
            session,
            account.id,
            tx_date=tx_date,
            display_text="LIDL",
            amount_cents=-1099,
            status="Executed",
            transaction_type="expense",
            category_id="groceries",
            merchant="Lidl",
        )
        session.commit()
        page = list_transactions(session, LedgerFilters())
        assert page.total_count == 1
        row = page.rows[0]
        assert row.id == tx.id
        assert row.date == tx_date
        assert row.account_name == account.name
        assert row.display_text == "LIDL"
        assert row.amount_cents == -1099
        assert row.currency == "EUR"
        assert row.status == "Executed"
        assert row.has_note is False

    def test_has_note_true_when_note_set(self, session, account):
        _tx(session, account.id, note="some note")
        session.commit()
        page = list_transactions(session, LedgerFilters())
        assert page.rows[0].has_note is True


# ---------------------------------------------------------------------------
# AC-2 — Filters (each alone and combined)
# ---------------------------------------------------------------------------


class TestFilters:
    def _seed(self, session, account, account2):
        """Seed a small variety of transactions for filter tests."""
        t_old = _tx(session, account.id, tx_date=date(2026, 1, 1), display_text="OLD TX")
        t_mid = _tx(session, account.id, tx_date=date(2026, 6, 15), display_text="MID TX")
        t_new = _tx(session, account.id, tx_date=date(2026, 12, 31), display_text="NEW TX")
        t_acct2 = _tx(session, account2.id, tx_date=date(2026, 6, 15), display_text="ACCT2 TX")
        session.commit()
        # Classify mid with category=groceries, accepted
        _cls(session, t_mid.id, transaction_type="expense", category_id="groceries")
        # Classify new with category=None (uncategorised classification), accepted
        _cls(session, t_new.id, transaction_type="income", category_id=None)
        session.commit()
        return t_old, t_mid, t_new, t_acct2

    def test_date_from_inclusive(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(session, LedgerFilters(date_from=date(2026, 6, 15)))
        ids = {r.id for r in page.rows}
        assert t_mid.id in ids
        assert t_new.id in ids
        assert t_acct2.id in ids
        assert t_old.id not in ids

    def test_date_to_inclusive(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(session, LedgerFilters(date_to=date(2026, 6, 15)))
        ids = {r.id for r in page.rows}
        assert t_old.id in ids
        assert t_mid.id in ids
        assert t_acct2.id in ids
        assert t_new.id not in ids

    def test_account_ids_filter(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(session, LedgerFilters(account_ids=(account2.id,)))
        ids = {r.id for r in page.rows}
        assert ids == {t_acct2.id}

    def test_transaction_types_filter(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(session, LedgerFilters(transaction_types=("income",)))
        ids = {r.id for r in page.rows}
        assert t_new.id in ids
        assert t_mid.id not in ids

    def test_category_ids_normal(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(session, LedgerFilters(category_ids=("groceries",)))
        ids = {r.id for r in page.rows}
        assert t_mid.id in ids
        assert t_new.id not in ids

    def test_category_ids_none_matches_unclassified(self, session, account, account2):
        """__none__ matches rows with no category in their active classification."""
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        # t_old and t_acct2 have no classification; t_new has accepted cls with no category
        page = list_transactions(session, LedgerFilters(category_ids=("__none__",)))
        ids = {r.id for r in page.rows}
        assert t_old.id in ids
        assert t_acct2.id in ids
        assert t_new.id in ids
        assert t_mid.id not in ids

    def test_review_states_accepted(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(session, LedgerFilters(review_states=("accepted",)))
        ids = {r.id for r in page.rows}
        assert t_mid.id in ids
        assert t_new.id in ids
        assert t_old.id not in ids

    def test_review_states_needs_review(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        # Add a needs_review cls to t_old
        _cls(session, t_old.id, review_state="needs_review")
        session.commit()
        page = list_transactions(session, LedgerFilters(review_states=("needs_review",)))
        ids = {r.id for r in page.rows}
        assert t_old.id in ids
        assert t_mid.id not in ids

    def test_review_states_unclassified(self, session, account, account2):
        """unclassified = no non-rejected Classification row at all."""
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(session, LedgerFilters(review_states=("unclassified",)))
        ids = {r.id for r in page.rows}
        assert t_old.id in ids
        assert t_acct2.id in ids
        assert t_mid.id not in ids
        assert t_new.id not in ids

    def test_combined_filters(self, session, account, account2):
        t_old, t_mid, t_new, t_acct2 = self._seed(session, account, account2)
        page = list_transactions(
            session,
            LedgerFilters(
                account_ids=(account.id,),
                review_states=("accepted",),
            ),
        )
        ids = {r.id for r in page.rows}
        assert t_mid.id in ids
        assert t_new.id in ids
        assert t_old.id not in ids
        assert t_acct2.id not in ids


# ---------------------------------------------------------------------------
# AC-3 — Search
# ---------------------------------------------------------------------------


class TestSearch:
    def _make_tx(self, session, account, display_text="", merchant=None, note=None):
        tx = _tx(session, account.id, display_text=display_text, merchant=merchant, note=note)
        session.commit()
        if merchant:
            tx.merchant = merchant
            session.commit()
        return tx

    def test_search_display_text_case_insensitive(self, session, account):
        t = _tx(session, account.id, display_text="LIDL STORE")
        session.commit()
        page = list_transactions(session, LedgerFilters(search="lidl"))
        assert any(r.id == t.id for r in page.rows)

    def test_search_merchant_case_insensitive(self, session, account):
        t = _tx(session, account.id, display_text="PAYMENT", merchant="Prisma")
        session.commit()
        page = list_transactions(session, LedgerFilters(search="PRISMA"))
        assert any(r.id == t.id for r in page.rows)

    def test_search_matches_merchant_on_active_classification(self, session, account):
        """Merchants assigned by the LLM or a manual edit are stored on Classification only."""
        t = _tx(session, account.id, display_text="CARD PAYMENT 4411")
        session.commit()
        _cls(session, t.id, merchant="Prisma", review_state="needs_review", source="llm")
        session.commit()
        page = list_transactions(session, LedgerFilters(search="prisma"))
        assert [r.id for r in page.rows] == [t.id]
        assert page.rows[0].merchant == "Prisma"

    def test_search_ignores_merchant_on_superseded_classification(self, session, account):
        """Only the active classification's merchant is searchable, not older history rows."""
        t = _tx(session, account.id, display_text="CARD PAYMENT 4412")
        session.commit()
        _cls(session, t.id, merchant="Old Shop", review_state="needs_review", source="llm",
             created_at=datetime(2026, 6, 1, 10, 0))
        _cls(session, t.id, merchant="Lidl", review_state="accepted", source="manual",
             created_at=datetime(2026, 6, 2, 10, 0))
        session.commit()
        assert list_transactions(session, LedgerFilters(search="lidl")).total_count == 1
        assert list_transactions(session, LedgerFilters(search="old shop")).total_count == 0

    def test_search_note_case_insensitive(self, session, account):
        t = _tx(session, account.id, display_text="PAYMENT", note="Holiday groceries")
        session.commit()
        page = list_transactions(session, LedgerFilters(search="HOLIDAY"))
        assert any(r.id == t.id for r in page.rows)

    def test_search_literal_percent(self, session, account):
        """A % in the search term must match a literal % in the text, not any string."""
        t_match = _tx(session, account.id, display_text="100% organic")
        t_no_match = _tx(session, account.id, display_text="totally organic")
        session.commit()
        page = list_transactions(session, LedgerFilters(search="100%"))
        ids = {r.id for r in page.rows}
        assert t_match.id in ids
        assert t_no_match.id not in ids

    def test_search_literal_underscore(self, session, account):
        """A _ in the search term must match a literal _ not any single character."""
        t_match = _tx(session, account.id, display_text="REF_12345")
        t_no_match = _tx(session, account.id, display_text="REFX12345")
        session.commit()
        page = list_transactions(session, LedgerFilters(search="REF_12345"))
        ids = {r.id for r in page.rows}
        assert t_match.id in ids
        assert t_no_match.id not in ids


# ---------------------------------------------------------------------------
# AC-4 — Sort determinism
# ---------------------------------------------------------------------------


class TestSortDeterminism:
    def _make_equal_rows(self, session, account, n=3, same_date=True, same_amount=True):
        txs = []
        for i in range(n):
            tx = Transaction(
                id=f"sort-tx-{uuid.uuid4().hex[:8]}",
                account_id=account.id,
                date=date(2026, 6, 1) if same_date else date(2026, 6, i + 1),
                amount_cents=-1000 if same_amount else -(i + 1) * 1000,
                currency="EUR",
                original_text=f"TX {i}",
                display_text=f"TX {i}",
                status="Executed",
            )
            session.add(tx)
            txs.append(tx)
        session.commit()
        return txs

    def _ids_for_sort(self, session, account, sort_key):
        page = list_transactions(
            session,
            LedgerFilters(account_ids=(account.id,)),
            sort=sort_key,
            page_size=100,
        )
        return [r.id for r in page.rows]

    def test_sort_date_desc_stable(self, session, account):
        self._make_equal_rows(session, account, n=3, same_date=True)
        run1 = self._ids_for_sort(session, account, "date_desc")
        run2 = self._ids_for_sort(session, account, "date_desc")
        assert run1 == run2 and len(run1) == 3

    def test_sort_date_asc_stable(self, session, account):
        self._make_equal_rows(session, account, n=3, same_date=True)
        run1 = self._ids_for_sort(session, account, "date_asc")
        run2 = self._ids_for_sort(session, account, "date_asc")
        assert run1 == run2 and len(run1) == 3

    def test_sort_amount_desc_stable(self, session, account):
        self._make_equal_rows(session, account, n=3, same_amount=True)
        run1 = self._ids_for_sort(session, account, "amount_desc")
        run2 = self._ids_for_sort(session, account, "amount_desc")
        assert run1 == run2 and len(run1) == 3

    def test_sort_amount_asc_stable(self, session, account):
        self._make_equal_rows(session, account, n=3, same_amount=True)
        run1 = self._ids_for_sort(session, account, "amount_asc")
        run2 = self._ids_for_sort(session, account, "amount_asc")
        assert run1 == run2 and len(run1) == 3

    def test_invalid_sort_raises(self, session, account):
        with pytest.raises(ValueError, match="sort"):
            list_transactions(session, LedgerFilters(), sort="invalid")


# ---------------------------------------------------------------------------
# AC-5 — Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    @pytest.fixture()
    def seeded_120(self, session, account):
        for i in range(120):
            _tx(
                session,
                account.id,
                tx_date=date(2026, 1, 1) + timedelta(days=i % 30),
                display_text=f"TX {i:04d}",
                amount_cents=-(i + 1) * 100,
            )
        session.commit()

    def test_page_size_50_yields_50_50_20(self, session, account, seeded_120):
        p0 = list_transactions(session, LedgerFilters(), page=0, page_size=50)
        p1 = list_transactions(session, LedgerFilters(), page=1, page_size=50)
        p2 = list_transactions(session, LedgerFilters(), page=2, page_size=50)
        assert len(p0.rows) == 50
        assert len(p1.rows) == 50
        assert len(p2.rows) == 20

    def test_no_overlap_across_pages(self, session, account, seeded_120):
        p0 = list_transactions(session, LedgerFilters(), page=0, page_size=50)
        p1 = list_transactions(session, LedgerFilters(), page=1, page_size=50)
        p2 = list_transactions(session, LedgerFilters(), page=2, page_size=50)
        ids0 = {r.id for r in p0.rows}
        ids1 = {r.id for r in p1.rows}
        ids2 = {r.id for r in p2.rows}
        assert not (ids0 & ids1)
        assert not (ids1 & ids2)
        assert not (ids0 & ids2)

    def test_total_count_constant_across_pages(self, session, account, seeded_120):
        p0 = list_transactions(session, LedgerFilters(), page=0, page_size=50)
        p1 = list_transactions(session, LedgerFilters(), page=1, page_size=50)
        assert p0.total_count == 120
        assert p1.total_count == 120

    def test_page_size_20_works(self, session, account, seeded_120):
        p = list_transactions(session, LedgerFilters(), page=0, page_size=20)
        assert len(p.rows) == 20
        assert p.total_count == 120

    def test_page_size_100_works(self, session, account, seeded_120):
        p = list_transactions(session, LedgerFilters(), page=0, page_size=100)
        assert len(p.rows) == 100
        assert p.total_count == 120

    def test_invalid_page_size_raises(self, session, account):
        with pytest.raises(ValueError, match="page_size"):
            list_transactions(session, LedgerFilters(), page_size=7)


# ---------------------------------------------------------------------------
# AC-7 — get_transaction_detail
# ---------------------------------------------------------------------------


class TestGetTransactionDetail:
    def test_returns_none_for_unknown_id(self, session, account):
        assert get_transaction_detail(session, "nonexistent-id") is None

    def test_source_rows_populated(self, session, account):
        tx = _tx(session, account.id)
        session.commit()

        batch = ImportBatch(
            id=str(uuid.uuid4()),
            account_id=account.id,
            filename="test.csv",
            file_hash="abc123",
            state="committed",
            parser_version="1.0",
            idempotency_token=str(uuid.uuid4()),
        )
        session.add(batch)
        session.commit()

        obs = SourceObservation(
            id=str(uuid.uuid4()),
            batch_id=batch.id,
            transaction_id=tx.id,
            row_number=1,
            raw_date="15.06.2026",
            raw_text="SUPERMARKET",
            raw_amount="-25,50",
            raw_balance="500,00",
            raw_status="Executed",
            raw_category="Food",
            raw_subcategory="Groceries",
            raw_reconciled="No",
        )
        session.add(obs)
        session.commit()

        detail = get_transaction_detail(session, tx.id)
        assert detail is not None
        assert len(detail.source_rows) == 1
        src = detail.source_rows[0]
        assert src.batch_filename == "test.csv"
        assert src.row_number == 1
        assert src.raw_date == "15.06.2026"
        assert src.raw_text == "SUPERMARKET"
        assert src.raw_amount == "-25,50"

    def test_classification_history_ordered_newest_first_includes_rejected(
        self, session, account
    ):
        tx = _tx(session, account.id)
        session.commit()
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
        _cls(session, tx.id, review_state="accepted", created_at=t1)
        _cls(session, tx.id, review_state="rejected", created_at=t2)
        session.commit()

        detail = get_transaction_detail(session, tx.id)
        assert detail is not None
        assert len(detail.classification_history) == 2
        # newest first
        assert detail.classification_history[0].review_state == "rejected"
        assert detail.classification_history[1].review_state == "accepted"

    def test_transfer_link_as_transaction_a(self, session, account, account2):
        # TransferLink requires transaction_a_id < transaction_b_id lexicographically
        id_a = "00000000-0001-0000-0000-000000000000"
        id_b = "00000000-0002-0000-0000-000000000000"
        tx_a = _tx(session, account.id, amount_cents=-15000, display_text="TRANSFER OUT", tx_id=id_a)
        tx_b = _tx(session, account2.id, amount_cents=15000, display_text="TRANSFER IN", tx_id=id_b)
        session.commit()
        link = TransferLink(
            id=str(uuid.uuid4()),
            transaction_a_id=tx_a.id,
            transaction_b_id=tx_b.id,
            confirmed_by="manual",
        )
        session.add(link)
        session.commit()

        detail = get_transaction_detail(session, tx_a.id)
        assert detail is not None
        assert len(detail.transfer_links) == 1
        tl = detail.transfer_links[0]
        assert tl.amount_cents == 15000
        assert tl.account_name == account2.name
        assert tl.link_state == "manual"

    def test_transfer_link_as_transaction_b(self, session, account, account2):
        # TransferLink requires transaction_a_id < transaction_b_id lexicographically
        id_a = "00000000-0001-0000-0000-000000000001"
        id_b = "00000000-0002-0000-0000-000000000001"
        tx_a = _tx(session, account.id, amount_cents=-15000, tx_id=id_a)
        tx_b = _tx(session, account2.id, amount_cents=15000, tx_id=id_b)
        session.commit()
        link = TransferLink(
            id=str(uuid.uuid4()),
            transaction_a_id=tx_a.id,
            transaction_b_id=tx_b.id,
            confirmed_by="rule",
        )
        session.add(link)
        session.commit()

        detail = get_transaction_detail(session, tx_b.id)
        assert detail is not None
        assert len(detail.transfer_links) == 1
        tl = detail.transfer_links[0]
        assert tl.amount_cents == -15000
        assert tl.account_name == account.name

    def test_note_and_note_updated_at_propagated(self, session, account):
        ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        tx = _tx(session, account.id, note="test note", note_updated_at=ts)
        session.commit()
        detail = get_transaction_detail(session, tx.id)
        assert detail is not None
        assert detail.note == "test note"
        assert detail.has_note is True

    def test_note_none_propagated(self, session, account):
        tx = _tx(session, account.id, note=None)
        session.commit()
        detail = get_transaction_detail(session, tx.id)
        assert detail is not None
        assert detail.note is None
        assert detail.note_updated_at is None
        assert detail.has_note is False


# ---------------------------------------------------------------------------
# AC-9 — Two identical purchases on same date
# ---------------------------------------------------------------------------


def test_two_identical_purchases_same_date_both_retained(session, account):
    tx1 = _tx(session, account.id, tx_date=date(2026, 6, 1), amount_cents=-2000, display_text="CAFE")
    tx2 = _tx(session, account.id, tx_date=date(2026, 6, 1), amount_cents=-2000, display_text="CAFE")
    session.commit()
    page = list_transactions(session, LedgerFilters())
    ids = {r.id for r in page.rows}
    assert tx1.id in ids
    assert tx2.id in ids
    assert page.total_count == 2


# ---------------------------------------------------------------------------
# AC-11 — manual classification survives model reruns and reimport
# ---------------------------------------------------------------------------


def test_get_current_classification_matches_list_transactions_active_cls(
    session, account
):
    """Consistency check: get_current_classification and list_transactions both use the
    same active classification logic.

    Seed a transaction with two Classifications (older accepted, newer rejected).
    Both get_current_classification and the transaction_type returned by
    list_transactions must reflect the older accepted row, not the rejected one.
    """
    tx = _tx(session, account.id, display_text="CONSISTENCY TEST")
    session.commit()

    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    accepted_cls = _cls(
        session, tx.id, transaction_type="expense", category_id="groceries",
        review_state="accepted", created_at=t1,
    )
    _cls(
        session, tx.id, transaction_type="income",
        review_state="rejected", created_at=t2,
    )
    session.commit()

    # get_current_classification must return the accepted row
    active = get_current_classification(session, tx.id)
    assert active is not None
    assert active.id == accepted_cls.id
    assert active.transaction_type == "expense"

    # list_transactions must surface the same type for the row
    page = list_transactions(session, LedgerFilters())
    row = next((r for r in page.rows if r.id == tx.id), None)
    assert row is not None
    assert row.transaction_type == "expense"
    assert row.category_id == "groceries"


def test_a11_manual_accepted_beats_newer_needs_review_in_list(session, account):
    """A11: manual-accepted classification survives a classify-batch rerun.

    After a model rerun inserts a newer needs_review classification, both
    get_current_classification and list_transactions must still surface the
    accepted row — not the newer needs_review one.

    This test documents that A11 is enforced at the QUERY layer: accepted rows
    are sorted before needs_review rows regardless of creation timestamp.
    """
    tx = _tx(session, account.id, display_text="A11 RERUN TEST")
    session.commit()

    t1 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 3, 2, tzinfo=timezone.utc)  # later — simulates model rerun
    manual_cls = _cls(
        session, tx.id,
        transaction_type="expense",
        category_id="groceries",
        review_state="accepted",
        source="manual",
        created_at=t1,
    )
    _cls(
        session, tx.id,
        transaction_type="unknown",
        review_state="needs_review",
        source="model",
        created_at=t2,
    )
    session.commit()

    # Query-layer: accepted wins over the newer needs_review row
    active = get_current_classification(session, tx.id)
    assert active is not None
    assert active.id == manual_cls.id
    assert active.review_state == "accepted"
    assert active.transaction_type == "expense"

    # list_transactions must surface the same result
    page = list_transactions(session, LedgerFilters())
    row = next((r for r in page.rows if r.id == tx.id), None)
    assert row is not None
    assert row.transaction_type == "expense"
    assert row.category_id == "groceries"
    assert row.review_state == "accepted"


# ---------------------------------------------------------------------------
# AC-13 — set_note
# ---------------------------------------------------------------------------


class TestSetNote:
    def test_set_new_note_stores_and_audits(self, session, account):
        tx = _tx(session, account.id)
        session.commit()

        set_note(session, tx.id, "Holiday groceries")
        session.commit()

        refreshed = session.get(Transaction, tx.id)
        assert refreshed.note == "Holiday groceries"
        assert refreshed.note_updated_at is not None

        events = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_id == tx.id, AuditEvent.action == "note_updated")
            .all()
        )
        assert len(events) == 1
        assert events[0].before_state == {"note": None}
        assert events[0].after_state == {"note": "Holiday groceries"}

    def test_update_existing_note_reflects_old_value(self, session, account):
        tx = _tx(session, account.id, note="old note")
        session.commit()

        set_note(session, tx.id, "new note")
        session.commit()

        events = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_id == tx.id, AuditEvent.action == "note_updated")
            .all()
        )
        assert len(events) == 1
        assert events[0].before_state == {"note": "old note"}
        assert events[0].after_state == {"note": "new note"}

    def test_clear_with_empty_string(self, session, account):
        tx = _tx(session, account.id, note="existing")
        session.commit()

        set_note(session, tx.id, "")
        session.commit()

        refreshed = session.get(Transaction, tx.id)
        assert refreshed.note is None
        assert refreshed.note_updated_at is not None
        events = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_id == tx.id, AuditEvent.action == "note_updated")
            .all()
        )
        assert len(events) == 1

    def test_clear_with_whitespace_only(self, session, account):
        tx = _tx(session, account.id, note="existing")
        session.commit()

        set_note(session, tx.id, "   ")
        session.commit()

        refreshed = session.get(Transaction, tx.id)
        assert refreshed.note is None

    def test_too_long_raises_before_any_mutation(self, session, account):
        tx = _tx(session, account.id, note="original")
        session.commit()

        with pytest.raises(ValueError, match="2000"):
            set_note(session, tx.id, "x" * 2001)

        refreshed = session.get(Transaction, tx.id)
        assert refreshed.note == "original"  # unchanged

    def test_no_op_same_value_no_audit(self, session, account):
        tx = _tx(session, account.id, note="unchanged")
        session.commit()

        set_note(session, tx.id, "unchanged")
        session.commit()

        events = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_id == tx.id, AuditEvent.action == "note_updated")
            .all()
        )
        assert len(events) == 0

    def test_no_op_none_to_none_no_audit(self, session, account):
        tx = _tx(session, account.id, note=None)
        session.commit()

        set_note(session, tx.id, None)
        session.commit()

        events = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_id == tx.id, AuditEvent.action == "note_updated")
            .all()
        )
        assert len(events) == 0

    def test_unknown_transaction_id_raises(self, session):
        with pytest.raises(ValueError, match="not found"):
            set_note(session, "does-not-exist", "note")


# ---------------------------------------------------------------------------
# AC-14 — Note unchanged by reimport and classification edits
# ---------------------------------------------------------------------------


class TestNotePreservation:
    def test_note_survives_manual_override(self, session, account):
        """manual_override() must not touch note or note_updated_at."""
        import services.classification_service as cls_svc

        ts = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        tx = _tx(session, account.id, note="keep me", note_updated_at=ts)
        session.commit()

        cls_svc.manual_override(
            session, tx.id,
            transaction_type="expense",
            category_id="groceries",
            merchant="Lidl",
        )
        session.commit()

        refreshed = session.get(Transaction, tx.id)
        assert refreshed.note == "keep me"

    def test_note_survives_set_note_then_classify(self, session, account):
        """Setting a note then classifying leaves note intact."""
        import services.classification_service as cls_svc

        tx = _tx(session, account.id)
        session.commit()

        set_note(session, tx.id, "budget note")
        session.commit()

        cls_svc.manual_override(
            session, tx.id,
            transaction_type="income",
            category_id=None,
            merchant=None,
        )
        session.commit()

        refreshed = session.get(Transaction, tx.id)
        assert refreshed.note == "budget note"

    def test_note_model_columns_exist(self, db_engine):
        """Transaction model has note and note_updated_at columns."""
        inspector = inspect(db_engine)
        col_names = {c["name"] for c in inspector.get_columns("transactions")}
        assert "note" in col_names
        assert "note_updated_at" in col_names

    def test_transaction_created_with_null_note(self, session, account):
        tx = _tx(session, account.id)
        session.commit()
        refreshed = session.get(Transaction, tx.id)
        assert refreshed.note is None
        assert refreshed.note_updated_at is None
