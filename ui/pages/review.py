"""Review page — pending classification decisions."""
from __future__ import annotations

import reflex as rx
from sqlalchemy.orm import joinedload

from config.categories import CATEGORIES
from storage.database import _get_session_factory
from storage.models import Classification, Transaction
from ui.components import shell


def _transaction_to_dict(txn: Transaction) -> dict:
    """Convert a Transaction ORM row to a serialisable plain dict.

    Accesses txn.classifications while the session is still open (caller's
    responsibility). The most recent non-rejected Classification drives the
    displayed type and category; falls back to the Transaction's own fields.
    """
    # Most-recent non-rejected classification (covers both needs_review and accepted
    # rows that we still want to surface for in-flight edits).
    pending_cls = None
    for cls in sorted(txn.classifications, key=lambda c: c.created_at, reverse=True):
        if cls.review_state != "rejected":
            pending_cls = cls
            break

    return {
        "id": txn.id,
        "date": txn.date.isoformat(),
        "display_text": txn.display_text,
        "amount_cents": txn.amount_cents,
        "currency": txn.currency,
        "status": txn.status,
        "transaction_type": (
            pending_cls.transaction_type
            if pending_cls
            else (txn.transaction_type or "unknown")
        ),
        "category_id": (pending_cls.category_id or "") if pending_cls else "",
        "merchant": (pending_cls.merchant or "") if pending_cls else "",
        "created_at": txn.created_at.isoformat(),
        "account_id": txn.account_id,
        # Classification metadata for the confirm/skip panel
        "cls_id": pending_cls.id if pending_cls else "",
        "cls_source": pending_cls.source if pending_cls else "",
        "cls_rationale": (pending_cls.rationale or "") if pending_cls else "",
        "cls_review_state": pending_cls.review_state if pending_cls else "",
    }


class ReviewState(rx.State):
    queue_items: list[dict] = []
    selected_transaction_id: str = ""
    queue_count: int = 0
    categories: list[dict] = []
    error_message: str = ""

    @rx.event
    def load_review_queue(self) -> None:
        """Fetch transactions that have no accepted Classification and store as dicts."""
        session = _get_session_factory()()
        try:
            accepted_subq = (
                session.query(Classification.id)
                .filter(
                    Classification.transaction_id == Transaction.id,
                    Classification.review_state == "accepted",
                )
                .exists()
            )
            txns = (
                session.query(Transaction)
                .options(joinedload(Transaction.classifications))
                .filter(~accepted_subq)
                .order_by(Transaction.date.desc(), Transaction.created_at.desc())
                .all()
            )
            items = [_transaction_to_dict(t) for t in txns]
            self.error_message = ""
        except Exception as exc:
            self.error_message = str(exc)
            items = []
        finally:
            session.close()

        self.queue_items = items
        self.queue_count = len(items)

    @rx.event
    def load_categories(self) -> None:
        """Populate categories from the config/categories.yaml singleton."""
        self.categories = [{"id": c.id, "name": c.name} for c in CATEGORIES]


@rx.page(
    route="/review",
    title="Review | Home Finance",
    on_load=[ReviewState.load_review_queue, ReviewState.load_categories],
)
def review() -> rx.Component:
    return shell(
        rx.heading("Review", size="7", data_testid="review-heading"),
        rx.text(
            "Category review queue — coming soon.",
            color_scheme="gray",
            data_testid="review-queue-placeholder",
        ),
    )
