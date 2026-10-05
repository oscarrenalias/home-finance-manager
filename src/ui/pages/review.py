"""Review page — pending classification decisions.

Route: /review
On-load events: ReviewState.load_review_queue, ReviewState.load_categories

Purpose
-------
Shows every transaction that has no *accepted* Classification record and lets
the operator manually assign a type, category, and optional merchant name, or
confirm/dismiss a suggested internal-transfer match between accounts.

ReviewState public vars
-----------------------
queue_items           list[dict]  All unclassified transactions as plain dicts
                                  (serialised by _transaction_to_dict).
queue_count           int         Length of queue_items; decremented on each
                                  confirm/transfer action.
selected_transaction_id str       ID of the row currently open in the
                                  classification panel ("" when none selected).
selected_type         str         Type chosen in the type selector.
selected_category     str         Category ID chosen in the category selector.
selected_merchant     str         Merchant name typed into the merchant input.
transfer_candidate    dict        Highest-confidence transfer counterpart for the
                                  selected transaction (empty dict when absent).
                                  Keys: id, date, account_name, display_amount.
categories            list[dict]  Category options loaded from
                                  config/categories.yaml; each has "id" and "name".
error_message         str         Non-empty when the last DB operation failed.
page_index            int         0-based index into paginated_items pages.

Computed vars (rx.var)
----------------------
paginated_items       Current page slice of queue_items (_PAGE_SIZE = 20).
total_pages           Ceiling division of queue length by _PAGE_SIZE.
has_prev_page / has_next_page  Pagination boundary flags.
page_info             Human-readable "Page N of M" string.
queue_is_empty        True when queue_items is empty.
has_transfer_candidate  True when transfer_candidate is non-empty.
confirm_enabled       True when enough inputs are filled to submit: type is set,
                      and category is set (unless the selected type does not use
                      categories: internal_transfer, contribution, income,
                      external_transfer, unknown).

Key event handlers
------------------
load_review_queue     Query all transactions lacking an accepted Classification,
                      populate queue_items, reset page_index to 0.
load_categories       Read CATEGORIES singleton into self.categories.
select_transaction    Set selected_transaction_id, clear form fields, and load
                      the top transfer candidate via _load_transfer_candidate.
confirm_classification  Write a manual-override Classification record via
                      classification_service.manual_override, then remove the
                      transaction from queue_items. See A11 note below.
confirm_transfer      Link both sides of a transfer pair via
                      transfer_service.confirm_transfer, remove both rows from
                      queue_items, and decrement queue_count by 1.
dismiss_transfer      Clear transfer_candidate without touching the queue or DB.
skip_classification   Deselect the current transaction without saving anything.
next_page / prev_page Increment/decrement page_index within bounds.
set_selected_type / set_selected_category / set_selected_merchant
                      Thin setters wired to the form controls.

A11 — manual overrides survive reimport
----------------------------------------
confirm_classification calls classification_service.manual_override which writes
a Classification row with source="manual" and review_state="accepted". On
reimport, load_review_queue filters on the *absence* of an accepted
Classification record (the accepted_subq sub-query in load_review_queue). A
manual override is therefore never overwritten by a reimport or a model rerun,
satisfying acceptance criterion A11.

Transfer matching (transfer_service)
-------------------------------------
When a transaction is selected, _load_transfer_candidate calls
transfer_service.find_transfer_candidates, which ranks counterpart transactions
by amount mirror, date proximity, and account membership. The top candidate is
shown in the transfer suggestion panel. Confirming it calls
transfer_service.confirm_transfer(session, txn_id_a, txn_id_b), which marks
both records as internal_transfer and links them; both rows then disappear from
the review queue.

data-testid inventory
---------------------
review-heading          Page <h1> element.
review-queue-count      Text node showing "<N> items in queue".
review-empty-state      Box displayed when the queue is empty.
review-row              Container box for each transaction row in the list.
review-expand-btn       "›" button inside each row; triggers select_transaction.
review-prev-page        "← Prev" pagination button.
review-next-page        "Next →" pagination button.
type-selector           Radix Select trigger for transaction type.
type-option-<type>      Individual items inside the type selector (e.g.
                        type-option-expense, type-option-internal_transfer).
category-selector       Radix Select trigger for category.
merchant-input          Text input for optional merchant name.
transfer-suggestion     Container box for the transfer match suggestion panel.
confirm-transfer-btn    "Confirm Transfer" button inside the suggestion panel.
dismiss-transfer-btn    "Dismiss" button inside the suggestion panel.
review-error-banner     Error callout in the classification panel.
confirm-btn             "Confirm" button to submit the classification.
skip-btn                "Skip" button to deselect without saving.
"""
from __future__ import annotations

import reflex as rx
from sqlalchemy.orm import joinedload

from config.categories import CATEGORIES
import services.classification_service as classification_service
import services.transfer_service as transfer_service
from services.transfer_service import find_transfer_candidates as _find_transfer_candidates
from storage.database import _get_session_factory
from storage.models import Account, Classification, Transaction
from ui.components import shell

_PAGE_SIZE = 20

_ALL_TYPES: list[str] = [
    "expense",
    "refund",
    "internal_transfer",
    "contribution",
    "income",
    "external_transfer",
    "unknown",
]

_TYPES_WITHOUT_CATEGORY: frozenset[str] = frozenset({
    "internal_transfer",
    "contribution",
    "income",
    "external_transfer",
    "unknown",
})

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
    selected_type: str = ""
    selected_category: str = ""
    selected_merchant: str = ""
    transfer_candidate: dict = {}

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

    @rx.var
    def has_transfer_candidate(self) -> bool:
        return bool(self.transfer_candidate)

    @rx.var
    def confirm_enabled(self) -> bool:
        if not self.selected_type:
            return False
        if self.selected_type in _TYPES_WITHOUT_CATEGORY:
            return True
        return bool(self.selected_category)

    @rx.event
    def next_page(self) -> None:
        if self.has_next_page:
            self.page_index += 1

    @rx.event
    def prev_page(self) -> None:
        if self.has_prev_page:
            self.page_index -= 1

    def _load_transfer_candidate(self, txn_id: str) -> None:
        """Fetch the highest-confidence transfer candidate for txn_id; clears state if none found."""
        if not txn_id:
            self.transfer_candidate = {}
            return
        session = _get_session_factory()()
        try:
            candidates = _find_transfer_candidates(session, txn_id)
            if candidates:
                c = candidates[0]
                acct = session.get(Account, c.account_id)
                account_name = acct.name if acct else c.account_id
                amount_cents = c.amount_cents
                sign = "-" if amount_cents < 0 else ""
                abs_cents = abs(amount_cents)
                display_amount = f"{sign}€{abs_cents // 100}.{abs_cents % 100:02d}"
                self.transfer_candidate = {
                    "id": c.id,
                    "date": c.date.isoformat(),
                    "account_name": account_name,
                    "display_amount": display_amount,
                }
            else:
                self.transfer_candidate = {}
        except Exception:
            self.transfer_candidate = {}
        finally:
            session.close()

    @rx.event
    def select_transaction(self, txn_id: str) -> None:
        self.selected_transaction_id = txn_id
        self.error_message = ""
        # Pre-populate from the LLM suggestion already stored in queue_items.
        item = next((i for i in self.queue_items if i["id"] == txn_id), {})
        self.selected_type = item.get("transaction_type", "") if item.get("transaction_type", "unknown") != "unknown" else ""
        self.selected_category = item.get("category_id", "")
        self.selected_merchant = item.get("merchant", "")
        self._load_transfer_candidate(txn_id)

    def _decrement_queue(self, txn_id: str) -> None:
        """Remove txn_id from queue_items and decrement queue_count by exactly 1."""
        self.queue_items = [item for item in self.queue_items if item["id"] != txn_id]
        self.queue_count = max(0, self.queue_count - 1)

    @rx.event
    def confirm_classification(self) -> None:
        """Apply a manual override for the selected transaction, then remove from queue."""
        txn_id = self.selected_transaction_id
        if not txn_id:
            return
        item_map = {i["id"]: i for i in self.queue_items}
        item = item_map.get(txn_id, {})
        txn_type = self.selected_type or item.get("transaction_type", "unknown")
        category_id = self.selected_category or None
        merchant = self.selected_merchant or None
        session = _get_session_factory()()
        try:
            classification_service.manual_override(
                session=session,
                transaction_id=txn_id,
                transaction_type=txn_type,
                category_id=category_id,
                merchant=merchant,
            )
            session.commit()
            self._decrement_queue(txn_id)
            self.selected_transaction_id = ""
            self.selected_type = ""
            self.selected_category = ""
            self.selected_merchant = ""
            self.error_message = ""
        except Exception as exc:
            self.error_message = str(exc)
            session.rollback()
        finally:
            session.close()

    @rx.event
    def confirm_transfer(self) -> None:
        """Confirm the suggested transfer pair via transfer_service."""
        txn_id_a = self.selected_transaction_id
        txn_id_b = self.transfer_candidate.get("id", "")
        if not txn_id_a or not txn_id_b:
            return
        session = _get_session_factory()()
        try:
            transfer_service.confirm_transfer(session, txn_id_a, txn_id_b)
            session.commit()
            self.queue_items = [
                item for item in self.queue_items
                if item["id"] not in {txn_id_a, txn_id_b}
            ]
            self.queue_count = max(0, self.queue_count - 1)
            self.selected_transaction_id = ""
            self.transfer_candidate = {}
            self.error_message = ""
        except Exception as exc:
            self.error_message = str(exc)
            session.rollback()
        finally:
            session.close()

    @rx.event
    def dismiss_transfer(self) -> None:
        """Clear the transfer candidate without changing queue or classification."""
        self.transfer_candidate = {}

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
        self._load_transfer_candidate(self.selected_transaction_id)

    @rx.event
    def load_categories(self) -> None:
        """Populate categories from the config/categories.yaml singleton."""
        self.categories = [{"id": c.id, "name": c.name} for c in CATEGORIES]

    @rx.event
    def set_selected_type(self, val: str) -> None:
        self.selected_type = val

    @rx.event
    def set_selected_category(self, val: str) -> None:
        self.selected_category = val

    @rx.event
    def set_selected_merchant(self, val: str) -> None:
        self.selected_merchant = val

    @rx.event
    def skip_classification(self) -> None:
        self.selected_transaction_id = ""
        self.selected_type = ""
        self.selected_category = ""
        self.selected_merchant = ""
        self.error_message = ""
        self.transfer_candidate = {}


def _review_row(item: dict) -> rx.Component:
    is_selected = ReviewState.selected_transaction_id == item["id"]
    return rx.box(
        # Clickable row header — entire strip opens/closes the panel
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
            rx.cond(is_selected, rx.text("▾", size="2"), rx.text("›", size="2")),
            align="center",
            gap="0.75em",
            width="100%",
            cursor="pointer",
            on_click=ReviewState.select_transaction(item["id"]),
            data_testid="review-expand-btn",
        ),
        # Inline classification panel — only visible for the selected row
        rx.cond(
            is_selected,
            _classification_panel(),
            rx.fragment(),
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


def _transfer_suggestion_panel() -> rx.Component:
    return rx.cond(
        ReviewState.has_transfer_candidate,
        rx.box(
            rx.text("Suggested transfer match", weight="bold", size="3", margin_bottom="0.5em"),
            rx.flex(
                rx.text(ReviewState.transfer_candidate["date"], size="2", color_scheme="gray"),
                rx.text(ReviewState.transfer_candidate["account_name"], size="2", flex="1"),
                rx.text(
                    ReviewState.transfer_candidate["display_amount"],
                    size="2",
                    font_family="monospace",
                ),
                gap="1em",
                align="center",
                margin_bottom="0.75em",
            ),
            rx.flex(
                rx.button(
                    "Confirm Transfer",
                    color_scheme="blue",
                    on_click=ReviewState.confirm_transfer,
                    data_testid="confirm-transfer-btn",
                ),
                rx.button(
                    "Dismiss",
                    variant="soft",
                    color_scheme="gray",
                    on_click=ReviewState.dismiss_transfer,
                    data_testid="dismiss-transfer-btn",
                ),
                gap="0.75em",
            ),
            data_testid="transfer-suggestion",
            padding="1em",
            border="1px solid var(--blue-6)",
            border_radius="0.5em",
            background="var(--blue-2)",
            margin_top="1em",
            width="100%",
        ),
        rx.fragment(),
    )


def _classification_panel() -> rx.Component:
    return rx.cond(
        ReviewState.selected_transaction_id != "",
        rx.box(
            rx.cond(
                ReviewState.error_message != "",
                rx.box(
                    rx.text(ReviewState.error_message, color="red", size="2"),
                    padding="0.75em",
                    border="1px solid var(--red-6)",
                    border_radius="0.5em",
                    margin_bottom="1em",
                    data_testid="review-error-banner",
                ),
                rx.fragment(),
            ),
            rx.text("Classify Transaction", weight="bold", size="4", margin_bottom="0.75em"),
            rx.vstack(
                rx.text("Type", size="2", weight="medium"),
                rx.select.root(
                    rx.select.trigger(
                        placeholder="Select type",
                        data_testid="type-selector",
                        width="100%",
                    ),
                    rx.select.content(
                        *[rx.select.item(t, value=t, data_testid=f"type-option-{t}") for t in _ALL_TYPES]
                    ),
                    on_change=ReviewState.set_selected_type,
                    value=ReviewState.selected_type,
                    width="100%",
                ),
                gap="0.25em",
                width="100%",
                align_items="start",
                margin_bottom="0.75em",
            ),
            rx.vstack(
                rx.text("Category", size="2", weight="medium"),
                rx.select.root(
                    rx.select.trigger(
                        placeholder="Select category",
                        data_testid="category-selector",
                        width="100%",
                    ),
                    rx.select.content(
                        rx.foreach(
                            ReviewState.categories,
                            lambda c: rx.select.item(c["name"], value=c["id"]),
                        )
                    ),
                    on_change=ReviewState.set_selected_category,
                    value=ReviewState.selected_category,
                    width="100%",
                ),
                gap="0.25em",
                width="100%",
                align_items="start",
                margin_bottom="0.75em",
            ),
            rx.vstack(
                rx.text("Merchant", size="2", weight="medium"),
                rx.input(
                    placeholder="Merchant name (optional)",
                    value=ReviewState.selected_merchant,
                    on_change=ReviewState.set_selected_merchant,
                    data_testid="merchant-input",
                    width="100%",
                ),
                gap="0.25em",
                width="100%",
                align_items="start",
                margin_bottom="1em",
            ),
            _transfer_suggestion_panel(),
            rx.flex(
                rx.button(
                    "Confirm",
                    color_scheme="green",
                    on_click=ReviewState.confirm_classification,
                    disabled=~ReviewState.confirm_enabled,
                    data_testid="confirm-btn",
                ),
                rx.button(
                    "Skip",
                    variant="soft",
                    color_scheme="gray",
                    on_click=ReviewState.skip_classification,
                    data_testid="skip-btn",
                ),
                gap="0.75em",
            ),
            padding="1.25em",
            border="1px solid var(--accent-6)",
            border_radius="0.5em",
            background="var(--accent-2)",
            width="100%",
            margin_top="1.5em",
        ),
        rx.fragment(),
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
