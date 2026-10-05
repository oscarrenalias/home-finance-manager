"""Transactions page — browsable ledger with filters."""
from __future__ import annotations

import json
import urllib.parse
from datetime import date
from urllib.parse import parse_qs

import reflex as rx

from config.categories import CATEGORIES, CATEGORY_MAP
from services import classification_service
from services.transaction_query import (
    LedgerFilters,
    LedgerRow,
    PAGE_SIZES,
    TransactionDetail,
    get_transaction_detail,
    list_transactions,
    set_note,
)
from storage.database import _get_session_factory
from storage.models import Account
from ui.components import shell

_ALL_TYPES: list[str] = [
    "expense",
    "refund",
    "internal_transfer",
    "contribution",
    "income",
    "external_transfer",
    "unknown",
]

_REVIEW_STATE_OPTIONS: list[dict] = [
    {"id": "accepted", "label": "Accepted"},
    {"id": "needs_review", "label": "Needs review"},
    {"id": "unclassified", "label": "Unclassified"},
]

_DEFAULT_PAGE_SIZE = 50

_TYPE_COLORS: dict[str, str] = {
    "expense": "red",
    "refund": "green",
    "internal_transfer": "blue",
    "contribution": "teal",
    "income": "green",
    "external_transfer": "orange",
    "unknown": "gray",
}

_REVIEW_STATE_COLORS: dict[str, str] = {
    "accepted": "green",
    "needs_review": "orange",
    "unclassified": "gray",
}


def _format_amount(amount_cents: int) -> str:
    sign = "-" if amount_cents < 0 else ""
    abs_cents = abs(amount_cents)
    return f"{sign}€{abs_cents // 100}.{abs_cents % 100:02d}"


def _row_to_dict(row: LedgerRow) -> dict:
    txn_type = row.transaction_type or "unknown"
    review_state = row.review_state or ""
    return {
        "id": row.id,
        "date": row.date.strftime("%d.%m.%Y"),
        "account_name": row.account_name,
        "display_text": row.display_text or "",
        "merchant": row.merchant or "",
        "category_name": row.category_name or "",
        "transaction_type": txn_type,
        "type_color": _TYPE_COLORS.get(txn_type, "gray"),
        "review_state": review_state,
        "review_color": _REVIEW_STATE_COLORS.get(review_state, "gray"),
        "amount_cents": row.amount_cents,
        "display_amount": _format_amount(row.amount_cents),
        "is_negative": row.amount_cents < 0,
        "status": row.status,
        "has_note": row.has_note,
    }


class TransactionsState(rx.State):
    # ---- filter state (all default to empty / off) ----
    filter_date_from: str = ""
    filter_date_to: str = ""
    filter_account_ids: list[str] = []
    filter_types: list[str] = []
    filter_category_ids: list[str] = []
    filter_review_states: list[str] = []
    filter_search: str = ""
    filter_show_rejected_deleted: bool = False

    # ---- dropdown options loaded from DB / config ----
    accounts: list[dict] = []
    categories: list[dict] = []

    # ---- results ----
    rows: list[dict] = []
    total_count: int = 0
    current_page: int = 0
    page_size: int = _DEFAULT_PAGE_SIZE
    sort: str = "date_desc"

    # ---- ui ----
    error_message: str = ""

    # ---- detail panel ----
    selected_transaction_id: str = ""
    detail_display_text: str = ""
    detail_source_rows: list[dict] = []
    detail_classification_history: list[dict] = []
    detail_transfer_links: list[dict] = []
    detail_note: str = ""
    detail_note_updated_at: str = ""
    detail_error: str = ""
    details_open: bool = False  # source rows + classification history, collapsed by default

    # ---- classification edit form ----
    edit_type: str = ""
    edit_category_id: str = ""  # "__none__" means no category
    edit_merchant: str = ""
    detail_save_error: str = ""

    # ---- note editor ----
    note_editor_text: str = ""
    note_save_error: str = ""

    # ---- computed vars ----

    @rx.var
    def total_pages(self) -> int:
        if self.total_count == 0:
            return 1
        return (self.total_count + self.page_size - 1) // self.page_size

    @rx.var
    def has_prev_page(self) -> bool:
        return self.current_page > 0

    @rx.var
    def has_next_page(self) -> bool:
        return self.current_page < self.total_pages - 1

    @rx.var
    def page_info(self) -> str:
        if self.total_count == 0:
            return "No transactions"
        start = self.current_page * self.page_size + 1
        end = min((self.current_page + 1) * self.page_size, self.total_count)
        return f"Showing {start}–{end} of {self.total_count}"

    @rx.var
    def page_size_str(self) -> str:
        return str(self.page_size)

    @rx.var
    def has_source_rows(self) -> bool:
        return len(self.detail_source_rows) > 0

    @rx.var
    def has_classification_history(self) -> bool:
        return len(self.detail_classification_history) > 0

    @rx.var
    def has_transfer_links(self) -> bool:
        return len(self.detail_transfer_links) > 0

    @rx.var
    def note_char_count(self) -> int:
        return len(self.note_editor_text)

    @rx.var
    def note_over_limit(self) -> bool:
        return len(self.note_editor_text) > 2000

    @rx.var
    def note_counter_label(self) -> str:
        return f"{len(self.note_editor_text)}/2000"

    @rx.var
    def has_filters(self) -> bool:
        return bool(
            self.filter_date_from
            or self.filter_date_to
            or self.filter_account_ids
            or self.filter_types
            or self.filter_category_ids
            or self.filter_review_states
            or self.filter_search
            or self.filter_show_rejected_deleted
        )

    # ---- private helpers ----

    def _url_qs(self) -> str:
        """Build a URL query string that encodes the current filter/sort/page state."""
        parts: list[tuple[str, str]] = []
        if self.filter_date_from:
            parts.append(("date_from", self.filter_date_from))
        if self.filter_date_to:
            parts.append(("date_to", self.filter_date_to))
        for aid in self.filter_account_ids:
            parts.append(("account", aid))
        for t in self.filter_types:
            parts.append(("type", t))
        for cid in self.filter_category_ids:
            parts.append(("category", cid))
        for rs in self.filter_review_states:
            parts.append(("review", rs))
        if self.filter_search:
            parts.append(("search", self.filter_search))
        if self.filter_show_rejected_deleted:
            parts.append(("show_rejected", "1"))
        if self.sort != "date_desc":
            parts.append(("sort", self.sort))
        if self.current_page != 0:
            parts.append(("page", str(self.current_page)))
        if self.page_size != _DEFAULT_PAGE_SIZE:
            parts.append(("page_size", str(self.page_size)))
        return urllib.parse.urlencode(parts)

    def _push_url(self):
        """Return a call_script action that syncs the browser URL to current state."""
        qs = self._url_qs()
        path = f"/transactions?{qs}" if qs else "/transactions"
        return rx.call_script(f"window.history.replaceState(null, '', {json.dumps(path)})")

    def _parse_url_params(self) -> None:
        """Read URL query params from the current request and apply them to filter state."""
        query = self.router.url.query  # raw query string, no leading '?'
        params = parse_qs(query, keep_blank_values=False)

        self.filter_date_from = params.get("date_from", [""])[0]
        self.filter_date_to = params.get("date_to", [""])[0]
        self.filter_account_ids = params.get("account", [])
        self.filter_types = params.get("type", [])
        self.filter_category_ids = params.get("category", [])
        self.filter_review_states = params.get("review", [])
        self.filter_search = params.get("search", [""])[0]
        self.filter_show_rejected_deleted = params.get("show_rejected", [""])[0] == "1"
        sort = params.get("sort", ["date_desc"])[0]
        self.sort = sort if sort in ("date_asc", "date_desc", "amount_asc", "amount_desc") else "date_desc"
        try:
            self.current_page = max(0, int(params.get("page", ["0"])[0]))
        except ValueError:
            self.current_page = 0
        try:
            ps = int(params.get("page_size", [str(_DEFAULT_PAGE_SIZE)])[0])
            self.page_size = ps if ps in PAGE_SIZES else _DEFAULT_PAGE_SIZE
        except ValueError:
            self.page_size = _DEFAULT_PAGE_SIZE

    def _build_filters(self) -> LedgerFilters:
        return LedgerFilters(
            date_from=date.fromisoformat(self.filter_date_from) if self.filter_date_from else None,
            date_to=date.fromisoformat(self.filter_date_to) if self.filter_date_to else None,
            account_ids=tuple(self.filter_account_ids),
            transaction_types=tuple(self.filter_types),
            category_ids=tuple(self.filter_category_ids),
            review_states=tuple(self.filter_review_states),
            search=self.filter_search,
            include_rejected_deleted=self.filter_show_rejected_deleted,
        )

    def _do_fetch(self) -> None:
        """Open a session, run list_transactions, close the session. Never held open."""
        session = _get_session_factory()()
        try:
            page = list_transactions(
                session,
                filters=self._build_filters(),
                sort=self.sort,
                page=self.current_page,
                page_size=self.page_size,
            )
            self.rows = [_row_to_dict(r) for r in page.rows]
            self.total_count = page.total_count
            self.error_message = ""
        except Exception as exc:
            self.error_message = str(exc)
            self.rows = []
            self.total_count = 0
        finally:
            session.close()

    # ---- detail panel helpers ----

    def _clear_detail(self) -> None:
        self.details_open = False
        self.detail_display_text = ""
        self.detail_source_rows = []
        self.detail_classification_history = []
        self.detail_transfer_links = []
        self.detail_note = ""
        self.detail_note_updated_at = ""
        self.detail_error = ""
        self.edit_type = ""
        self.edit_category_id = ""
        self.edit_merchant = ""
        self.detail_save_error = ""
        self.note_editor_text = ""
        self.note_save_error = ""

    def _apply_detail(self, d: TransactionDetail) -> None:
        """Apply a TransactionDetail to state vars; does not touch selected_transaction_id."""
        self.detail_display_text = d.display_text or ""
        self.detail_source_rows = [
            {
                "batch_filename": sr.batch_filename,
                "import_date": sr.import_date.strftime("%d.%m.%Y") if sr.import_date else "",
                "row_number": str(sr.row_number),
                "raw_date": sr.raw_date,
                "raw_text": sr.raw_text,
                "raw_amount": sr.raw_amount,
                "raw_balance": sr.raw_balance or "",
                "raw_status": sr.raw_status,
                "raw_category": sr.raw_category,
                "raw_subcategory": sr.raw_subcategory,
            }
            for sr in d.source_rows
        ]
        self.detail_classification_history = [
            {
                "transaction_type": cr.transaction_type,
                "category_id": cr.category_id or "",
                "category_name": (
                    CATEGORY_MAP[cr.category_id].name
                    if cr.category_id and cr.category_id in CATEGORY_MAP
                    else ""
                ),
                "merchant": cr.merchant or "",
                "source": cr.source,
                "review_state": cr.review_state,
                "rationale": cr.rationale or "",
                "model_version": cr.model_version or "",
                "rule_version": cr.rule_version or "",
                "created_at": cr.created_at.strftime("%d.%m.%Y %H:%M"),
            }
            for cr in d.classification_history
        ]
        self.detail_transfer_links = [
            {
                "counterpart_date": (
                    tl.counterpart_date.strftime("%d.%m.%Y") if tl.counterpart_date else ""
                ),
                "account_name": tl.account_name or "",
                "display_amount": (
                    _format_amount(tl.amount_cents) if tl.amount_cents is not None else ""
                ),
                "link_state": tl.link_state,
            }
            for tl in d.transfer_links
        ]
        self.detail_note = d.note or ""
        self.detail_note_updated_at = (
            d.note_updated_at.strftime("%d.%m.%Y %H:%M") if d.note_updated_at else ""
        )
        self.note_editor_text = d.note or ""
        # Pre-fill edit form from the active classification values on the detail
        self.edit_type = d.transaction_type or ""
        self.edit_category_id = d.category_id or "__none__"
        self.edit_merchant = d.merchant or ""
        self.detail_save_error = ""

    @rx.event
    def open_detail(self, txn_id: str) -> None:
        """Open the detail panel for txn_id; clicking the same row again closes it."""
        if self.selected_transaction_id == txn_id:
            self.selected_transaction_id = ""
            self._clear_detail()
            return
        self.selected_transaction_id = txn_id
        self._clear_detail()
        session = _get_session_factory()()
        try:
            d = get_transaction_detail(session, txn_id)
            if d is None:
                self.detail_error = "Transaction not found"
                return
            self._apply_detail(d)
        except Exception as exc:
            self.detail_error = str(exc)
        finally:
            session.close()

    @rx.event
    def close_detail(self) -> None:
        self.selected_transaction_id = ""
        self._clear_detail()

    @rx.event
    def toggle_details(self) -> None:
        self.details_open = not self.details_open

    @rx.event
    def set_edit_type(self, val: str) -> None:
        self.edit_type = val

    @rx.event
    def set_edit_category_id(self, val: str) -> None:
        self.edit_category_id = val

    @rx.event
    def set_edit_merchant(self, val: str) -> None:
        self.edit_merchant = val

    @rx.event
    def save_classification(self) -> None:
        """Apply a manual classification override and refresh both the detail and the table row."""
        if not self.selected_transaction_id:
            return
        if not self.edit_type:
            self.detail_save_error = "Transaction type is required."
            return
        self.detail_save_error = ""
        category_id = (
            self.edit_category_id
            if self.edit_category_id and self.edit_category_id != "__none__"
            else None
        )
        merchant = self.edit_merchant.strip() or None
        session = _get_session_factory()()
        try:
            classification_service.manual_override(
                session=session,
                transaction_id=self.selected_transaction_id,
                transaction_type=self.edit_type,
                category_id=category_id,
                merchant=merchant,
            )
            session.commit()
            # Reload detail so classification history shows the new entry
            d = get_transaction_detail(session, self.selected_transaction_id)
            if d is not None:
                self._apply_detail(d)
            # Refresh the table so the ledger row reflects the new classification
            self._do_fetch()
        except Exception as exc:
            self.detail_save_error = str(exc)
            session.rollback()
        finally:
            session.close()

    # ---- note events ----

    @rx.event
    def set_note_editor_text(self, val: str) -> None:
        self.note_editor_text = val

    @rx.event
    def save_note(self) -> None:
        if not self.selected_transaction_id:
            return
        if len(self.note_editor_text) > 2000:
            self.note_save_error = "Note must not exceed 2000 characters."
            return
        self.note_save_error = ""
        session = _get_session_factory()()
        try:
            set_note(session, self.selected_transaction_id, self.note_editor_text)
            session.commit()
            d = get_transaction_detail(session, self.selected_transaction_id)
            if d is not None:
                self._apply_detail(d)
            self._do_fetch()
        except Exception as exc:
            self.note_save_error = str(exc)
            session.rollback()
        finally:
            session.close()

    @rx.event
    def clear_note(self) -> None:
        if not self.selected_transaction_id:
            return
        self.note_save_error = ""
        session = _get_session_factory()()
        try:
            set_note(session, self.selected_transaction_id, None)
            session.commit()
            d = get_transaction_detail(session, self.selected_transaction_id)
            if d is not None:
                self._apply_detail(d)
            self._do_fetch()
        except Exception as exc:
            self.note_save_error = str(exc)
            session.rollback()
        finally:
            session.close()

    # ---- on-load events ----

    @rx.event
    def load_options(self) -> None:
        """Load account list from DB and category list from config."""
        self.categories = [
            {"id": "__none__", "label": "Uncategorised"},
            *[{"id": c.id, "label": c.name} for c in CATEGORIES],
        ]
        session = _get_session_factory()()
        try:
            accts = (
                session.query(Account)
                .filter(Account.active == True)  # noqa: E712
                .order_by(Account.name)
                .all()
            )
            self.accounts = [{"id": a.id, "label": a.name} for a in accts]
        except Exception as exc:
            self.error_message = str(exc)
        finally:
            session.close()

    @rx.event
    def fetch_transactions(self) -> None:
        """Fetch transactions using current filter state."""
        self._do_fetch()

    @rx.event
    def load_from_url(self):
        """Apply URL query params to filter/sort/page state before initial fetch."""
        self._parse_url_params()
        yield self._push_url()

    # ---- filter setters — each resets to page 0 and re-fetches ----

    @rx.event
    def set_filter_date_from(self, val: str):
        self.filter_date_from = val
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def set_filter_date_to(self, val: str):
        self.filter_date_to = val
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    # Multi-select filters are lists of individual checkboxes: Radix CheckboxGroup
    # exposes no on_change in Reflex 0.9, so each checkbox toggles its own value.
    @staticmethod
    def _toggled(values: list[str], value: str, checked: bool) -> list[str]:
        if checked:
            return values if value in values else [*values, value]
        return [v for v in values if v != value]

    @rx.event
    def toggle_filter_account(self, value: str, checked: bool):
        self.filter_account_ids = self._toggled(self.filter_account_ids, value, checked)
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def toggle_filter_type(self, value: str, checked: bool):
        self.filter_types = self._toggled(self.filter_types, value, checked)
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def toggle_filter_category(self, value: str, checked: bool):
        self.filter_category_ids = self._toggled(self.filter_category_ids, value, checked)
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def toggle_filter_review_state(self, value: str, checked: bool):
        self.filter_review_states = self._toggled(self.filter_review_states, value, checked)
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def set_filter_search(self, val: str):
        self.filter_search = val
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def toggle_show_rejected_deleted(self, checked: bool):
        self.filter_show_rejected_deleted = checked
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def clear_filters(self):
        self.filter_date_from = ""
        self.filter_date_to = ""
        self.filter_account_ids = []
        self.filter_types = []
        self.filter_category_ids = []
        self.filter_review_states = []
        self.filter_search = ""
        self.filter_show_rejected_deleted = False
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    # ---- pagination / sort ----

    @rx.event
    def prev_page(self):
        if self.has_prev_page:
            self.current_page -= 1
            self._do_fetch()
            yield self._push_url()

    @rx.event
    def next_page(self):
        if self.has_next_page:
            self.current_page += 1
            self._do_fetch()
            yield self._push_url()

    @rx.event
    def set_page_size(self, val: str):
        try:
            size = int(val)
        except ValueError:
            return
        if size not in PAGE_SIZES:
            return
        self.page_size = size
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def toggle_sort_date(self):
        self.sort = "date_asc" if self.sort == "date_desc" else "date_desc"
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def toggle_sort_amount(self):
        self.sort = "amount_asc" if self.sort == "amount_desc" else "amount_desc"
        self._do_fetch()
        yield self._push_url()


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------


def _filter_label(text: str) -> rx.Component:
    return rx.text(text, size="1", weight="medium", color_scheme="gray", margin_bottom="0.25em")


def _date_filters() -> rx.Component:
    return rx.vstack(
        _filter_label("Date range"),
        rx.flex(
            rx.input(
                type="date",
                value=TransactionsState.filter_date_from,
                on_change=TransactionsState.set_filter_date_from,
                data_testid="filter-date-from",
                size="2",
                width="140px",
            ),
            rx.text("–", size="2", color_scheme="gray"),
            rx.input(
                type="date",
                value=TransactionsState.filter_date_to,
                on_change=TransactionsState.set_filter_date_to,
                data_testid="filter-date-to",
                size="2",
                width="140px",
            ),
            align="center",
            gap="0.5em",
        ),
        align_items="start",
        gap="0",
    )


def _search_input() -> rx.Component:
    return rx.vstack(
        _filter_label("Search"),
        rx.input(
            placeholder="Search description, merchant, note…",
            value=TransactionsState.filter_search,
            on_change=TransactionsState.set_filter_search,
            data_testid="filter-search",
            size="2",
            width="260px",
        ),
        align_items="start",
        gap="0",
    )


def _filter_checkbox(label, value, selected, handler, testid=None) -> rx.Component:
    """One option in a multi-select filter; `selected` is the state list var."""
    return rx.checkbox(
        label,
        checked=selected.contains(value),
        on_change=lambda checked: handler(value, checked),
        data_testid=testid,
        size="2",
    )


def _type_filter() -> rx.Component:
    return rx.vstack(
        _filter_label("Type"),
        rx.box(
            rx.vstack(
                *[
                    _filter_checkbox(
                        t, t, TransactionsState.filter_types,
                        TransactionsState.toggle_filter_type, f"filter-type-{t}",
                    )
                    for t in _ALL_TYPES
                ],
                gap="0.35em",
                data_testid="filter-types",
            ),
            max_height="160px",
            overflow_y="auto",
            border="1px solid var(--gray-4)",
            border_radius="0.4em",
            padding="0.5em 0.75em",
            min_width="160px",
        ),
        align_items="start",
        gap="0",
    )


def _account_filter() -> rx.Component:
    return rx.vstack(
        _filter_label("Account"),
        rx.box(
            rx.vstack(
                rx.foreach(
                    TransactionsState.accounts,
                    lambda acct: _filter_checkbox(
                        acct["label"], acct["id"], TransactionsState.filter_account_ids,
                        TransactionsState.toggle_filter_account,
                    ),
                ),
                gap="0.35em",
                data_testid="filter-accounts",
            ),
            max_height="160px",
            overflow_y="auto",
            border="1px solid var(--gray-4)",
            border_radius="0.4em",
            padding="0.5em 0.75em",
            min_width="180px",
        ),
        align_items="start",
        gap="0",
    )


def _category_filter() -> rx.Component:
    return rx.vstack(
        _filter_label("Category"),
        rx.box(
            rx.vstack(
                rx.foreach(
                    TransactionsState.categories,
                    lambda cat: _filter_checkbox(
                        cat["label"], cat["id"], TransactionsState.filter_category_ids,
                        TransactionsState.toggle_filter_category,
                    ),
                ),
                gap="0.35em",
                data_testid="filter-categories",
            ),
            max_height="200px",
            overflow_y="auto",
            border="1px solid var(--gray-4)",
            border_radius="0.4em",
            padding="0.5em 0.75em",
            min_width="200px",
        ),
        align_items="start",
        gap="0",
    )


def _review_status_filter() -> rx.Component:
    return rx.vstack(
        _filter_label("Review status"),
        rx.box(
            rx.vstack(
                *[
                    _filter_checkbox(
                        opt["label"], opt["id"], TransactionsState.filter_review_states,
                        TransactionsState.toggle_filter_review_state,
                        f"filter-review-{opt['id']}",
                    )
                    for opt in _REVIEW_STATE_OPTIONS
                ],
                gap="0.35em",
                data_testid="filter-review-states",
            ),
            border="1px solid var(--gray-4)",
            border_radius="0.4em",
            padding="0.5em 0.75em",
            min_width="160px",
        ),
        align_items="start",
        gap="0",
    )


def _filter_bar() -> rx.Component:
    return rx.box(
        rx.vstack(
            # Top row: dates + search + clear button
            rx.flex(
                _date_filters(),
                _search_input(),
                rx.spacer(),
                rx.vstack(
                    rx.text(" ", size="1"),  # spacer to align with labeled inputs
                    rx.flex(
                        rx.switch(
                            data_testid="filter-show-rejected",
                            checked=TransactionsState.filter_show_rejected_deleted,
                            on_change=TransactionsState.toggle_show_rejected_deleted,
                        ),
                        rx.text("Show rejected/deleted", size="2"),
                        align="center",
                        gap="0.5em",
                    ),
                    align_items="start",
                    gap="0",
                ),
                rx.vstack(
                    rx.text(" ", size="1"),
                    rx.button(
                        "Clear filters",
                        variant="soft",
                        color_scheme="gray",
                        size="2",
                        on_click=TransactionsState.clear_filters,
                        disabled=~TransactionsState.has_filters,
                        data_testid="filter-clear",
                    ),
                    align_items="start",
                    gap="0",
                ),
                align="end",
                gap="1.5em",
                wrap="wrap",
                width="100%",
            ),
            # Bottom row: multi-select filter groups
            rx.flex(
                _type_filter(),
                _category_filter(),
                _account_filter(),
                _review_status_filter(),
                gap="1.5em",
                align="start",
                wrap="wrap",
                width="100%",
            ),
            gap="1em",
            width="100%",
            align_items="start",
        ),
        padding="1em 1.25em",
        border="1px solid var(--gray-4)",
        border_radius="0.5em",
        background="var(--color-panel)",
        margin_bottom="1.5em",
        width="100%",
        data_testid="filter-bar",
    )


def _detail_kv(label: str, val) -> rx.Component:
    """Compact key-value row; hidden when the value is empty."""
    return rx.cond(
        val,
        rx.flex(
            rx.text(label, size="1", color_scheme="gray", width="105px", flex_shrink="0"),
            rx.text(val, size="1"),
            align="start",
            gap="0.5em",
            width="100%",
        ),
        rx.fragment(),
    )


def _detail_card(*children, testid: str) -> rx.Component:
    return rx.box(
        *children,
        padding="0.5em 0.75em",
        border="1px solid var(--gray-4)",
        border_radius="0.4em",
        width="100%",
        data_testid=testid,
    )


def _source_row_item(sr: dict) -> rx.Component:
    return _detail_card(
        _detail_kv("File", sr["batch_filename"]),
        _detail_kv("Imported", sr["import_date"]),
        _detail_kv("Row #", sr["row_number"]),
        _detail_kv("Raw date", sr["raw_date"]),
        _detail_kv("Description", sr["raw_text"]),
        _detail_kv("Amount", sr["raw_amount"]),
        _detail_kv("Balance", sr["raw_balance"]),
        _detail_kv("Status", sr["raw_status"]),
        _detail_kv("Bank category", sr["raw_category"]),
        _detail_kv("Subcategory", sr["raw_subcategory"]),
        testid="source-row-item",
    )


def _classification_history_item(ch: dict) -> rx.Component:
    return _detail_card(
        _detail_kv("Type", ch["transaction_type"]),
        _detail_kv("Category", ch["category_name"]),
        _detail_kv("Merchant", ch["merchant"]),
        _detail_kv("Source", ch["source"]),
        _detail_kv("Review state", ch["review_state"]),
        _detail_kv("Rationale", ch["rationale"]),
        _detail_kv("Model", ch["model_version"]),
        _detail_kv("Rule", ch["rule_version"]),
        _detail_kv("Created", ch["created_at"]),
        testid="classification-history-item",
    )


def _detail_column(title: str, has_items, items, render, empty_text: str, testid: str) -> rx.Component:
    return rx.vstack(
        rx.text(title, weight="medium", size="2"),
        rx.cond(
            has_items,
            rx.vstack(rx.foreach(items, render), gap="0.5em", width="100%"),
            rx.text(empty_text, size="1", color_scheme="gray"),
        ),
        gap="0.4em",
        flex="1 1 320px",
        min_width="0",
        align_items="start",
        data_testid=testid,
    )


def _details_section() -> rx.Component:
    """Source rows and classification history, side by side, behind a toggle."""
    return rx.box(
        rx.button(
            rx.cond(TransactionsState.details_open, "▾ Details", "▸ Details"),
            variant="ghost",
            size="1",
            color_scheme="gray",
            on_click=TransactionsState.toggle_details,
            data_testid="detail-details-toggle",
        ),
        rx.cond(
            TransactionsState.details_open,
            rx.flex(
                _detail_column(
                    "Source rows",
                    TransactionsState.has_source_rows,
                    TransactionsState.detail_source_rows,
                    _source_row_item,
                    "No source rows.",
                    "detail-source-rows",
                ),
                _detail_column(
                    "Classification history",
                    TransactionsState.has_classification_history,
                    TransactionsState.detail_classification_history,
                    _classification_history_item,
                    "No classification history.",
                    "detail-classification-history",
                ),
                gap="1em",
                wrap="wrap",
                align="start",
                width="100%",
                margin_top="0.5em",
            ),
            rx.fragment(),
        ),
        margin_top="1.25em",
        width="100%",
    )


def _transfer_link_item(tl: dict) -> rx.Component:
    return rx.flex(
        rx.text(tl["counterpart_date"], size="2", color_scheme="gray", width="90px", flex_shrink="0"),
        rx.text(tl["account_name"], size="2", flex="1"),
        rx.text(tl["display_amount"], size="2", font_family="monospace", flex_shrink="0"),
        rx.badge(tl["link_state"], size="1", variant="soft", color_scheme="blue", flex_shrink="0"),
        align="center",
        gap="1em",
        padding="0.5em 0.75em",
        border="1px solid var(--gray-4)",
        border_radius="0.4em",
        data_testid="transfer-link-item",
    )


def _edit_classification_form() -> rx.Component:
    """Inline form for applying a manual classification override."""
    return rx.vstack(
        rx.text("Edit Classification", weight="medium", size="2", margin_bottom="0.25em"),
        rx.flex(
            rx.vstack(
                rx.text("Type", size="1", color_scheme="gray"),
                rx.select.root(
                    rx.select.trigger(data_testid="edit-type-select", size="2"),
                    rx.select.content(
                        *[
                            rx.select.item(t, value=t, data_testid=f"edit-type-option-{t}")
                            for t in _ALL_TYPES
                        ]
                    ),
                    value=TransactionsState.edit_type,
                    on_change=TransactionsState.set_edit_type,
                ),
                align_items="start",
                gap="0.25em",
            ),
            rx.vstack(
                rx.text("Category", size="1", color_scheme="gray"),
                rx.select.root(
                    rx.select.trigger(data_testid="edit-category-select", size="2"),
                    rx.select.content(
                        rx.foreach(
                            TransactionsState.categories,
                            lambda cat: rx.select.item(cat["label"], value=cat["id"]),
                        ),
                    ),
                    value=TransactionsState.edit_category_id,
                    on_change=TransactionsState.set_edit_category_id,
                ),
                align_items="start",
                gap="0.25em",
            ),
            rx.vstack(
                rx.text("Merchant", size="1", color_scheme="gray"),
                rx.input(
                    value=TransactionsState.edit_merchant,
                    on_change=TransactionsState.set_edit_merchant,
                    placeholder="Merchant (optional)",
                    data_testid="edit-merchant-input",
                    size="2",
                    width="200px",
                ),
                align_items="start",
                gap="0.25em",
            ),
            gap="1em",
            align="end",
            wrap="wrap",
        ),
        rx.cond(
            TransactionsState.detail_save_error != "",
            rx.text(
                TransactionsState.detail_save_error,
                color="red",
                size="2",
                data_testid="edit-save-error",
            ),
            rx.fragment(),
        ),
        rx.button(
            "Save",
            on_click=TransactionsState.save_classification,
            data_testid="edit-save-btn",
            size="2",
        ),
        gap="0.5em",
        align_items="start",
        width="100%",
        padding_top="1.25em",
        border_top="1px solid var(--gray-4)",
        margin_top="1.25em",
        data_testid="edit-classification-form",
    )


def _note_editor_panel() -> rx.Component:
    """Note editor section shown inside the detail panel."""
    return rx.vstack(
        rx.text("Note", weight="medium", size="2", margin_bottom="0.25em"),
        rx.text_area(
            value=TransactionsState.note_editor_text,
            on_change=TransactionsState.set_note_editor_text,
            placeholder="Add a note…",
            data_testid="note-input",
            rows="4",
            width="100%",
            resize="vertical",
        ),
        rx.flex(
            rx.text(
                TransactionsState.note_counter_label,
                size="1",
                color_scheme=rx.cond(TransactionsState.note_over_limit, "red", "gray"),
            ),
            width="100%",
        ),
        rx.cond(
            TransactionsState.note_over_limit,
            rx.text(
                "Note must not exceed 2000 characters.",
                color="red",
                size="2",
                data_testid="note-error",
            ),
            rx.fragment(),
        ),
        rx.cond(
            TransactionsState.note_save_error != "",
            rx.text(
                TransactionsState.note_save_error,
                color="red",
                size="2",
            ),
            rx.fragment(),
        ),
        rx.cond(
            TransactionsState.detail_note_updated_at != "",
            rx.text(
                "Last edited: " + TransactionsState.detail_note_updated_at,
                size="1",
                color_scheme="gray",
                data_testid="note-last-edited",
            ),
            rx.fragment(),
        ),
        rx.flex(
            rx.button(
                "Save",
                on_click=TransactionsState.save_note,
                data_testid="note-save-btn",
                size="2",
                disabled=TransactionsState.note_over_limit,
            ),
            rx.button(
                "Clear",
                on_click=TransactionsState.clear_note,
                data_testid="note-clear-btn",
                size="2",
                variant="soft",
                color_scheme="gray",
            ),
            gap="0.75em",
            align="center",
        ),
        gap="0.5em",
        align_items="start",
        width="100%",
        padding_top="1.25em",
        border_top="1px solid var(--gray-4)",
        margin_top="1.25em",
        data_testid="note-editor",
    )


def _detail_panel() -> rx.Component:
    return rx.box(
        rx.flex(
            rx.text("Transaction Detail", weight="bold", size="3"),
            rx.spacer(),
            rx.button(
                "Close",
                variant="ghost",
                size="1",
                on_click=TransactionsState.close_detail,
                data_testid="detail-close-btn",
            ),
            align="center",
            margin_bottom="1em",
        ),
        rx.cond(
            TransactionsState.detail_error != "",
            rx.box(
                rx.text(TransactionsState.detail_error, color="red", size="2"),
                padding="0.5em",
                border="1px solid var(--red-6)",
                border_radius="0.4em",
                margin_bottom="1em",
                data_testid="detail-error-banner",
            ),
            rx.fragment(),
        ),
        # Edit classification form
        _edit_classification_form(),
        # Note editor
        _note_editor_panel(),
        # Transfer links section
        rx.text(
            "Transfer links",
            weight="medium",
            size="2",
            margin_top="1.25em",
            margin_bottom="0.5em",
        ),
        rx.cond(
            TransactionsState.has_transfer_links,
            rx.vstack(
                rx.foreach(TransactionsState.detail_transfer_links, _transfer_link_item),
                gap="0.5em",
                width="100%",
                data_testid="detail-transfer-links",
            ),
            rx.text(
                "No transfer links.",
                size="2",
                color_scheme="gray",
                data_testid="detail-transfer-links",
            ),
        ),
        _details_section(),
        padding="1.25em",
        border_top="2px solid var(--accent-6)",
        background="var(--accent-2)",
        data_testid="transaction-detail-panel",
    )


def _ledger_row(item: dict) -> rx.Component:
    is_negative = item["is_negative"]
    is_selected = TransactionsState.selected_transaction_id == item["id"]
    return rx.box(
        rx.flex(
            rx.text(item["date"], size="2", color_scheme="gray", width="90px", flex_shrink="0"),
            rx.text(item["account_name"], size="2", width="130px", flex_shrink="0", color_scheme="gray"),
            rx.vstack(
                rx.text(
                    item["display_text"],
                    size="2",
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                    max_width="280px",
                ),
                rx.cond(
                    item["merchant"] != "",
                    rx.text(item["merchant"], size="1", color_scheme="gray"),
                    rx.fragment(),
                ),
                rx.cond(
                    (item["status"] == "Rejected") | (item["status"] == "Deleted"),
                    rx.badge(
                        item["status"],
                        color_scheme="red",
                        variant="outline",
                        size="1",
                        data_testid="status-badge",
                    ),
                    rx.fragment(),
                ),
                gap="0",
                align_items="start",
                flex="1",
                min_width="0",
            ),
            rx.text(
                item["category_name"],
                size="1",
                color_scheme="gray",
                width="140px",
                flex_shrink="0",
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
            ),
            rx.badge(
                item["transaction_type"],
                color_scheme=item["type_color"],
                variant="soft",
                size="1",
                width="110px",
                flex_shrink="0",
                data_testid="ledger-row-type",
            ),
            rx.cond(
                item["review_state"] != "",
                rx.badge(
                    item["review_state"],
                    color_scheme=item["review_color"],
                    variant="outline",
                    size="1",
                    width="90px",
                    flex_shrink="0",
                ),
                rx.box(width="90px", flex_shrink="0"),
            ),
            rx.text(
                item["display_amount"],
                size="2",
                font_family="monospace",
                color=rx.cond(is_negative, "var(--red-11)", "inherit"),
                text_align="right",
                width="100px",
                flex_shrink="0",
            ),
            rx.cond(
                item["has_note"],
                rx.icon("notebook-text", size=14, color="var(--gray-8)", data_testid="note-indicator"),
                rx.box(width="18px"),
            ),
            rx.cond(
                is_selected,
                rx.text("▾", size="2", color_scheme="gray", flex_shrink="0"),
                rx.text("›", size="2", color_scheme="gray", flex_shrink="0"),
            ),
            align="center",
            gap="0.75em",
            padding="0.5em 1em",
            width="100%",
            cursor="pointer",
            _hover={"background": "var(--accent-2)"},
            on_click=TransactionsState.open_detail(item["id"]),
            data_testid="ledger-row",
        ),
        rx.cond(
            is_selected,
            _detail_panel(),
            rx.fragment(),
        ),
        border_bottom="1px solid var(--gray-3)",
        width="100%",
    )


def _table_header() -> rx.Component:
    return rx.flex(
        rx.flex(
            rx.text(
                "Date",
                size="1",
                weight="medium",
                color_scheme="gray",
                cursor="pointer",
                on_click=TransactionsState.toggle_sort_date,
            ),
            rx.cond(
                TransactionsState.sort == "date_desc",
                rx.text("↓", size="1", color_scheme="gray"),
                rx.cond(
                    TransactionsState.sort == "date_asc",
                    rx.text("↑", size="1", color_scheme="gray"),
                    rx.fragment(),
                ),
            ),
            align="center",
            gap="0.25em",
            width="90px",
            flex_shrink="0",
        ),
        rx.text("Account", size="1", weight="medium", color_scheme="gray", width="130px", flex_shrink="0"),
        rx.text("Description", size="1", weight="medium", color_scheme="gray", flex="1", min_width="0"),
        rx.text("Category", size="1", weight="medium", color_scheme="gray", width="140px", flex_shrink="0"),
        rx.flex(
            rx.text("Type", size="1", weight="medium", color_scheme="gray"),
            width="110px",
            flex_shrink="0",
        ),
        rx.text("Status", size="1", weight="medium", color_scheme="gray", width="90px", flex_shrink="0"),
        rx.flex(
            rx.text(
                "Amount",
                size="1",
                weight="medium",
                color_scheme="gray",
                cursor="pointer",
                on_click=TransactionsState.toggle_sort_amount,
            ),
            rx.cond(
                TransactionsState.sort == "amount_desc",
                rx.text("↓", size="1", color_scheme="gray"),
                rx.cond(
                    TransactionsState.sort == "amount_asc",
                    rx.text("↑", size="1", color_scheme="gray"),
                    rx.fragment(),
                ),
            ),
            align="center",
            gap="0.25em",
            justify="end",
            width="100px",
            flex_shrink="0",
        ),
        rx.box(width="18px"),
        rx.box(width="16px"),
        align="center",
        gap="0.75em",
        padding="0.4em 1em",
        background="var(--gray-2)",
        border_bottom="1px solid var(--gray-4)",
        width="100%",
    )


def _ledger_table() -> rx.Component:
    return rx.box(
        _table_header(),
        rx.foreach(TransactionsState.rows, _ledger_row),
        border="1px solid var(--gray-4)",
        border_radius="0.5em",
        overflow="hidden",
        width="100%",
    )


def _pagination_controls() -> rx.Component:
    return rx.flex(
        rx.button(
            "← Prev",
            variant="soft",
            size="2",
            on_click=TransactionsState.prev_page,
            disabled=~TransactionsState.has_prev_page,
            data_testid="ledger-prev-page",
        ),
        rx.text(
            TransactionsState.page_info,
            size="2",
            color_scheme="gray",
            data_testid="ledger-total-count",
        ),
        rx.button(
            "Next →",
            variant="soft",
            size="2",
            on_click=TransactionsState.next_page,
            disabled=~TransactionsState.has_next_page,
            data_testid="ledger-next-page",
        ),
        rx.select.root(
            rx.select.trigger(data_testid="ledger-page-size", size="2"),
            rx.select.content(
                *[rx.select.item(str(s), value=str(s)) for s in PAGE_SIZES]
            ),
            value=TransactionsState.page_size_str,
            on_change=TransactionsState.set_page_size,
        ),
        align="center",
        justify="center",
        gap="1em",
        padding="1em 0",
    )


def _empty_state() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.icon("inbox", size=40, color="var(--gray-6)"),
            rx.cond(
                TransactionsState.has_filters,
                rx.vstack(
                    rx.text(
                        "No transactions match these filters",
                        size="3",
                        color_scheme="gray",
                    ),
                    rx.button(
                        "Clear filters",
                        variant="soft",
                        color_scheme="gray",
                        size="2",
                        on_click=TransactionsState.clear_filters,
                        data_testid="empty-state-clear",
                    ),
                    align="center",
                    gap="0.75em",
                ),
                rx.vstack(
                    rx.text(
                        "No transactions yet — import a CSV",
                        size="3",
                        color_scheme="gray",
                    ),
                    rx.link(
                        "Go to Import", href="/import", size="2",
                        data_testid="empty-state-import-link",
                    ),
                    align="center",
                    gap="0.5em",
                ),
            ),
            align="center",
            gap="0.75em",
        ),
        padding="3em",
        text_align="center",
        width="100%",
        border="1px solid var(--gray-4)",
        border_radius="0.5em",
        data_testid="ledger-empty-state",
    )


def _error_banner() -> rx.Component:
    return rx.cond(
        TransactionsState.error_message != "",
        rx.box(
            rx.text(TransactionsState.error_message, color="red", size="2"),
            padding="0.75em",
            border="1px solid var(--red-6)",
            border_radius="0.5em",
            margin_bottom="1em",
            data_testid="ledger-error-banner",
        ),
        rx.fragment(),
    )


@rx.page(
    route="/transactions",
    title="Transactions | Home Finance",
    on_load=[
        TransactionsState.load_options,
        TransactionsState.load_from_url,
        TransactionsState.fetch_transactions,
    ],
)
def transactions() -> rx.Component:
    return shell(
        rx.vstack(
            rx.flex(
                rx.heading("Transactions", size="7"),
                rx.spacer(),
                align="center",
                width="100%",
                margin_bottom="1em",
            ),
            _error_banner(),
            _filter_bar(),
            rx.cond(
                TransactionsState.total_count == 0,
                _empty_state(),
                rx.vstack(
                    _ledger_table(),
                    rx.cond(
                        TransactionsState.total_pages > 1,
                        _pagination_controls(),
                        rx.text(
                            TransactionsState.page_info,
                            size="2",
                            color_scheme="gray",
                            padding="0.5em 0",
                            data_testid="ledger-total-count",
                        ),
                    ),
                    width="100%",
                    gap="0",
                ),
            ),
            align_items="start",
            width="100%",
        ),
    )
