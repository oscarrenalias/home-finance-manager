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
    stripped = text.rstrip(")")
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return collapsed.lower()
