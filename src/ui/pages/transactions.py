"""Transactions page — browsable ledger with filters."""
from __future__ import annotations

import json
import urllib.parse
from datetime import date
from urllib.parse import parse_qs

import reflex as rx

from config.categories import CATEGORIES
from services.transaction_query import (
    LedgerFilters,
    LedgerRow,
    PAGE_SIZES,
    list_transactions,
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

    @rx.event
    def set_filter_account_ids(self, val: list[str]):
        self.filter_account_ids = val
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def set_filter_types(self, val: list[str]):
        self.filter_types = val
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def set_filter_category_ids(self, val: list[str]):
        self.filter_category_ids = val
        self.current_page = 0
        self._do_fetch()
        yield self._push_url()

    @rx.event
    def set_filter_review_states(self, val: list[str]):
        self.filter_review_states = val
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


def _type_filter() -> rx.Component:
    return rx.vstack(
        _filter_label("Type"),
        rx.box(
            rx.checkbox_group.root(
                *[
                    rx.checkbox_group.item(t, value=t, data_testid=f"filter-type-{t}")
                    for t in _ALL_TYPES
                ],
                value=TransactionsState.filter_types,
                on_change=TransactionsState.set_filter_types,
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
            rx.checkbox_group.root(
                rx.foreach(
                    TransactionsState.accounts,
                    lambda acct: rx.checkbox_group.item(
                        acct["label"],
                        value=acct["id"],
                    ),
                ),
                value=TransactionsState.filter_account_ids,
                on_change=TransactionsState.set_filter_account_ids,
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
            rx.checkbox_group.root(
                rx.foreach(
                    TransactionsState.categories,
                    lambda cat: rx.checkbox_group.item(
                        cat["label"],
                        value=cat["id"],
                    ),
                ),
                value=TransactionsState.filter_category_ids,
                on_change=TransactionsState.set_filter_category_ids,
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
            rx.checkbox_group.root(
                *[
                    rx.checkbox_group.item(
                        opt["label"],
                        value=opt["id"],
                        data_testid=f"filter-review-{opt['id']}",
                    )
                    for opt in _REVIEW_STATE_OPTIONS
                ],
                value=TransactionsState.filter_review_states,
                on_change=TransactionsState.set_filter_review_states,
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


def _ledger_row(item: dict) -> rx.Component:
    is_negative = item["amount_cents"] < 0
    return rx.flex(
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
        align="center",
        gap="0.75em",
        padding="0.5em 1em",
        border_bottom="1px solid var(--gray-3)",
        width="100%",
        _hover={"background": "var(--accent-2)"},
        data_testid="ledger-row",
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
                    rx.link("Go to Import", href="/import", size="2"),
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
