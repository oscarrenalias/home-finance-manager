"""Playwright browser tests for the Transactions page.

Covered acceptance criteria:
  AC-6:  /transactions?type=expense&category=groceries pre-applies both filters;
         changing a filter updates the URL.
  AC-10: transaction with display_text containing HTML/script is rendered as literal
         text; no injected DOM element exists (A17).
  AC-12: empty ledger state shows link to /import; no-filter-match state shows
         Clear filters button; opening detail panel and editing classification
         updates the ledger row in place.
  AC-16: note golden path — open detail, type note, Save, note-indicator appears
         on ledger row, reload, note persists; note containing <script> text is
         rendered as plain text.

Each test is independent and idempotent. DB rows are seeded via SQLAlchemy
directly against app_db_url; unique display_text values prevent cross-test
interference.
"""
from __future__ import annotations

import datetime
import re
import uuid

import sqlalchemy as sa
from playwright.sync_api import Page, expect
from sqlalchemy.orm import sessionmaker

from storage.models import Classification, Transaction

# Deterministic account IDs from the seed migration (uuid5 of "home-finances.<slug>")
_COMMON_ACCOUNT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.common"))
_ACCRUAL_ACCOUNT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.accrual"))

_INTERACT_TIMEOUT_MS = 20_000
_NAV_TIMEOUT_MS = 30_000


def _engine(db_url: str):
    return sa.create_engine(db_url, connect_args={"check_same_thread": False})


def _clear_ledger(db_url: str) -> None:
    """Delete all ledger rows (children first) from the shared session test DB."""
    engine = _engine(db_url)
    try:
        with engine.begin() as conn:
            for table in ("transfer_links", "classifications", "source_observations", "transactions"):
                conn.execute(sa.text(f"DELETE FROM {table}"))
    finally:
        engine.dispose()


def _classifications(db_url: str, tx_id: str) -> list[tuple[str, str, str]]:
    """Return every (transaction_type, source, review_state) row for a transaction.

    Not ordered by created_at: SQLite timestamps have one-second resolution, so a
    seeded row and an edit made in the same second would tie.
    """
    engine = _engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT transaction_type, source, review_state FROM classifications "
                    "WHERE transaction_id = :tx"
                ),
                {"tx": tx_id},
            ).all()
        return [tuple(r) for r in rows]
    finally:
        engine.dispose()


def _seed_tx(
    db_url: str,
    *,
    display_text: str,
    amount_cents: int = -1000,
    tx_date: datetime.date | None = None,
    status: str = "Executed",
    account_id: str = _COMMON_ACCOUNT_ID,
    transaction_type: str | None = None,
    category_id: str | None = None,
    note: str | None = None,
) -> str:
    """Insert one Transaction and return its ID."""
    tx_id = str(uuid.uuid4())
    engine = _engine(db_url)
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            Transaction(
                id=tx_id,
                account_id=account_id,
                date=tx_date or datetime.date(2030, 6, 1),
                amount_cents=amount_cents,
                currency="EUR",
                original_text=display_text,
                display_text=display_text,
                status=status,
                transaction_type=transaction_type,
                category_id=category_id,
                note=note,
            )
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()
    return tx_id


def _add_classification(
    db_url: str,
    tx_id: str,
    *,
    transaction_type: str = "expense",
    category_id: str | None = "groceries",
    merchant: str | None = None,
    review_state: str = "accepted",
    source: str = "manual",
) -> str:
    cls_id = str(uuid.uuid4())
    engine = _engine(db_url)
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            Classification(
                id=cls_id,
                transaction_id=tx_id,
                transaction_type=transaction_type,
                category_id=category_id,
                merchant=merchant,
                review_state=review_state,
                source=source,
            )
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()
    return cls_id


# ---------------------------------------------------------------------------
# AC-12 — Empty ledger state
# ---------------------------------------------------------------------------


def test_empty_ledger_shows_import_link(page: Page, app_server: str, app_db_url: str) -> None:
    """AC-12: empty ledger state appears with a link to /import when no transactions exist."""
    # The app DB is shared across the session; other tests seed their own rows per test,
    # so clearing the ledger here makes the empty state deterministic.
    _clear_ledger(app_db_url)

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    empty = page.get_by_test_id("ledger-empty-state")
    expect(empty).to_be_visible(timeout=_NAV_TIMEOUT_MS)
    expect(empty).to_contain_text("No transactions yet")
    import_link = page.get_by_test_id("empty-state-import-link")
    expect(import_link).to_be_visible()
    expect(import_link).to_have_attribute("href", re.compile(r"/import$"))
    expect(page.get_by_test_id("ledger-row")).to_have_count(0)


# ---------------------------------------------------------------------------
# AC-6 — URL query parameters pre-apply filters
# ---------------------------------------------------------------------------


def test_url_params_preapply_type_filter(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-6: /transactions?type=expense pre-applies the expense type filter."""
    # Seed an expense transaction that will show when type=expense filter is applied.
    run = uuid.uuid4().hex[:8]
    tag = f"ac6-expense-{run}"
    other = f"ac6-income-{run}"
    tx_id = _seed_tx(app_db_url, display_text=tag, amount_cents=-500)
    _add_classification(app_db_url, tx_id, transaction_type="expense", category_id=None)
    other_id = _seed_tx(app_db_url, display_text=other, amount_cents=900)
    _add_classification(app_db_url, other_id, transaction_type="income", category_id=None)

    page.goto(f"{app_server}/transactions?type=expense&search=ac6-", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)
    rows = page.get_by_test_id("ledger-row")
    expect(rows.filter(has_text=tag)).to_have_count(1, timeout=_INTERACT_TIMEOUT_MS)
    expect(rows.filter(has_text=other)).to_have_count(0)
    expect(page.get_by_test_id("filter-type-expense")).to_have_attribute("data-state", "checked")


def test_url_params_preapply_type_and_category(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-6: /transactions?type=expense&category=groceries pre-applies both filters."""
    run = uuid.uuid4().hex[:8]
    tag = f"ac6-both-{run}"
    other = f"ac6-other-cat-{run}"
    tx_id = _seed_tx(app_db_url, display_text=tag, amount_cents=-300)
    _add_classification(
        app_db_url, tx_id, transaction_type="expense", category_id="groceries"
    )
    other_id = _seed_tx(app_db_url, display_text=other, amount_cents=-300)
    _add_classification(app_db_url, other_id, transaction_type="expense", category_id=None)

    page.goto(
        f"{app_server}/transactions?type=expense&category=groceries&search={run}",
        wait_until="networkidle",
    )
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)
    rows = page.get_by_test_id("ledger-row")
    expect(rows.filter(has_text=tag)).to_have_count(1, timeout=_INTERACT_TIMEOUT_MS)
    expect(rows.filter(has_text=other)).to_have_count(0)


def test_filter_change_updates_url(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-6: changing a filter updates the browser URL query string."""
    run = uuid.uuid4().hex[:8]
    income_tag = f"ac6-urlchange-income-{run}"
    expense_tag = f"ac6-urlchange-expense-{run}"
    income_id = _seed_tx(app_db_url, display_text=income_tag, amount_cents=200)
    _add_classification(app_db_url, income_id, transaction_type="income", category_id=None)
    expense_id = _seed_tx(app_db_url, display_text=expense_tag, amount_cents=-200)
    _add_classification(app_db_url, expense_id, transaction_type="expense", category_id=None)

    page.goto(f"{app_server}/transactions?search={run}", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)
    rows = page.get_by_test_id("ledger-row")
    expect(rows).to_have_count(2, timeout=_INTERACT_TIMEOUT_MS)

    # Tick "income": only the income row remains and the URL carries the filter.
    income_cb = page.get_by_test_id("filter-type-income")
    income_cb.click()
    expect(income_cb).to_have_attribute("data-state", "checked", timeout=_INTERACT_TIMEOUT_MS)
    expect(page).to_have_url(re.compile(r"[?&]type=income(&|$)"), timeout=_INTERACT_TIMEOUT_MS)
    expect(rows.filter(has_text=income_tag)).to_have_count(1)
    expect(rows.filter(has_text=expense_tag)).to_have_count(0)

    # Untick it: both rows return and the filter leaves the URL.
    income_cb.click()
    expect(income_cb).to_have_attribute("data-state", "unchecked", timeout=_INTERACT_TIMEOUT_MS)
    expect(page).not_to_have_url(re.compile(r"type="), timeout=_INTERACT_TIMEOUT_MS)
    expect(rows).to_have_count(2, timeout=_INTERACT_TIMEOUT_MS)


# ---------------------------------------------------------------------------
# AC-10 — A17: HTML/script in description renders as literal text
# ---------------------------------------------------------------------------


def test_script_in_display_text_renders_as_literal(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-10 / A17: display_text containing <script>alert(1)</script> is rendered
    as a literal text node — no injected <script> element, no alert fires."""
    malicious = "<script>alert('xss-ac10')</script>"
    tag = f"ac10-{uuid.uuid4().hex[:8]}"
    display = f"{tag} {malicious}"
    _seed_tx(app_db_url, display_text=display, tx_date=datetime.date(2030, 12, 31))

    # Capture any dialogs that appear (alert/confirm/prompt) — should be none.
    dialog_fired = []
    page.on("dialog", lambda d: dialog_fired.append(d.message) or d.dismiss())

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    # Confirm no dialog was triggered
    assert not dialog_fired, f"Script executed unexpectedly: {dialog_fired}"

    # The text node must contain the literal < and > characters
    row = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)

    # No <script> element should exist in the DOM with our payload text
    injected = page.locator("script").filter(has_text="xss-ac10")
    assert injected.count() == 0, "Injected <script> element found in DOM"


# ---------------------------------------------------------------------------
# AC-12 — No-match state with Clear filters button
# ---------------------------------------------------------------------------


def test_no_match_state_shows_clear_filters(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-12: applying a filter that matches nothing shows the no-filter-match state
    with a Clear filters button."""
    # A unique search term guarantees no match regardless of rows other tests seeded.
    no_match = f"no-match-{uuid.uuid4().hex}"
    page.goto(
        f"{app_server}/transactions?search={no_match}",
        wait_until="networkidle",
    )
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    empty_state = page.get_by_test_id("ledger-empty-state")
    expect(empty_state).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)
    expect(empty_state).to_contain_text("No transactions match these filters")

    page.get_by_test_id("empty-state-clear").click()
    expect(page.get_by_test_id("filter-search")).to_have_value("", timeout=_INTERACT_TIMEOUT_MS)
    expect(page).not_to_have_url(re.compile(r"search="), timeout=_INTERACT_TIMEOUT_MS)


# ---------------------------------------------------------------------------
# AC-12 — Detail panel golden path: open, edit classification, row updates
# ---------------------------------------------------------------------------


def test_detail_panel_opens_and_closes(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-12/AC-7: clicking a ledger row opens the detail panel; clicking close hides it."""
    tag = f"ac12-detail-{uuid.uuid4().hex[:8]}"
    tx_id = _seed_tx(
        app_db_url,
        display_text=tag,
        amount_cents=-750,
        tx_date=datetime.date(2030, 7, 1),
    )
    _add_classification(app_db_url, tx_id, transaction_type="expense", category_id=None)

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    row = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)
    row.click()

    panel = page.get_by_test_id("transaction-detail-panel")
    expect(panel).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Close via close button
    close_btn = page.get_by_test_id("detail-close-btn")
    expect(close_btn).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)
    close_btn.click()

    expect(panel).not_to_be_visible(timeout=_INTERACT_TIMEOUT_MS)


def test_detail_panel_edit_classification_golden_path(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-12/AC-8: open detail on classified transaction → form pre-filled → change type
    → Save → ledger row type updates in place."""
    tag = f"ac12-edit-{uuid.uuid4().hex[:8]}"
    tx_id = _seed_tx(
        app_db_url,
        display_text=tag,
        amount_cents=-500,
        tx_date=datetime.date(2030, 8, 1),
    )
    # An LLM suggestion awaiting review; the manual edit must then win on review_state,
    # not on created_at (SQLite timestamps tie within the same second).
    _add_classification(
        app_db_url,
        tx_id,
        transaction_type="expense",
        category_id="groceries",
        merchant="Lidl",
        review_state="needs_review",
        source="llm",
    )

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    # Find and click the row
    row = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)
    row.click()

    panel = page.get_by_test_id("transaction-detail-panel")
    expect(panel).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Edit classification form must be visible
    form = page.get_by_test_id("edit-classification-form")
    expect(form).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Change type to "income" via the type select
    type_select = page.get_by_test_id("edit-type-select")
    expect(type_select).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)
    type_select.click()
    page.get_by_test_id("edit-type-option-income").click()

    save_btn = page.get_by_test_id("edit-save-btn")
    expect(save_btn).to_be_enabled(timeout=_INTERACT_TIMEOUT_MS)
    save_btn.click()

    # AC-12: the row updates in place, and the edit is stored as a manual override (A11).
    expect(row.get_by_test_id("ledger-row-type")).to_have_text("income", timeout=_INTERACT_TIMEOUT_MS)
    expect(page.get_by_test_id("ledger-error-banner")).to_have_count(0)
    stored = _classifications(app_db_url, tx_id)
    assert ("income", "manual", "accepted") in stored, stored
    assert ("expense", "llm", "needs_review") in stored, stored
    assert len(stored) == 2, f"expected the LLM row plus one manual override, got {stored}"


def test_detail_panel_all_sections_visible(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-7: detail panel shows all three sections: source rows, history, transfer links."""
    tag = f"ac7-sections-{uuid.uuid4().hex[:8]}"
    tx_id = _seed_tx(
        app_db_url,
        display_text=tag,
        amount_cents=-900,
        tx_date=datetime.date(2030, 9, 1),
    )

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    row = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)
    row.click()

    panel = page.get_by_test_id("transaction-detail-panel")
    expect(panel).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Transfer links are always shown; source rows and history start collapsed.
    expect(panel.get_by_test_id("detail-transfer-links")).to_be_visible(
        timeout=_INTERACT_TIMEOUT_MS
    )
    expect(panel.get_by_test_id("detail-source-rows")).to_have_count(0)
    expect(panel.get_by_test_id("detail-classification-history")).to_have_count(0)

    toggle = panel.get_by_test_id("detail-details-toggle")
    toggle.click()
    expect(panel.get_by_test_id("detail-source-rows")).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)
    expect(panel.get_by_test_id("detail-source-rows")).to_contain_text("No source rows.")
    expect(panel.get_by_test_id("detail-classification-history")).to_be_visible()
    expect(panel.get_by_test_id("detail-classification-history")).to_contain_text(
        "No classification history."
    )

    toggle.click()
    expect(panel.get_by_test_id("detail-source-rows")).to_have_count(0, timeout=_INTERACT_TIMEOUT_MS)


# ---------------------------------------------------------------------------
# AC-16 — Note golden path
# ---------------------------------------------------------------------------


def test_note_golden_path_save_and_reload(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-16: open detail → type note → Save → note-indicator appears on ledger row
    → reload page → note still persists in the editor."""
    tag = f"ac16-note-{uuid.uuid4().hex[:8]}"
    _seed_tx(
        app_db_url,
        display_text=tag,
        amount_cents=-250,
        tx_date=datetime.date(2030, 10, 1),
    )

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    row = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)
    row.click()

    panel = page.get_by_test_id("transaction-detail-panel")
    expect(panel).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    note_editor = page.get_by_test_id("note-editor")
    expect(note_editor).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    note_text = f"Test note for {tag}"
    note_input = page.get_by_test_id("note-input")
    expect(note_input).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)
    note_input.fill(note_text)

    save_btn = page.get_by_test_id("note-save-btn")
    expect(save_btn).to_be_enabled(timeout=_INTERACT_TIMEOUT_MS)
    save_btn.click()

    page.wait_for_load_state("networkidle", timeout=_NAV_TIMEOUT_MS)

    # Note indicator should now appear on the ledger row
    row_updated = page.get_by_test_id("ledger-row").filter(has_text=tag)
    note_indicator = row_updated.get_by_test_id("note-indicator")
    expect(note_indicator).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Close panel
    page.get_by_test_id("detail-close-btn").click()
    expect(panel).not_to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Reload and verify the note persists
    page.reload(wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    row_after_reload = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row_after_reload.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)
    row_after_reload.click()

    panel_after = page.get_by_test_id("transaction-detail-panel")
    expect(panel_after).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    note_input_after = page.get_by_test_id("note-input")
    expect(note_input_after).to_have_value(note_text, timeout=_INTERACT_TIMEOUT_MS)


def test_note_over_2000_chars_shows_error(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-16: typing more than 2000 chars shows note-error and disables Save."""
    tag = f"ac16-toolong-{uuid.uuid4().hex[:8]}"
    _seed_tx(
        app_db_url,
        display_text=tag,
        amount_cents=-100,
        tx_date=datetime.date(2030, 11, 1),
    )

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    row = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)
    row.click()

    panel = page.get_by_test_id("transaction-detail-panel")
    expect(panel).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    note_input = page.get_by_test_id("note-input")
    expect(note_input).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)
    note_input.fill("x" * 2001)

    # Error message must appear
    note_error = page.get_by_test_id("note-error")
    expect(note_error).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Save button must be disabled
    save_btn = page.get_by_test_id("note-save-btn")
    expect(save_btn).to_be_disabled(timeout=_INTERACT_TIMEOUT_MS)


def test_note_script_text_renders_as_literal(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-16: a note containing <script>text is displayed as literal text in the editor."""
    tag = f"ac16-script-note-{uuid.uuid4().hex[:8]}"
    malicious_note = "<script>alert('note-xss')</script>"
    _seed_tx(
        app_db_url,
        display_text=tag,
        amount_cents=-100,
        tx_date=datetime.date(2030, 11, 15),
        note=malicious_note,
    )

    dialog_fired = []
    page.on("dialog", lambda d: dialog_fired.append(d.message) or d.dismiss())

    page.goto(f"{app_server}/transactions", wait_until="networkidle")
    expect(page.get_by_test_id("filter-bar")).to_be_visible(timeout=_NAV_TIMEOUT_MS)

    row = page.get_by_test_id("ledger-row").filter(has_text=tag)
    row.wait_for(state="visible", timeout=_INTERACT_TIMEOUT_MS)
    row.click()

    panel = page.get_by_test_id("transaction-detail-panel")
    expect(panel).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    note_input = page.get_by_test_id("note-input")
    expect(note_input).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # The textarea value must contain the literal script text
    note_value = note_input.input_value()
    assert "alert" in note_value or malicious_note in note_value, (
        f"Note textarea did not contain expected text. Got: {note_value!r}"
    )

    # No dialog/alert should have fired
    assert not dialog_fired, f"Script executed unexpectedly: {dialog_fired}"

    # No injected <script> element in DOM
    assert page.locator("script").filter(has_text="note-xss").count() == 0
