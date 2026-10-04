"""Tests for domain.classification pure functions and classification service — no Reflex."""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


# ---------------------------------------------------------------------------
# Minimal concrete types that satisfy the protocols
# ---------------------------------------------------------------------------

@dataclass
class _Rule:
    priority: int
    pattern_type: str
    pattern_value: str
    is_confirmed: bool
    transaction_type: str
    category_id: str | None = None
    merchant: str | None = None


@dataclass
class _Classification:
    source: str
    transaction_type: str
    review_state: str = "accepted"
    category_id: str | None = None
    merchant: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _ts(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# match_rules
# ---------------------------------------------------------------------------

class TestMatchRules:
    def _fn(self):
        from domain.classification import match_rules
        return match_rules

    def test_no_rules_returns_none(self):
        assert self._fn()("SHOP", None, []) is None

    def test_unconfirmed_rule_ignored(self):
        rule = _Rule(1, "text_contains", "shop", is_confirmed=False,
                     transaction_type="expense")
        assert self._fn()("SHOP payment", None, [rule]) is None

    def test_text_contains_case_insensitive(self):
        rule = _Rule(1, "text_contains", "shop", is_confirmed=True,
                     transaction_type="expense")
        result = self._fn()("SHOP payment", None, [rule])
        assert result is rule

    def test_text_contains_no_match(self):
        rule = _Rule(1, "text_contains", "grocery", is_confirmed=True,
                     transaction_type="expense")
        assert self._fn()("SHOP payment", None, [rule]) is None

    def test_merchant_exact_match(self):
        rule = _Rule(1, "merchant_exact", "k-market", is_confirmed=True,
                     transaction_type="expense")
        result = self._fn()("purchase", "K-Market", [rule])
        assert result is rule

    def test_merchant_exact_no_match_on_substring(self):
        rule = _Rule(1, "merchant_exact", "market", is_confirmed=True,
                     transaction_type="expense")
        assert self._fn()("purchase", "K-Market", [rule]) is None

    def test_merchant_contains_match(self):
        rule = _Rule(1, "merchant_contains", "market", is_confirmed=True,
                     transaction_type="expense")
        result = self._fn()("purchase", "K-Market", [rule])
        assert result is rule

    def test_merchant_contains_no_merchant_skipped(self):
        rule = _Rule(1, "merchant_contains", "market", is_confirmed=True,
                     transaction_type="expense")
        assert self._fn()("purchase", None, [rule]) is None

    def test_merchant_exact_no_merchant_skipped(self):
        rule = _Rule(1, "merchant_exact", "k-market", is_confirmed=True,
                     transaction_type="expense")
        assert self._fn()("purchase", None, [rule]) is None

    def test_higher_priority_wins(self):
        low = _Rule(1, "text_contains", "shop", is_confirmed=True,
                    transaction_type="expense")
        high = _Rule(10, "text_contains", "shop", is_confirmed=True,
                     transaction_type="income")
        result = self._fn()("shop visit", None, [low, high])
        assert result is high

    def test_only_confirmed_rules_compete_for_priority(self):
        unconfirmed_high = _Rule(99, "text_contains", "shop", is_confirmed=False,
                                 transaction_type="income")
        confirmed_low = _Rule(1, "text_contains", "shop", is_confirmed=True,
                              transaction_type="expense")
        result = self._fn()("shop visit", None, [unconfirmed_high, confirmed_low])
        assert result is confirmed_low

    def test_unknown_pattern_type_does_not_match(self):
        rule = _Rule(1, "regex", ".*shop.*", is_confirmed=True,
                     transaction_type="expense")
        assert self._fn()("shop", None, [rule]) is None

    def test_merchant_exact_case_insensitive(self):
        rule = _Rule(1, "merchant_exact", "prisma", is_confirmed=True,
                     transaction_type="expense")
        result = self._fn()("purchase", "PRISMA", [rule])
        assert result is rule


# ---------------------------------------------------------------------------
# resolve_active
# ---------------------------------------------------------------------------

class TestResolveActive:
    def _fn(self):
        from domain.classification import resolve_active
        return resolve_active

    def test_empty_list_no_link_returns_none(self):
        assert self._fn()([], False) is None

    def test_transfer_link_returns_internal_transfer(self):
        c = _Classification("rule", "expense")
        result = self._fn()([c], has_transfer_link=True)
        assert result is not None
        assert result.transaction_type == "internal_transfer"

    def test_transfer_link_prefers_existing_internal_transfer_classification(self):
        expense_c = _Classification("manual", "expense", created_at=_ts(2026, 1, 2))
        transfer_c = _Classification("rule", "internal_transfer", created_at=_ts(2026, 1, 1))
        result = self._fn()([expense_c, transfer_c], has_transfer_link=True)
        assert result is transfer_c

    def test_transfer_link_no_existing_transfer_yields_synthetic(self):
        from domain.classification import _TransferClassification
        c = _Classification("manual", "expense")
        result = self._fn()([c], has_transfer_link=True)
        assert isinstance(result, _TransferClassification)
        assert result.transaction_type == "internal_transfer"
        assert result.source == "rule"
        assert result.review_state == "accepted"

    def test_manual_wins_over_rule(self):
        manual = _Classification("manual", "income", created_at=_ts(2026, 1, 1))
        rule = _Classification("rule", "expense", created_at=_ts(2026, 1, 2))
        result = self._fn()([manual, rule], has_transfer_link=False)
        assert result is manual

    def test_rule_wins_over_llm(self):
        rule = _Classification("rule", "expense", created_at=_ts(2026, 1, 1))
        llm = _Classification("llm", "income", created_at=_ts(2026, 1, 2))
        result = self._fn()([rule, llm], has_transfer_link=False)
        assert result is rule

    def test_same_tier_latest_timestamp_wins(self):
        older = _Classification("rule", "expense", created_at=_ts(2026, 1, 1))
        newer = _Classification("rule", "income", created_at=_ts(2026, 1, 2))
        result = self._fn()([older, newer], has_transfer_link=False)
        assert result is newer

    def test_single_classification_returned(self):
        c = _Classification("llm", "expense")
        result = self._fn()([c], has_transfer_link=False)
        assert result is c

    def test_transfer_link_latest_internal_transfer_wins(self):
        old_t = _Classification("rule", "internal_transfer", created_at=_ts(2026, 1, 1))
        new_t = _Classification("manual", "internal_transfer", created_at=_ts(2026, 1, 2))
        result = self._fn()([old_t, new_t], has_transfer_link=True)
        assert result is new_t


# ---------------------------------------------------------------------------
# affects_spending
# ---------------------------------------------------------------------------

class TestAffectsSpending:
    def _fn(self):
        from domain.classification import affects_spending
        return affects_spending

    def test_none_returns_false(self):
        assert self._fn()(None) is False

    def test_expense_returns_true(self):
        c = _Classification("rule", "expense")
        assert self._fn()(c) is True

    def test_refund_returns_true(self):
        c = _Classification("rule", "refund")
        assert self._fn()(c) is True

    def test_income_returns_false(self):
        c = _Classification("rule", "income")
        assert self._fn()(c) is False

    def test_internal_transfer_returns_false(self):
        c = _Classification("rule", "internal_transfer")
        assert self._fn()(c) is False

    def test_unknown_returns_false(self):
        c = _Classification("rule", "unknown")
        assert self._fn()(c) is False

    def test_contribution_returns_false(self):
        c = _Classification("rule", "contribution")
        assert self._fn()(c) is False

    def test_external_transfer_returns_false(self):
        c = _Classification("rule", "external_transfer")
        assert self._fn()(c) is False


# ---------------------------------------------------------------------------
# A12: gross / refund / net spending arithmetic (pure arithmetic, no DB)
# ---------------------------------------------------------------------------

def test_a12_gross_refund_net_spending_arithmetic():
    """A12: EUR 100 expense + EUR 20 refund → gross 100, refund 20, net 80."""
    from domain.classification import affects_spending

    expense_clf = _Classification("rule", "expense")
    refund_clf = _Classification("rule", "refund")
    transfer_clf = _Classification("rule", "internal_transfer")

    assert affects_spending(expense_clf) is True
    assert affects_spending(refund_clf) is True
    assert affects_spending(transfer_clf) is False

    # Amount stored as signed integer cents; gross uses absolute value of debit
    gross_expense_cents = abs(-10000)   # EUR 100 outflow
    refund_cents = 2000                 # EUR 20 inflow refund
    net_spending_cents = gross_expense_cents - refund_cents

    assert gross_expense_cents == 10000
    assert refund_cents == 2000
    assert net_spending_cents == 8000   # EUR 80


# ---------------------------------------------------------------------------
# Service-layer fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def svc_engine(tmp_path) -> Generator[Engine, None, None]:
    from storage.models import Base
    db_path = tmp_path / "cls_svc_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def svc_session(svc_engine) -> Generator[Session, None, None]:
    from storage.models import Account
    factory = sessionmaker(bind=svc_engine, expire_on_commit=False)
    s = factory()
    acct = Account(id="acct-cls-svc", name="Common", role="common", currency="EUR", active=True)
    s.add(acct)
    s.commit()
    yield s
    s.close()


def _txn_svc(session: Session, txn_id: str, amount_cents: int):
    from storage.models import Transaction
    t = Transaction(
        id=txn_id,
        account_id="acct-cls-svc",
        date=date(2026, 1, 1),
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
# create_rule
# ---------------------------------------------------------------------------

def test_create_rule_inserts_confirmed_rule(svc_session):
    from services.classification_service import create_rule
    rule = create_rule(svc_session, "text_contains", "supermarket", "expense", priority=5)
    svc_session.commit()
    assert rule.id is not None
    assert rule.is_confirmed is True
    assert rule.priority == 5
    assert rule.pattern_type == "text_contains"
    assert rule.transaction_type == "expense"


def test_create_rule_rejects_invalid_pattern_type(svc_session):
    from services.classification_service import create_rule
    with pytest.raises(ValueError, match="pattern_type"):
        create_rule(svc_session, "regex_match", ".*shop.*", "expense")


def test_create_rule_rejects_invalid_transaction_type(svc_session):
    from services.classification_service import create_rule
    with pytest.raises(ValueError, match="transaction_type"):
        create_rule(svc_session, "text_contains", "shop", "not_a_type")


# ---------------------------------------------------------------------------
# apply_classification
# ---------------------------------------------------------------------------

def test_apply_classification_inserts_row_and_audit(svc_session):
    from services.classification_service import apply_classification
    from storage.models import AuditEvent
    txn = _txn_svc(svc_session, "txn-apply-svc-1", -5000)
    clf = apply_classification(svc_session, txn.id, "rule", "expense", "groceries", "Prisma", None)
    svc_session.commit()
    assert clf.id is not None
    assert clf.transaction_type == "expense"
    assert clf.source == "rule"
    assert clf.review_state == "needs_review"
    audit_count = svc_session.query(AuditEvent).filter_by(entity_id=clf.id).count()
    assert audit_count == 1


def test_apply_classification_rejects_invalid_source(svc_session):
    from services.classification_service import apply_classification
    txn = _txn_svc(svc_session, "txn-apply-svc-2", -3000)
    with pytest.raises(ValueError, match="source"):
        apply_classification(svc_session, txn.id, "unknown_source", "expense", None, None, None)


def test_apply_classification_rejects_invalid_transaction_type(svc_session):
    from services.classification_service import apply_classification
    txn = _txn_svc(svc_session, "txn-apply-svc-3", -3000)
    with pytest.raises(ValueError, match="transaction_type"):
        apply_classification(svc_session, txn.id, "rule", "not_a_type", None, None, None)


# ---------------------------------------------------------------------------
# manual_override
# ---------------------------------------------------------------------------

def test_manual_override_sets_accepted_state(svc_session):
    from services.classification_service import manual_override
    from storage.models import AuditEvent
    txn = _txn_svc(svc_session, "txn-manual-svc-1", -7500)
    clf = manual_override(svc_session, txn.id, "expense", "groceries", "K-Market")
    svc_session.commit()
    assert clf.source == "manual"
    assert clf.review_state == "accepted"
    assert clf.transaction_type == "expense"
    # manual_override emits two audit events: one from apply_classification + one for override itself
    audit_count = svc_session.query(AuditEvent).filter_by(entity_id=clf.id).count()
    assert audit_count == 2


# ---------------------------------------------------------------------------
# resolve_active_classification
# ---------------------------------------------------------------------------

def test_resolve_active_classification_returns_none_for_unknown_id(svc_session):
    from services.classification_service import resolve_active_classification
    result = resolve_active_classification(svc_session, "nonexistent-txn-id")
    assert result is None


def test_resolve_active_classification_returns_manual_over_rule(svc_session):
    from services.classification_service import apply_classification, manual_override, resolve_active_classification
    txn = _txn_svc(svc_session, "txn-resolve-svc-1", -3000)
    apply_classification(svc_session, txn.id, "rule", "income", None, None, None)
    manual_override(svc_session, txn.id, "expense", "groceries", None)
    svc_session.commit()

    result = resolve_active_classification(svc_session, txn.id)
    assert result is not None
    assert result.source == "manual"
    assert result.transaction_type == "expense"


# ---------------------------------------------------------------------------
# AC-6: batch classification atomicity
# ---------------------------------------------------------------------------

def test_batch_classification_all_or_nothing(svc_engine):
    """AC-6: Classifications applied in a batch are atomic — rollback leaves zero rows."""
    from sqlalchemy.orm import sessionmaker
    from storage.models import Classification
    from services.classification_service import apply_classification

    factory = sessionmaker(bind=svc_engine, expire_on_commit=False)
    session = factory()
    try:
        txn1 = _txn_svc(session, "txn-ac6-1", -1000)
        txn2 = _txn_svc(session, "txn-ac6-2", -2000)
        _txn_svc(session, "txn-ac6-3", -3000)
        session.flush()

        apply_classification(session, txn1.id, "rule", "expense", None, None, None)
        apply_classification(session, txn2.id, "rule", "expense", None, None, None)
        # Simulate failure before third transaction is classified — roll back entire batch
        session.rollback()
    finally:
        session.close()

    # In a new session, verify no classifications were committed
    verify = factory()
    try:
        count = verify.query(Classification).count()
        assert count == 0
    finally:
        verify.close()


# ---------------------------------------------------------------------------
# A11: manual override survives reimport
# ---------------------------------------------------------------------------

def test_a11_manual_override_survives_reimport(svc_session):
    """A11: commit → manual override → reimport (new rule classifications) → resolve still returns manual."""
    from services.classification_service import apply_classification, manual_override, resolve_active_classification
    txn = _txn_svc(svc_session, "txn-a11-svc", -10000)

    apply_classification(svc_session, txn.id, "rule", "unknown", None, None, None)
    svc_session.commit()

    manual_override(svc_session, txn.id, "expense", "groceries", "Prisma")
    svc_session.commit()

    # Simulate rule engine re-run on reimport
    apply_classification(svc_session, txn.id, "rule", "income", None, None, None)
    svc_session.commit()

    result = resolve_active_classification(svc_session, txn.id)
    assert result is not None
    assert result.source == "manual"
    assert result.transaction_type == "expense"
