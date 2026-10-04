"""Tests for domain.classification pure functions — no DB, no Reflex."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


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
