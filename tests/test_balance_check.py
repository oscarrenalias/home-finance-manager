"""Tests for domain.balance_check — BalanceMismatch, BalanceCheckResult, check_balances."""
from __future__ import annotations

from datetime import date

import pytest

from domain.balance_check import BalanceCheckResult, BalanceMismatch, check_balances
from domain.parser import ParsedRow


def _make_row(
    row_number: int,
    dt: date,
    amount_cents: int,
    balance_cents: int | None,
    is_pending: bool = False,
) -> ParsedRow:
    return ParsedRow(
        row_number=row_number,
        raw_date=dt.strftime("%d.%m.%Y"),
        raw_category="",
        raw_subcategory="",
        raw_text="SHOP",
        raw_amount=str(amount_cents),
        raw_balance="" if balance_cents is None else str(balance_cents),
        raw_status="Pending" if is_pending else "Executed",
        raw_reconciled="No",
        parsed_date=dt,
        parsed_amount_cents=amount_cents,
        parsed_balance_cents=balance_cents,
        is_pending=is_pending,
        display_text="SHOP",
    )


class TestDataclasses:
    def test_balance_mismatch_fields(self):
        m = BalanceMismatch(
            row_number_a=1,
            row_number_b=2,
            expected_cents=99000,
            actual_cents=98000,
        )
        assert m.row_number_a == 1
        assert m.row_number_b == 2
        assert m.expected_cents == 99000
        assert m.actual_cents == 98000

    def test_balance_check_result_defaults(self):
        r = BalanceCheckResult(checked_pairs=0)
        assert r.mismatches == []
        assert r.inconclusive_reasons == []

    def test_balance_check_result_not_shared_mutable_default(self):
        r1 = BalanceCheckResult(checked_pairs=0)
        r1.mismatches.append(BalanceMismatch(1, 2, 0, 0))
        r1.inconclusive_reasons.append("reason")
        r2 = BalanceCheckResult(checked_pairs=0)
        assert r2.mismatches == []
        assert r2.inconclusive_reasons == []

    def test_balance_mismatch_requires_all_fields(self):
        with pytest.raises(TypeError):
            BalanceMismatch(row_number_a=1, row_number_b=2, expected_cents=0)  # type: ignore[call-arg]


class TestCheckBalances:
    def test_empty_input_zero_pairs(self):
        result = check_balances([])
        assert result.checked_pairs == 0
        assert result.mismatches == []
        assert result.inconclusive_reasons == []

    def test_single_row_zero_pairs(self):
        row = _make_row(2, date(2026, 1, 1), -1000, 99000)
        result = check_balances([row])
        assert result.checked_pairs == 0

    def test_ascending_two_row_match(self):
        # 100000 + (-1000) == 99000 — arithmetic holds
        row1 = _make_row(2, date(2026, 1, 1), -500, 100000)
        row2 = _make_row(3, date(2026, 1, 2), -1000, 99000)
        result = check_balances([row1, row2])
        assert result.checked_pairs == 1
        assert result.mismatches == []
        assert result.inconclusive_reasons == []

    def test_ascending_two_row_mismatch(self):
        # 100000 + (-1000) == 99000, but actual == 98000 → mismatch
        row1 = _make_row(2, date(2026, 1, 1), -500, 100000)
        row2 = _make_row(3, date(2026, 1, 2), -1000, 98000)
        result = check_balances([row1, row2])
        assert result.checked_pairs == 1
        assert len(result.mismatches) == 1
        m = result.mismatches[0]
        assert m.expected_cents == 99000
        assert m.actual_cents == 98000
        assert m.row_number_a == 2
        assert m.row_number_b == 3

    def test_descending_two_row_match(self):
        # Newer row first — should be reversed and produce same check result
        row_newer = _make_row(2, date(2026, 1, 2), -1000, 99000)
        row_older = _make_row(3, date(2026, 1, 1), -500, 100000)
        result = check_balances([row_newer, row_older])
        assert result.checked_pairs == 1
        assert result.mismatches == []

    def test_descending_two_row_mismatch(self):
        # Newer row has wrong balance; after reversal, mismatch is detected
        row_newer = _make_row(2, date(2026, 1, 2), -1000, 98000)
        row_older = _make_row(3, date(2026, 1, 1), -500, 100000)
        result = check_balances([row_newer, row_older])
        assert result.checked_pairs == 1
        assert len(result.mismatches) == 1
        m = result.mismatches[0]
        assert m.expected_cents == 99000
        assert m.actual_cents == 98000

    def test_same_day_pair_inconclusive_not_mismatch(self):
        row1 = _make_row(2, date(2026, 1, 1), -500, 100000)
        row2 = _make_row(3, date(2026, 1, 1), -1000, 99000)
        result = check_balances([row1, row2])
        assert result.checked_pairs == 0
        assert result.mismatches == []
        assert len(result.inconclusive_reasons) == 1

    def test_mixed_valid_pair_and_same_day(self):
        # rows 2→3: different days (valid pair), rows 3→4: same day (inconclusive)
        row1 = _make_row(2, date(2026, 1, 1), -500, 100000)
        row2 = _make_row(3, date(2026, 1, 2), -1000, 99000)
        row3 = _make_row(4, date(2026, 1, 2), -200, 98800)
        result = check_balances([row1, row2, row3])
        assert result.checked_pairs == 1
        assert len(result.inconclusive_reasons) == 1

    def test_pending_rows_excluded(self):
        # Pending row: excluded even with non-null balance
        row_pending = _make_row(2, date(2026, 1, 1), -500, 100000, is_pending=True)
        row_exec = _make_row(3, date(2026, 1, 2), -1000, 99000)
        result = check_balances([row_pending, row_exec])
        assert result.checked_pairs == 0

    def test_null_balance_rows_excluded(self):
        row1 = _make_row(2, date(2026, 1, 1), -500, None)
        row2 = _make_row(3, date(2026, 1, 2), -1000, 99000)
        result = check_balances([row1, row2])
        assert result.checked_pairs == 0

    def test_three_valid_rows_two_pairs_checked(self):
        # 100000 + (-1000) == 99000, 99000 + (-2000) == 97000
        row1 = _make_row(2, date(2026, 1, 1), -500, 100000)
        row2 = _make_row(3, date(2026, 1, 2), -1000, 99000)
        row3 = _make_row(4, date(2026, 1, 3), -2000, 97000)
        result = check_balances([row1, row2, row3])
        assert result.checked_pairs == 2
        assert result.mismatches == []

    def test_no_reflex_import(self):
        import sys
        import domain.balance_check  # noqa: F401
        assert "reflex" not in {mod.split(".")[0] for mod in sys.modules}
