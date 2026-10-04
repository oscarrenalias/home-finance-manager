"""Matching types for duplicate detection during CSV import."""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from domain.parser import ParsedRow


@dataclass
class ExistingRecord:
    transaction_id: str
    date: date
    amount_cents: int
    display_text: str
    balance_after: int | None
    status: str


@dataclass
class MatchCandidate:
    parsed_row: ParsedRow
    existing_transaction_id: str | None
    confidence: Literal["exact", "probable", "ambiguous", "new"]
    match_evidence: list[str] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    """Return a matching key: lowercase, whitespace collapsed, cosmetic ')' suffix stripped."""
    stripped = text.rstrip().rstrip(")")
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return collapsed.lower()


def match_rows(
    parsed_rows: list[ParsedRow],
    existing: list[ExistingRecord],
) -> list[MatchCandidate]:
    """Match parsed rows against existing ledger records to determine confidence levels.

    One-to-one pool: each ExistingRecord is consumed at most once per call.
    Pending rows always yield confidence='new'.
    Bank category/subcategory are not used as matching evidence.
    """
    pool = list(existing)
    results: list[MatchCandidate] = []

    for row in parsed_rows:
        if row.is_pending:
            results.append(
                MatchCandidate(
                    parsed_row=row,
                    existing_transaction_id=None,
                    confidence="new",
                    match_evidence=[],
                )
            )
            continue

        norm_text = _normalize_text(row.display_text)

        date_amount = [
            r
            for r in pool
            if r.date == row.parsed_date and r.amount_cents == row.parsed_amount_cents
        ]

        if not date_amount:
            results.append(
                MatchCandidate(
                    parsed_row=row,
                    existing_transaction_id=None,
                    confidence="new",
                    match_evidence=[],
                )
            )
            continue

        text_matches = [r for r in date_amount if _normalize_text(r.display_text) == norm_text]

        if not text_matches:
            # date+amount match only — ambiguous when multiple candidates, else new
            confidence = "ambiguous" if len(date_amount) > 1 else "new"
            evidence = ["date", "amount"] if confidence == "ambiguous" else []
            results.append(
                MatchCandidate(
                    parsed_row=row,
                    existing_transaction_id=None,
                    confidence=confidence,  # type: ignore[arg-type]
                    match_evidence=evidence,
                )
            )
            continue

        # Look for balance confirmation among text matches
        exact: ExistingRecord | None = None
        if row.parsed_balance_cents is not None:
            exact = next(
                (
                    r
                    for r in text_matches
                    if r.balance_after is not None and r.balance_after == row.parsed_balance_cents
                ),
                None,
            )

        if exact is not None:
            pool.remove(exact)
            results.append(
                MatchCandidate(
                    parsed_row=row,
                    existing_transaction_id=exact.transaction_id,
                    confidence="exact",
                    match_evidence=["date", "amount", "text", "balance"],
                )
            )
        else:
            matched = text_matches[0]
            pool.remove(matched)
            results.append(
                MatchCandidate(
                    parsed_row=row,
                    existing_transaction_id=matched.transaction_id,
                    confidence="probable",
                    match_evidence=["date", "amount", "text"],
                )
            )

    return results
