"""Transaction query service — data structures, pagination constants, and shared classification helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from config.categories import CATEGORY_MAP
from storage.models import Account, Classification, Transaction


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


def list_transactions(
    session: Session,
    filters: LedgerFilters,
    sort: str = "date_desc",
    page: int = 0,
    page_size: int = 50,
) -> LedgerPage:
    """Return a paginated, filtered, sorted page of executed transactions.

    All filtering happens in SQL via bound parameters — no full-table loads.
    page_size must be one of PAGE_SIZES. sort must be one of the four recognised
    sort keys. Raises ValueError for invalid inputs.
    """
    if page_size not in PAGE_SIZES:
        raise ValueError(f"page_size must be one of {PAGE_SIZES}, got {page_size}")

    _VALID_SORTS = {"date_desc", "date_asc", "amount_desc", "amount_asc"}
    if sort not in _VALID_SORTS:
        raise ValueError(f"sort must be one of {_VALID_SORTS}, got {sort!r}")

    # Correlated scalar subquery: ID of the most recent non-rejected Classification
    # for the current Transaction row.  Returns NULL when no such row exists.
    active_cls_id_sq = (
        select(Classification.id)
        .where(
            Classification.transaction_id == Transaction.id,
            Classification.review_state != "rejected",
        )
        .order_by(Classification.created_at.desc(), Classification.id.desc())
        .limit(1)
        .correlate(Transaction)
        .scalar_subquery()
    )

    ActiveCls = aliased(Classification, name="active_cls")

    # Base query: Transaction INNER JOIN Account, LEFT OUTER JOIN active classification
    q = (
        session.query(Transaction, Account, ActiveCls)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(ActiveCls, ActiveCls.id == active_cls_id_sq)
    )

    # ------------------------------------------------------------------ filters
    predicates = []

    if filters.date_from is not None:
        predicates.append(Transaction.date >= filters.date_from)
    if filters.date_to is not None:
        predicates.append(Transaction.date <= filters.date_to)

    if filters.account_ids:
        predicates.append(Transaction.account_id.in_(filters.account_ids))

    if filters.transaction_types:
        # Prefer the active classification type; fall back to Transaction.transaction_type
        # for transactions that have never been classified.
        predicates.append(
            or_(
                ActiveCls.transaction_type.in_(filters.transaction_types),
                and_(
                    ActiveCls.id.is_(None),
                    Transaction.transaction_type.in_(filters.transaction_types),
                ),
            )
        )

    if filters.category_ids:
        normal_cats = [c for c in filters.category_ids if c != "__none__"]
        has_none_cat = "__none__" in filters.category_ids
        cat_conds: list = []
        if normal_cats:
            cat_conds.append(
                or_(
                    ActiveCls.category_id.in_(normal_cats),
                    and_(
                        ActiveCls.id.is_(None),
                        Transaction.category_id.in_(normal_cats),
                    ),
                )
            )
        if has_none_cat:
            # "__none__" matches transactions with no category in their active classification
            # (or no classification at all and no category on the transaction itself).
            cat_conds.append(
                or_(
                    and_(ActiveCls.id.isnot(None), ActiveCls.category_id.is_(None)),
                    and_(ActiveCls.id.is_(None), Transaction.category_id.is_(None)),
                )
            )
        if cat_conds:
            predicates.append(or_(*cat_conds))

    if filters.review_states:
        normal_states = [s for s in filters.review_states if s != "unclassified"]
        has_unclassified = "unclassified" in filters.review_states
        state_conds: list = []
        if normal_states:
            state_conds.append(ActiveCls.review_state.in_(normal_states))
        if has_unclassified:
            # "unclassified" = no non-rejected Classification row exists at all
            state_conds.append(ActiveCls.id.is_(None))
        if state_conds:
            predicates.append(or_(*state_conds))

    if filters.search:
        # Escape % and _ so they are treated as literal characters, not wildcards
        escaped = (
            filters.search
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        predicates.append(
            or_(
                Transaction.display_text.ilike(pattern, escape="\\"),
                Transaction.merchant.ilike(pattern, escape="\\"),
                Transaction.note.ilike(pattern, escape="\\"),
            )
        )

    if not filters.include_rejected_deleted:
        predicates.append(Transaction.status.notin_(["Rejected", "Deleted"]))

    if predicates:
        q = q.filter(and_(*predicates))

    # ----------------------------------------------------------- count (pre-page)
    total_count: int = q.with_entities(func.count(Transaction.id)).scalar() or 0

    # -------------------------------------------------------------------- sort
    _sort_clauses = {
        "date_desc": [Transaction.date.desc(), Transaction.created_at.desc(), Transaction.id.desc()],
        "date_asc":  [Transaction.date.asc(),  Transaction.created_at.asc(),  Transaction.id.asc()],
        "amount_desc": [Transaction.amount_cents.desc(), Transaction.created_at.desc(), Transaction.id.desc()],
        "amount_asc":  [Transaction.amount_cents.asc(),  Transaction.created_at.asc(),  Transaction.id.asc()],
    }
    q = q.order_by(*_sort_clauses[sort])

    # --------------------------------------------------------------- paginate
    rows_data = q.offset(page * page_size).limit(page_size).all()

    # --------------------------------------------------------- build LedgerRow
    def _resolve_category_name(cat_id: Optional[str]) -> Optional[str]:
        if cat_id and cat_id in CATEGORY_MAP:
            return CATEGORY_MAP[cat_id].name
        return None

    rows = [
        LedgerRow(
            id=tx.id,
            date=tx.date,
            account_name=acct.name,
            display_text=tx.display_text,
            amount_cents=tx.amount_cents,
            currency=tx.currency,
            status=tx.status,
            transaction_type=cls.transaction_type if cls else tx.transaction_type,
            category_id=cls.category_id if cls else tx.category_id,
            category_name=_resolve_category_name(
                cls.category_id if cls else tx.category_id
            ),
            merchant=cls.merchant if cls else tx.merchant,
            review_state=cls.review_state if cls else None,
            classification_source=cls.source if cls else tx.classification_source,
            has_note=bool(tx.note),
        )
        for tx, acct, cls in rows_data
    ]

    return LedgerPage(rows=rows, total_count=total_count, page=page, page_size=page_size)
