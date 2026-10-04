"""Tests for services/transfer_service.py.

Covers find_transfer_candidates, confirm_transfer, and undo_transfer.
Uses function-scoped in-memory SQLite; no Reflex imports; no sample-data files.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.transfer_service import confirm_transfer, find_transfer_candidates, undo_transfer
from storage.models import Account, AuditEvent, Base, Classification, Transaction, TransferLink


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine(tmp_path) -> Generator[Engine, None, None]:
    db_path = tmp_path / "transfer_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(db_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    s = factory()
    # Seed two accounts
    acct_a = Account(id="acct-aaa", name="Common", role="common", currency="EUR", active=True)
    acct_b = Account(id="acct-bbb", name="Accrual", role="accrual", currency="EUR", active=True)
    s.add_all([acct_a, acct_b])
    s.commit()
    yield s
    s.close()


def _txn(session: Session, txn_id: str, account_id: str, amount_cents: int, txn_date: date) -> Transaction:
    t = Transaction(
        id=txn_id,
        account_id=account_id,
        date=txn_date,
        amount_cents=amount_cents,
        balance_after=None,
        currency="EUR",
        original_text="Test",
        display_text="Test",
        status="Executed",
        transaction_type="unknown",
    )
    session.add(t)
    session.flush()
    return t


# ---------------------------------------------------------------------------
# find_transfer_candidates
# ---------------------------------------------------------------------------


def test_find_candidates_returns_empty_for_unknown_transaction(session):
    result = find_transfer_candidates(session, "nonexistent-id")
    assert result == []


def test_find_candidates_returns_opposite_amount_different_account(session):
    ref_date = date(2026, 1, 10)
    # "a" < "b" lexicographically; use clear ordering
    src = _txn(session, "txn-source-1", "acct-aaa", -15000, ref_date)
    candidate = _txn(session, "txn-cand-01", "acct-bbb", 15000, ref_date)
    session.commit()

    results = find_transfer_candidates(session, src.id)
    assert len(results) == 1
    assert results[0].id == candidate.id


def test_find_candidates_excludes_same_account(session):
    ref_date = date(2026, 1, 10)
    src = _txn(session, "txn-source-2", "acct-aaa", -15000, ref_date)
    # Same account as source — should be excluded
    _txn(session, "txn-same-acct", "acct-aaa", 15000, ref_date)
    session.commit()

    results = find_transfer_candidates(session, src.id)
    assert results == []


def test_find_candidates_excludes_outside_date_window(session):
    ref_date = date(2026, 1, 10)
    src = _txn(session, "txn-source-3", "acct-aaa", -15000, ref_date)
    # 4 days away — outside the ±3-day window
    _txn(session, "txn-far-date", "acct-bbb", 15000, date(2026, 1, 14))
    session.commit()

    results = find_transfer_candidates(session, src.id)
    assert results == []


def test_find_candidates_includes_boundary_dates(session):
    ref_date = date(2026, 1, 10)
    src = _txn(session, "txn-source-4", "acct-aaa", -15000, ref_date)
    # Exactly 3 days away — must be included
    boundary = _txn(session, "txn-boundary", "acct-bbb", 15000, date(2026, 1, 13))
    session.commit()

    results = find_transfer_candidates(session, src.id)
    assert any(r.id == boundary.id for r in results)


def test_find_candidates_excludes_already_linked_transactions(session):
    ref_date = date(2026, 1, 10)
    # Use IDs that sort lexicographically correctly for the check constraint
    src = _txn(session, "txn-aaa-src", "acct-aaa", -15000, ref_date)
    already_linked = _txn(session, "txn-bbb-lnk", "acct-bbb", 15000, ref_date)
    session.flush()

    link = TransferLink(
        transaction_a_id="txn-aaa-src",
        transaction_b_id="txn-bbb-lnk",
        confirmed_by="manual",
    )
    session.add(link)
    session.commit()

    results = find_transfer_candidates(session, src.id)
    assert not any(r.id == already_linked.id for r in results)


# ---------------------------------------------------------------------------
# confirm_transfer
# ---------------------------------------------------------------------------


def test_confirm_transfer_creates_link_and_classifications(session):
    ref_date = date(2026, 2, 1)
    # Ensure lexicographic order: "txn-alpha" < "txn-omega"
    txn_a = _txn(session, "txn-alpha", "acct-aaa", -10000, ref_date)
    txn_b = _txn(session, "txn-omega", "acct-bbb", 10000, ref_date)
    session.flush()

    link = confirm_transfer(session, txn_a.id, txn_b.id)
    session.commit()

    assert link.id is not None
    assert link.transaction_a_id == "txn-alpha"
    assert link.transaction_b_id == "txn-omega"
    assert link.confirmed_by == "manual"

    # Both transactions should have Classification rows set to internal_transfer
    clfs = session.query(Classification).filter(
        Classification.transaction_id.in_([txn_a.id, txn_b.id])
    ).all()
    assert len(clfs) == 2
    for clf in clfs:
        assert clf.transaction_type == "internal_transfer"
        assert clf.source == "rule"


def test_confirm_transfer_normalises_id_order(session):
    ref_date = date(2026, 2, 1)
    # Pass in reverse order; confirm_transfer should swap them to satisfy DB constraint
    txn_a = _txn(session, "txn-alpha2", "acct-aaa", -5000, ref_date)
    txn_b = _txn(session, "txn-omega2", "acct-bbb", 5000, ref_date)
    session.flush()

    # Deliberately pass in reverse (b before a)
    link = confirm_transfer(session, txn_b.id, txn_a.id)
    session.commit()

    assert link.transaction_a_id < link.transaction_b_id


def test_confirm_transfer_creates_audit_events(session):
    ref_date = date(2026, 2, 2)
    txn_a = _txn(session, "txn-aud-a", "acct-aaa", -2000, ref_date)
    txn_b = _txn(session, "txn-aud-b", "acct-bbb", 2000, ref_date)
    session.flush()

    confirm_transfer(session, txn_a.id, txn_b.id)
    session.commit()

    events = session.query(AuditEvent).filter(
        AuditEvent.entity_id.in_([txn_a.id, txn_b.id]),
        AuditEvent.action == "transfer_linked",
    ).all()
    assert len(events) == 2


# ---------------------------------------------------------------------------
# undo_transfer
# ---------------------------------------------------------------------------


def test_undo_transfer_removes_link(session):
    ref_date = date(2026, 3, 1)
    txn_a = _txn(session, "txn-und-a", "acct-aaa", -3000, ref_date)
    txn_b = _txn(session, "txn-und-b", "acct-bbb", 3000, ref_date)
    session.flush()

    link = confirm_transfer(session, txn_a.id, txn_b.id)
    session.commit()

    link_id = link.id
    undo_transfer(session, link_id)
    session.commit()

    assert session.get(TransferLink, link_id) is None


def test_undo_transfer_creates_audit_events(session):
    ref_date = date(2026, 3, 2)
    txn_a = _txn(session, "txn-undev-a", "acct-aaa", -8000, ref_date)
    txn_b = _txn(session, "txn-undev-b", "acct-bbb", 8000, ref_date)
    session.flush()

    link = confirm_transfer(session, txn_a.id, txn_b.id)
    session.commit()

    undo_transfer(session, link.id)
    session.commit()

    events = session.query(AuditEvent).filter(
        AuditEvent.entity_id.in_([txn_a.id, txn_b.id]),
        AuditEvent.action == "transfer_unlinked",
    ).all()
    assert len(events) == 2


def test_undo_transfer_is_idempotent(session):
    # Undoing a nonexistent link must not raise
    undo_transfer(session, "nonexistent-link-id")
    session.commit()


def test_undo_transfer_rejects_internal_transfer_classifications(session):
    """After undo_transfer, rule-sourced internal_transfer Classification rows are rejected."""
    from services.classification_service import resolve_active_classification

    ref_date = date(2026, 3, 3)
    txn_a = _txn(session, "txn-rej-a", "acct-aaa", -5000, ref_date)
    txn_b = _txn(session, "txn-rej-b", "acct-bbb", 5000, ref_date)
    session.flush()

    link = confirm_transfer(session, txn_a.id, txn_b.id)
    session.commit()

    undo_transfer(session, link.id)
    session.commit()

    # Stale internal_transfer classifications must be rejected, not active
    stale = session.query(Classification).filter(
        Classification.transaction_id.in_([txn_a.id, txn_b.id]),
        Classification.transaction_type == "internal_transfer",
    ).all()
    assert len(stale) == 2
    assert all(c.review_state == "rejected" for c in stale)

    # resolve_active_classification skips rejected rows, so both return None
    assert resolve_active_classification(session, txn_a.id) is None
    assert resolve_active_classification(session, txn_b.id) is None


def test_a08_transfer_combined_spending_unchanged(session):
    """A08: Linking as internal transfer yields affects_spending=False; undo restores None."""
    from domain.classification import affects_spending
    from services.classification_service import resolve_active_classification

    ref_date = date(2026, 4, 1)
    txn_a = _txn(session, "txn-a08-a", "acct-aaa", -22000, ref_date)
    txn_b = _txn(session, "txn-a08-b", "acct-bbb", 22000, ref_date)
    session.flush()

    link = confirm_transfer(session, txn_a.id, txn_b.id)
    session.commit()

    clf_a = resolve_active_classification(session, txn_a.id)
    clf_b = resolve_active_classification(session, txn_b.id)
    assert clf_a is not None and clf_a.transaction_type == "internal_transfer"
    assert clf_b is not None and clf_b.transaction_type == "internal_transfer"
    assert affects_spending(clf_a) is False
    assert affects_spending(clf_b) is False

    undo_transfer(session, link.id)
    session.commit()

    # After undo the stale classifications are rejected; no active classification remains
    clf_a_after = resolve_active_classification(session, txn_a.id)
    clf_b_after = resolve_active_classification(session, txn_b.id)
    assert clf_a_after is None
    assert clf_b_after is None
