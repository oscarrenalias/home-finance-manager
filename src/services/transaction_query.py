"""Transaction query service — data structures, pagination constants, and shared classification helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from storage.models import Classification


PAGE_SIZES = (20, 50, 100)


@dataclass(frozen=True)
class LedgerFilters:
    date_from: Optional[date] = None
    date_to: Optional[date] = None          # inclusive upper bound
    account_ids: tuple[str, ...] = ()
    transaction_types: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()      # "__none__" matches uncategorised
    review_states: tuple[str, ...] = ()     # "accepted" | "needs_review" | "unclassified"
    search: str = ""                        # case-insensitive substring on display_text, merchant, note
    include_rejected_deleted: bool = False  # bank status Rejected/Deleted hidden by default


@dataclass(frozen=True)
class LedgerRow:
    id: str
    date: date
    account_name: str
    display_text: str
    amount_cents: int
    currency: str
    status: str
    transaction_type: Optional[str]
    category_id: Optional[str]
    category_name: Optional[str]
    merchant: Optional[str]
    review_state: Optional[str]
    classification_source: Optional[str]
    has_note: bool


@dataclass(frozen=True)
class LedgerPage:
    rows: list[LedgerRow]
    total_count: int
    page: int
    page_size: int


@dataclass(frozen=True)
class SourceObservationRow:
    batch_filename: str
    import_date: Optional[date]
    row_number: int
    raw_date: str
    raw_text: str
    raw_amount: str
    raw_balance: Optional[str]
    raw_status: str
    raw_category: str
    raw_subcategory: str


@dataclass(frozen=True)
class ClassificationRecord:
    transaction_type: str
    category_id: Optional[str]
    merchant: Optional[str]
    source: str
    review_state: str
    rationale: Optional[str]
    model_version: Optional[str]
    rule_version: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class TransferLinkInfo:
    counterpart_date: Optional[date]
    account_name: Optional[str]
    amount_cents: Optional[int]
    link_state: str  # confirmed_by value: "manual" | "rule"


@dataclass(frozen=True)
class TransactionDetail(LedgerRow):
    """Full transaction detail including history, source observations, and notes.

    Inherits all LedgerRow fields. Additional fields have defaults so that
    construction from a LedgerRow is straightforward — callers must still pass
    all required LedgerRow fields positionally or by keyword.
    """

    source_rows: list[SourceObservationRow] = field(default_factory=list)
    classification_history: list[ClassificationRecord] = field(default_factory=list)
    transfer_links: list[TransferLinkInfo] = field(default_factory=list)
    note: Optional[str] = None
    note_updated_at: Optional[datetime] = None


def get_current_classification(
    session: Session, transaction_id: str
) -> Optional[Classification]:
    """Return the most-recent non-rejected Classification row for a transaction, or None.

    Mirrors the rule used in review.py::_transaction_to_dict so that the Review
    page and the ledger always agree on which classification is active.
    """
    return (
        session.query(Classification)
        .filter(
            Classification.transaction_id == transaction_id,
            Classification.review_state != "rejected",
        )
        .order_by(Classification.created_at.desc())
        .first()
    )
