"""Review page — pending classification decisions."""
from __future__ import annotations

import reflex as rx
from sqlalchemy.orm import joinedload

from config.categories import CATEGORIES
from storage.database import _get_session_factory
from storage.models import Classification, Transaction, TransferLink
from ui.components import shell

_PAGE_SIZE = 20

_TYPE_COLORS: dict[str, str] = {
    "expense": "red",
    "refund": "green",
    "internal_transfer": "blue",
    "contribution": "teal",
    "income": "green",
    "external_transfer": "orange",
}


def _transaction_to_dict(txn: Transaction) -> dict:
    """Convert a Transaction ORM row to a serialisable plain dict.

    Accesses txn.classifications while the session is still open (caller's
    responsibility). The most recent non-rejected Classification drives the
    displayed type and category; falls back to the Transaction's own fields.
    """
    pending_cls = None
    for cls in sorted(txn.classifications, key=lambda c: c.created_at, reverse=True):
        if cls.review_state != "rejected":
            pending_cls = cls
            break

    txn_type = (
        (pending_cls.transaction_type if pending_cls else None)
        or txn.transaction_type
        or "unknown"
    )

    amount_cents = txn.amount_cents
    sign = "-" if amount_cents < 0 else ""
    abs_cents = abs(amount_cents)
    display_amount = f"{sign}€{abs_cents // 100}.{abs_cents % 100:02d}"

    raw_text = txn.display_text or ""
    display_description = (raw_text[:50] + "…") if len(raw_text) > 50 else raw_text

    return {
        "id": txn.id,
        "date": txn.date.isoformat(),
        "display_text": txn.display_text,
        "display_description": display_description,
        "display_amount": display_amount,
        "amount_cents": txn.amount_cents,
        "currency": txn.currency,
        "status": txn.status,
        "transaction_type": txn_type,
        "type_label": txn_type if txn_type != "unknown" else "unclassified",
        "type_color": _TYPE_COLORS.get(txn_type, "gray"),
        "category_id": (pending_cls.category_id or "") if pending_cls else "",
        "merchant": (pending_cls.merchant or "") if pending_cls else "",
        "created_at": txn.created_at.isoformat(),
        "account_id": txn.account_id,
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
    page_index: int = 0

    @rx.var
    def paginated_items(self) -> list[dict]:
        start = self.page_index * _PAGE_SIZE
        return self.queue_items[start : start + _PAGE_SIZE]

    @rx.var
    def total_pages(self) -> int:
        if not self.queue_items:
            return 1
        return (len(self.queue_items) + _PAGE_SIZE - 1) // _PAGE_SIZE

    @rx.var
    def has_prev_page(self) -> bool:
        return self.page_index > 0

    @rx.var
    def has_next_page(self) -> bool:
        return (self.page_index + 1) * _PAGE_SIZE < len(self.queue_items)

    @rx.var
    def page_info(self) -> str:
        return f"Page {self.page_index + 1} of {self.total_pages}"

    @rx.var
    def queue_is_empty(self) -> bool:
        return len(self.queue_items) == 0

    @rx.event
    def next_page(self) -> None:
        if self.has_next_page:
            self.page_index += 1

    @rx.event
    def prev_page(self) -> None:
        if self.has_prev_page:
            self.page_index -= 1

    @rx.event
    def select_transaction(self, txn_id: str) -> None:
        self.selected_transaction_id = txn_id

    def _decrement_queue(self, txn_id: str) -> None:
        """Remove txn_id from queue_items and decrement queue_count by exactly 1."""
        self.queue_items = [item for item in self.queue_items if item["id"] != txn_id]
        self.queue_count = max(0, self.queue_count - 1)

    @rx.event
    def confirm_classification(self, txn_id: str, cls_id: str, transaction_type: str, category_id: str) -> None:
        """Accept an existing classification or create a manual one, then remove from queue."""
        session = _get_session_factory()()
        try:
            if cls_id:
                cls = session.get(Classification, cls_id)
                if cls:
                    cls.review_state = "accepted"
            else:
                cls = Classification(
                    transaction_id=txn_id,
                    source="manual",
                    transaction_type=transaction_type or "unknown",
                    category_id=category_id or None,
                    review_state="accepted",
                )
                session.add(cls)
            session.commit()
            self._decrement_queue(txn_id)
        except Exception as exc:
            self.error_message = str(exc)
            session.rollback()
        finally:
            session.close()

    @rx.event
    def confirm_transfer(self, txn_id_a: str, txn_id_b: str) -> None:
        """Confirm a matched transfer pair. Both sides are accepted; counts as one queue decrement."""
        session = _get_session_factory()()
        try:
            a_id, b_id = (txn_id_a, txn_id_b) if txn_id_a < txn_id_b else (txn_id_b, txn_id_a)
            link = TransferLink(
                transaction_a_id=a_id,
                transaction_b_id=b_id,
                confirmed_by="manual",
            )
            session.add(link)
            for tid in (txn_id_a, txn_id_b):
                session.add(Classification(
                    transaction_id=tid,
                    source="manual",
                    transaction_type="internal_transfer",
                    review_state="accepted",
                ))
            session.commit()
            # Remove both items from the in-memory list but count as a single queue action.
            self.queue_items = [
                item for item in self.queue_items
                if item["id"] not in {txn_id_a, txn_id_b}
            ]
            self.queue_count = max(0, self.queue_count - 1)
        except Exception as exc:
            self.error_message = str(exc)
            session.rollback()
        finally:
            session.close()

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
        self.page_index = 0

    @rx.event
    def load_categories(self) -> None:
        """Populate categories from the config/categories.yaml singleton."""
        self.categories = [{"id": c.id, "name": c.name} for c in CATEGORIES]


def _review_row(item: dict) -> rx.Component:
    return rx.box(
        rx.flex(
            rx.text(
                item["date"],
                size="2",
                color_scheme="gray",
                width="100px",
                flex_shrink="0",
            ),
            rx.text(
                item["display_description"],
                size="2",
                flex="1",
                min_width="0",
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
            ),
            rx.text(
                item["display_amount"],
                size="2",
                flex_shrink="0",
                min_width="90px",
                text_align="right",
                font_family="monospace",
            ),
            rx.badge(
                item["type_label"],
                color_scheme=item["type_color"],
                variant="soft",
                size="1",
            ),
            rx.button(
                "›",
                variant="ghost",
                size="1",
                data_testid="review-expand-btn",
                on_click=ReviewState.select_transaction(item["id"]),
            ),
            align="center",
            gap="0.75em",
            width="100%",
        ),
        data_testid="review-row",
        padding="0.6em 1em",
        border_bottom="1px solid var(--gray-4)",
        _hover={"background": "var(--accent-2)"},
    )


def _pagination_controls() -> rx.Component:
    return rx.flex(
        rx.button(
            "← Prev",
            variant="soft",
            size="2",
            on_click=ReviewState.prev_page,
            disabled=~ReviewState.has_prev_page,
            data_testid="review-prev-page",
        ),
        rx.text(ReviewState.page_info, size="2", color_scheme="gray"),
        rx.button(
            "Next →",
            variant="soft",
            size="2",
            on_click=ReviewState.next_page,
            disabled=~ReviewState.has_next_page,
            data_testid="review-next-page",
        ),
        align="center",
        justify="center",
        gap="1em",
        padding="1em 0",
    )


def _queue_list() -> rx.Component:
    return rx.box(
        rx.cond(
            ReviewState.error_message != "",
            rx.box(
                rx.text(ReviewState.error_message, color="red", size="2"),
                padding="0.75em",
                border="1px solid var(--red-6)",
                border_radius="0.5em",
                margin_bottom="1em",
            ),
            rx.fragment(),
        ),
        rx.text(
            ReviewState.queue_count,
            " items in queue",
            size="2",
            color_scheme="gray",
            margin_bottom="1em",
            data_testid="review-queue-count",
        ),
        rx.cond(
            ReviewState.queue_is_empty,
            rx.box(
                rx.text(
                    "No transactions pending review.",
                    size="2",
                    color_scheme="gray",
                ),
                padding="2em",
                text_align="center",
                data_testid="review-empty-state",
            ),
            rx.box(
                rx.foreach(ReviewState.paginated_items, _review_row),
                border="1px solid var(--gray-4)",
                border_radius="0.5em",
                overflow="hidden",
            ),
        ),
        rx.cond(
            ReviewState.total_pages > 1,
            _pagination_controls(),
            rx.fragment(),
        ),
        width="100%",
    )


@rx.page(
    route="/review",
    title="Review | Home Finance",
    on_load=[ReviewState.load_review_queue, ReviewState.load_categories],
)
def review() -> rx.Component:
    return shell(
        rx.heading("Review", size="7", margin_bottom="1em", data_testid="review-heading"),
        _queue_list(),
    )
