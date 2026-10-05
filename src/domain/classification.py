"""Pure classification functions — no database access, no Reflex or SQLAlchemy imports.

Public API:
- match_rules: find the highest-priority confirmed rule matching a transaction's text/merchant
- resolve_active: collapse a list of Classification rows to a single effective result
- affects_spending: gate whether a classification contributes to spending reports

Contract:
- No side effects. All functions are pure; callers own all I/O and session handling.
- Structural typing via Protocol lets callers pass ORM rows without importing ORM here.

Key invariants:
- Precedence: manual > rule > llm. Within a tier, the later created_at wins.
- A confirmed TransferLink overrides all stored classifications — resolve_active returns
  an internal_transfer result regardless of what Classification rows exist.
- Only "expense" and "refund" types count toward spending; all other types are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


# ---------------------------------------------------------------------------
# Structural protocols — callers pass ORM rows; these define the required shape
# ---------------------------------------------------------------------------

class ClassificationRule(Protocol):
    priority: int
    pattern_type: str    # "text_contains" | "merchant_exact" | "merchant_contains"
    pattern_value: str
    is_confirmed: bool
    transaction_type: str
    category_id: str | None
    merchant: str | None


class Classification(Protocol):
    source: str           # "manual" | "rule" | "llm"
    transaction_type: str
    category_id: str | None
    merchant: str | None
    review_state: str     # "accepted" | "needs_review" | "rejected"
    created_at: datetime


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@dataclass
class _TransferClassification:
    """Synthetic classification returned when a confirmed transfer link exists."""
    transaction_type: str = "internal_transfer"
    source: str = "rule"
    category_id: str | None = None
    merchant: str | None = None
    review_state: str = "accepted"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Source tier: lower number = higher precedence
_SOURCE_TIER: dict[str, int] = {"manual": 0, "rule": 1, "llm": 2}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def match_rules(
    display_text: str,
    merchant: str | None,
    rules: list[ClassificationRule],
) -> ClassificationRule | None:
    """Return highest-priority confirmed rule matching display_text/merchant, or None.

    Pattern types:
    - text_contains:      case-insensitive substring match against display_text
    - merchant_contains:  case-insensitive substring match against merchant
    - merchant_exact:     case-insensitive exact match against merchant
    """
    text_lower = display_text.lower()
    merchant_lower = merchant.lower() if merchant else ""

    best: ClassificationRule | None = None
    for rule in rules:
        if not rule.is_confirmed:
            continue

        pattern = rule.pattern_value.lower()
        if rule.pattern_type == "text_contains":
            matched = pattern in text_lower
        elif rule.pattern_type == "merchant_contains":
            matched = bool(merchant_lower) and pattern in merchant_lower
        elif rule.pattern_type == "merchant_exact":
            matched = bool(merchant_lower) and pattern == merchant_lower
        else:
            matched = False

        if matched and (best is None or rule.priority > best.priority):
            best = rule

    return best


def resolve_active(
    classifications: list[Classification],
    has_transfer_link: bool,
) -> Classification | None:
    """Return the single effective classification per precedence rules, or None.

    Precedence: manual > rule > llm. Within a tier, latest created_at wins.
    If has_transfer_link is True, the result is always an internal_transfer
    classification regardless of stored classifications.
    """
    if has_transfer_link:
        transfer = [c for c in classifications if c.transaction_type == "internal_transfer"]
        if transfer:
            return max(transfer, key=lambda c: c.created_at)
        return _TransferClassification()

    if not classifications:
        return None

    def _sort_key(c: Classification) -> tuple[int, float]:
        tier = _SOURCE_TIER.get(c.source, 99)
        ts = c.created_at.timestamp() if c.created_at else 0.0
        return (tier, -ts)  # lower tier = higher precedence; higher ts = later = wins

    return min(classifications, key=_sort_key)


def affects_spending(classification: Classification | None) -> bool:
    """Return True only for expense or refund transaction types."""
    if classification is None:
        return False
    return classification.transaction_type in {"expense", "refund"}
