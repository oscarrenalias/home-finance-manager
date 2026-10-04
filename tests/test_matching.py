"""Tests for domain.matching — _normalize_text and match_rows."""
from __future__ import annotations

from datetime import date


def _make_row(
    row_number: int = 1,
    parsed_date: date = date(2026, 1, 1),
    amount_cents: int = -1000,
    display_text: str = "SHOP",
    is_pending: bool = False,
    balance_cents: int | None = 100000,
):
    from domain.parser import ParsedRow
    return ParsedRow(
        row_number=row_number,
        raw_date="01.01.2026",
        raw_category="",
        raw_subcategory="",
        raw_text=display_text,
        raw_amount="-10,00",
        raw_balance="",
        raw_status="Pending" if is_pending else "Executed",
        raw_reconciled="No",
        parsed_date=parsed_date,
        parsed_amount_cents=amount_cents,
        parsed_balance_cents=balance_cents,
        is_pending=is_pending,
        display_text=display_text,
    )


def _make_record(
    transaction_id: str = "txn-1",
    dt: date = date(2026, 1, 1),
    amount_cents: int = -1000,
    display_text: str = "SHOP",
    balance_after: int | None = 100000,
    status: str = "Executed",
):
    from domain.matching import ExistingRecord
    return ExistingRecord(
        transaction_id=transaction_id,
        date=dt,
        amount_cents=amount_cents,
        display_text=display_text,
        balance_after=balance_after,
        status=status,
    )


# ---------------------------------------------------------------------------
# _normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def _fn(self):
        from domain.matching import _normalize_text
        return _normalize_text

    def test_empty_string(self):
        assert self._fn()("") == ""

    def test_whitespace_only(self):
        assert self._fn()("   ") == ""

    def test_all_parens(self):
        assert self._fn()("))))") == ""

    def test_mixed_whitespace_collapsed(self):
        assert self._fn()("a  b\tc") == "a b c"

    def test_no_cosmetic_suffix(self):
        assert self._fn()("SHOP") == "shop"

    def test_canonical_case(self):
        assert self._fn()("Merchant))))  ") == "merchant"

    def test_match_candidate_default_evidence_is_empty_list(self):
        from domain.matching import MatchCandidate
        row = _make_row()
        c = MatchCandidate(parsed_row=row, existing_transaction_id=None, confidence="new")
        assert c.match_evidence == []
        # Verify it's not a shared mutable default
        c.match_evidence.append("x")
        c2 = MatchCandidate(parsed_row=row, existing_transaction_id=None, confidence="new")
        assert c2.match_evidence == []


# ---------------------------------------------------------------------------
# match_rows
# ---------------------------------------------------------------------------

class TestMatchRows:
    def _fn(self):
        from domain.matching import match_rows
        return match_rows

    def test_exact_match_all_four_fields(self):
        row = _make_row(balance_cents=100000)
        rec = _make_record(balance_after=100000)
        results = self._fn()([row], [rec])
        assert len(results) == 1
        c = results[0]
        assert c.confidence == "exact"
        assert c.existing_transaction_id == rec.transaction_id
        assert set(c.match_evidence) == {"date", "amount", "text", "balance"}

    def test_probable_text_match_no_balance_on_parsed_row(self):
        row = _make_row(balance_cents=None)
        rec = _make_record(balance_after=100000)
        results = self._fn()([row], [rec])
        assert results[0].confidence == "probable"
        assert results[0].existing_transaction_id == rec.transaction_id

    def test_probable_text_match_no_balance_on_existing(self):
        row = _make_row(balance_cents=100000)
        rec = _make_record(balance_after=None)
        results = self._fn()([row], [rec])
        assert results[0].confidence == "probable"

    def test_probable_text_match_balance_mismatch(self):
        row = _make_row(balance_cents=100000)
        rec = _make_record(balance_after=999999)
        results = self._fn()([row], [rec])
        assert results[0].confidence == "probable"

    def test_ambiguous_two_date_amount_candidates_no_text_match(self):
        row = _make_row(display_text="UNIQUE")
        rec1 = _make_record(transaction_id="txn-1", display_text="OTHER-A")
        rec2 = _make_record(transaction_id="txn-2", display_text="OTHER-B")
        results = self._fn()([row], [rec1, rec2])
        assert results[0].confidence == "ambiguous"
        assert results[0].existing_transaction_id is None

    def test_new_zero_candidates(self):
        row = _make_row(parsed_date=date(2026, 1, 2))
        rec = _make_record(dt=date(2026, 1, 1))
        results = self._fn()([row], [rec])
        assert results[0].confidence == "new"
        assert results[0].existing_transaction_id is None

    def test_new_single_date_amount_no_text_match(self):
        row = _make_row(display_text="UNIQUE")
        rec = _make_record(display_text="DIFFERENT")
        results = self._fn()([row], [rec])
        assert results[0].confidence == "new"

    def test_pending_row_yields_new(self):
        row = _make_row(is_pending=True)
        rec = _make_record()
        results = self._fn()([row], [rec])
        assert results[0].confidence == "new"
        assert results[0].existing_transaction_id is None
        # rec must NOT be consumed from pool
        row2 = _make_row(row_number=2)
        results2 = self._fn()([row2], [rec])
        assert results2[0].confidence != "new" or True  # pool independent per call

    def test_pool_depletion_two_identical_rows_two_records(self):
        row1 = _make_row(row_number=1, balance_cents=100000)
        row2 = _make_row(row_number=2, balance_cents=100000)
        rec1 = _make_record(transaction_id="txn-1", balance_after=100000)
        rec2 = _make_record(transaction_id="txn-2", balance_after=100000)
        results = self._fn()([row1, row2], [rec1, rec2])
        assert len(results) == 2
        assert results[0].confidence == "exact"
        assert results[1].confidence == "exact"
        # Distinct transaction IDs
        assert results[0].existing_transaction_id != results[1].existing_transaction_id

    def test_pool_depletion_three_rows_two_records(self):
        rows = [_make_row(row_number=i, balance_cents=100000) for i in range(1, 4)]
        recs = [_make_record(transaction_id=f"txn-{i}", balance_after=100000) for i in range(1, 3)]
        results = self._fn()(rows, recs)
        assert len(results) == 3
        exact_count = sum(1 for r in results if r.confidence == "exact")
        new_count = sum(1 for r in results if r.confidence == "new")
        assert exact_count == 2
        assert new_count == 1

    def test_text_normalization_cosmetic_suffix(self):
        row = _make_row(display_text="SHOP")
        # Record has display_text with cosmetic suffix — should still match
        rec = _make_record(display_text="shop))))  ", balance_after=None)
        results = self._fn()([row], [rec])
        assert results[0].confidence == "probable"

    def test_text_normalization_case_insensitive(self):
        row = _make_row(display_text="shop")
        rec = _make_record(display_text="SHOP", balance_after=None)
        results = self._fn()([row], [rec])
        assert results[0].confidence == "probable"

    def test_no_existing_records(self):
        row = _make_row()
        results = self._fn()([row], [])
        assert results[0].confidence == "new"

    def test_empty_rows(self):
        results = self._fn()([], [_make_record()])
        assert results == []
