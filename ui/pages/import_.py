"""Import page — CSV statement upload and preview workflow."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

import reflex as rx

from services.import_service import CommitResult, ImportPreview, ImportService
from storage.database import _get_session_factory
from storage.file_store import FileStore
from storage.models import Account
from ui.components import shell


def _make_service() -> ImportService:
    return ImportService(
        session_factory=_get_session_factory(),
        file_store=FileStore(),
    )


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _format_eur(cents: int) -> str:
    """Format integer cents as a signed EUR string (e.g. -255 → '−€ 2.55')."""
    negative = cents < 0
    abs_cents = abs(cents)
    euros = abs_cents // 100
    cent_part = abs_cents % 100
    sign = "−" if negative else ""
    return f"{sign}€ {euros:,}.{cent_part:02d}"


class ImportState(rx.State):
    # --- form controls ---
    account_options: list[tuple[str, str]] = []
    selected_account_id: str = ""
    filename: str = ""
    coverage_start: str = ""
    coverage_end: str = ""
    completeness: bool = False
    committing: bool = False
    error_message: str = ""

    # --- preview display vars (frontend-visible mirror of _preview) ---
    preview_has_data: bool = False
    preview_already_imported: bool = False
    preview_same_file_different_account: bool = False
    preview_executed_count: int = 0
    preview_new_count: int = 0
    preview_reused_count: int = 0
    preview_ambiguous_count: int = 0
    preview_pending_count: int = 0
    preview_error_count: int = 0
    preview_parse_errors: list[str] = []
    preview_balance_status: str = ""  # "ok" | "inconclusive" | "mismatch" | ""
    preview_balance_mismatches: list[str] = []
    preview_rows: list[dict[str, str]] = []
    same_account_warning_ack: bool = False

    # --- commit result display vars ---
    commit_result_has_data: bool = False
    commit_result_new_transactions: int = 0
    commit_result_reused_transactions: int = 0
    commit_result_pending_observations: int = 0
    commit_result_enqueued_jobs: int = 0

    # Backend vars — server-side only, not synced to the browser (Reflex `_` prefix convention)
    _preview: Optional[ImportPreview] = None
    _commit_result: Optional[CommitResult] = None

    @rx.var(cache=False)
    def has_any_parse_errors(self) -> bool:
        return self.preview_error_count > 0 or len(self.preview_parse_errors) > 0

    @rx.var(cache=False)
    def commit_disabled(self) -> bool:
        if self.preview_already_imported:
            return True
        if self.has_any_parse_errors:
            return True
        if self.preview_same_file_different_account and not self.same_account_warning_ack:
            return True
        return False

    @rx.event
    def set_selected_account_id(self, value: str) -> None:
        self.selected_account_id = value

    @rx.event
    def set_coverage_start(self, value: str) -> None:
        self.coverage_start = value

    @rx.event
    def set_coverage_end(self, value: str) -> None:
        self.coverage_end = value

    @rx.event
    def set_completeness(self, value: bool) -> None:
        self.completeness = value

    @rx.event
    def set_same_account_warning_ack(self, value: bool) -> None:
        self.same_account_warning_ack = value

    @rx.event
    def load_accounts(self) -> None:
        session = _get_session_factory()()
        try:
            accounts = (
                session.query(Account)
                .filter(Account.active == True)  # noqa: E712
                .order_by(Account.name)
                .all()
            )
            self.account_options = [(a.id, a.name) for a in accounts]
            if self.account_options and not self.selected_account_id:
                self.selected_account_id = self.account_options[0][0]
        finally:
            session.close()

    def _populate_preview_vars(self, preview: ImportPreview) -> None:
        """Copy ImportPreview fields into frontend-visible state vars."""
        self.preview_has_data = True
        self.preview_already_imported = preview.already_imported
        self.preview_same_file_different_account = preview.same_file_different_account
        self.preview_executed_count = preview.executed_count
        self.preview_new_count = preview.new_count
        self.preview_reused_count = preview.exact_match_count + preview.probable_match_count
        self.preview_ambiguous_count = preview.ambiguous_count
        self.preview_pending_count = preview.pending_count
        self.preview_error_count = preview.error_count
        self.preview_parse_errors = list(preview.parse_errors)

        bc = preview.balance_check
        if bc is None or bc.checked_pairs == 0:
            self.preview_balance_status = "inconclusive"
            self.preview_balance_mismatches = list(bc.inconclusive_reasons) if bc else []
        elif bc.mismatches:
            self.preview_balance_status = "mismatch"
            self.preview_balance_mismatches = [
                f"Rows {m.row_number_a}–{m.row_number_b}: "
                f"expected {_format_eur(m.expected_cents)}, "
                f"got {_format_eur(m.actual_cents)}"
                for m in bc.mismatches
            ]
        else:
            self.preview_balance_status = "ok"
            self.preview_balance_mismatches = []

        self.preview_rows = [
            {
                "row_number": str(rp.row_number),
                "date": str(rp.parsed_date),
                "display_text": rp.display_text,
                "amount": _format_eur(rp.amount_cents),
                "status": "Pending" if rp.is_pending else "Executed",
                "confidence": (
                    rp.match_candidate.confidence if rp.match_candidate else ""
                ),
            }
            for rp in preview.row_previews
        ]

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        if not files:
            return
        file = files[0]
        filename = file.filename or "upload.csv"
        self.filename = filename
        self.error_message = ""
        self._preview = None
        self._commit_result = None
        self.preview_has_data = False
        self.preview_already_imported = False
        self.preview_same_file_different_account = False
        self.preview_executed_count = 0
        self.preview_new_count = 0
        self.preview_reused_count = 0
        self.preview_ambiguous_count = 0
        self.preview_pending_count = 0
        self.preview_error_count = 0
        self.preview_parse_errors = []
        self.preview_balance_status = ""
        self.preview_balance_mismatches = []
        self.preview_rows = []
        self.same_account_warning_ack = False
        self.commit_result_has_data = False
        account_id = self.selected_account_id
        coverage_start = _parse_date(self.coverage_start)
        coverage_end = _parse_date(self.coverage_end)
        completeness = self.completeness
        yield  # flush reset state to frontend

        data = await file.read()
        svc = _make_service()
        try:
            preview = svc.preview(
                account_id=account_id,
                filename=filename,
                data=data,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                completeness=completeness,
            )
            if preview.parse_errors:
                # Header-level errors prevent all row parsing — surface via error banner.
                self.error_message = "; ".join(preview.parse_errors)
            else:
                self._preview = preview
                self._populate_preview_vars(preview)
        except Exception as exc:
            self.error_message = str(exc)

    @rx.event(background=True)
    async def handle_commit(self) -> None:
        async with self:
            preview = self._preview
            if preview is None:
                return
            account_id = self.selected_account_id
            coverage_start_str = self.coverage_start
            coverage_end_str = self.coverage_end
            completeness = self.completeness
            self.committing = True
            self.error_message = ""

        coverage_start = _parse_date(coverage_start_str)
        coverage_end = _parse_date(coverage_end_str)

        svc = _make_service()
        try:
            result = svc.commit(
                account_id=account_id,
                idempotency_token=preview.idempotency_token,
                preview=preview,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                completeness=completeness,
            )
            async with self:
                self._commit_result = result
                self._preview = None
                self.committing = False
                # Clear preview display
                self.preview_has_data = False
                self.preview_rows = []
                # Populate commit result
                self.commit_result_has_data = True
                self.commit_result_new_transactions = result.new_transactions
                self.commit_result_reused_transactions = result.reused_transactions
                self.commit_result_pending_observations = result.pending_observations
                self.commit_result_enqueued_jobs = result.enqueued_jobs
        except Exception as exc:
            async with self:
                self.error_message = str(exc)
                self.committing = False


# ---------------------------------------------------------------------------
# UI components — form inputs
# ---------------------------------------------------------------------------


def _error_banner() -> rx.Component:
    return rx.cond(
        ImportState.error_message,
        rx.callout(
            ImportState.error_message,
            color_scheme="red",
            width="100%",
            data_testid="error-banner",
        ),
        rx.fragment(),
    )


def _account_selector() -> rx.Component:
    return rx.vstack(
        rx.text("Account", weight="medium", size="2"),
        rx.select.root(
            rx.select.trigger(placeholder="Select account"),
            rx.select.content(
                rx.foreach(
                    ImportState.account_options,
                    lambda opt: rx.select.item(opt[1], value=opt[0], data_testid=opt[0]),
                ),
            ),
            value=ImportState.selected_account_id,
            on_change=ImportState.set_selected_account_id,
            width="300px",
            data_testid="account-select",
        ),
        align="start",
        gap="0.5em",
    )


def _upload_area() -> rx.Component:
    return rx.vstack(
        rx.text("CSV file", weight="medium", size="2"),
        rx.upload(
            rx.vstack(
                rx.icon("upload", size=24),
                rx.text("Drop a CSV file here, or click to browse"),
                rx.text("Single .csv file · max 10 MB", size="1", color_scheme="gray"),
                align="center",
                gap="0.5em",
            ),
            id="csv_upload",
            accept={"text/csv": [".csv"]},
            max_files=1,
            max_size=10 * 1024 * 1024,
            on_drop=ImportState.handle_upload(
                rx.upload_files(upload_id="csv_upload")
            ),
            border="2px dashed var(--gray-6)",
            border_radius="0.5em",
            padding="2em",
            width="100%",
            text_align="center",
            cursor="pointer",
            _hover={"border_color": "var(--accent-6)"},
            data_testid="csv-upload",
        ),
        align="start",
        gap="0.75em",
        width="100%",
    )


def _coverage_fields() -> rx.Component:
    return rx.vstack(
        rx.text("Coverage period", weight="medium", size="2"),
        rx.hstack(
            rx.vstack(
                rx.text("Start date", size="1", color_scheme="gray"),
                rx.input(
                    placeholder="YYYY-MM-DD",
                    value=ImportState.coverage_start,
                    on_change=ImportState.set_coverage_start,
                    width="160px",
                ),
                align="start",
                gap="0.25em",
            ),
            rx.vstack(
                rx.text("End date", size="1", color_scheme="gray"),
                rx.input(
                    placeholder="YYYY-MM-DD",
                    value=ImportState.coverage_end,
                    on_change=ImportState.set_coverage_end,
                    width="160px",
                ),
                align="start",
                gap="0.25em",
            ),
            gap="1em",
            align="start",
        ),
        align="start",
        gap="0.5em",
    )


def _completeness_checkbox() -> rx.Component:
    return rx.hstack(
        rx.checkbox(
            checked=ImportState.completeness,
            on_change=ImportState.set_completeness,
        ),
        rx.tooltip(
            rx.text("Complete export", size="2"),
            content=(
                "Check this if the CSV covers the entire coverage period with no gaps. "
                "Used to detect missing transactions between imports."
            ),
        ),
        align="center",
        gap="0.5em",
    )


# ---------------------------------------------------------------------------
# UI components — preview panel
# ---------------------------------------------------------------------------


def _stat_tile(label: str, value: Any) -> rx.Component:
    return rx.vstack(
        rx.text(value, weight="bold", size="5"),
        rx.text(label, size="1", color_scheme="gray"),
        align="center",
        gap="0.15em",
    )


def _summary_row() -> rx.Component:
    return rx.hstack(
        _stat_tile("Total", ImportState.preview_executed_count + ImportState.preview_pending_count),
        _stat_tile("New", ImportState.preview_new_count),
        _stat_tile("Reused", ImportState.preview_reused_count),
        _stat_tile("Ambiguous", ImportState.preview_ambiguous_count),
        _stat_tile("Pending", ImportState.preview_pending_count),
        _stat_tile("Errors", ImportState.preview_error_count),
        gap="2em",
        padding="1em",
        border="1px solid var(--gray-4)",
        border_radius="0.5em",
        width="100%",
        flex_wrap="wrap",
    )


def _balance_check_indicator() -> rx.Component:
    return rx.cond(
        ImportState.preview_balance_status == "ok",
        rx.hstack(
            rx.icon("circle-check-big", size=16, color="var(--green-9)"),
            rx.text("Balance checks passed", size="2"),
            align="center",
            gap="0.5em",
        ),
        rx.cond(
            ImportState.preview_balance_status == "mismatch",
            rx.vstack(
                rx.hstack(
                    rx.icon("circle-x", size=16, color="var(--red-9)"),
                    rx.text("Balance mismatches found", size="2", color="var(--red-9)"),
                    align="center",
                    gap="0.5em",
                ),
                rx.foreach(
                    ImportState.preview_balance_mismatches,
                    lambda msg: rx.text(msg, size="1", color_scheme="red"),
                ),
                align="start",
                gap="0.25em",
            ),
            rx.hstack(
                rx.icon("circle-help", size=16, color="var(--orange-9)"),
                rx.text("Balance check inconclusive", size="2", color="var(--orange-9)"),
                align="center",
                gap="0.5em",
            ),
        ),
    )


def _confidence_badge(confidence: str) -> rx.Component:
    return rx.badge(
        confidence,
        color_scheme=rx.match(
            confidence,
            ("exact", "green"),
            ("probable", "blue"),
            ("ambiguous", "orange"),
            ("new", "gray"),
            "gray",
        ),
        variant="soft",
    )


def _row_table() -> rx.Component:
    return rx.box(
        rx.table.root(
            rx.table.header(
                rx.table.row(
                    rx.table.column_header_cell("#"),
                    rx.table.column_header_cell("Date"),
                    rx.table.column_header_cell("Description"),
                    rx.table.column_header_cell("Amount", justify="end"),
                    rx.table.column_header_cell("Status"),
                    rx.table.column_header_cell("Match"),
                ),
            ),
            rx.table.body(
                rx.foreach(
                    ImportState.preview_rows,
                    lambda row: rx.table.row(
                        rx.table.cell(row["row_number"]),
                        rx.table.cell(row["date"]),
                        rx.table.cell(
                            row["display_text"],
                            max_width="300px",
                            overflow="hidden",
                            text_overflow="ellipsis",
                            white_space="nowrap",
                        ),
                        rx.table.cell(
                            row["amount"],
                            justify="end",
                            style={"font_variant_numeric": "tabular-nums"},
                        ),
                        rx.table.cell(
                            rx.badge(
                                row["status"],
                                color_scheme=rx.match(
                                    row["status"],
                                    ("Pending", "orange"),
                                    "gray",
                                ),
                                variant="soft",
                            )
                        ),
                        rx.table.cell(
                            _confidence_badge(row["confidence"]),
                        ),
                    ),
                ),
            ),
            variant="surface",
            width="100%",
            data_testid="preview-table",
        ),
        overflow_x="auto",
        width="100%",
    )


def _parse_errors_list() -> rx.Component:
    return rx.cond(
        ImportState.has_any_parse_errors,
        rx.vstack(
            rx.callout(
                rx.vstack(
                    rx.text(
                        f"Parse errors prevented reading {ImportState.preview_error_count} row(s):",
                        weight="medium",
                    ),
                    rx.foreach(
                        ImportState.preview_parse_errors,
                        lambda err: rx.text(err, size="1"),
                    ),
                    align="start",
                    gap="0.25em",
                ),
                color_scheme="red",
                width="100%",
            ),
            width="100%",
        ),
        rx.fragment(),
    )


def _commit_button() -> rx.Component:
    return rx.cond(
        ImportState.committing,
        rx.button(
            rx.spinner(size="2"),
            "Committing…",
            disabled=True,
            color_scheme="blue",
            data_testid="commit-btn",
        ),
        rx.button(
            "Commit import",
            on_click=ImportState.handle_commit,
            disabled=ImportState.commit_disabled,
            color_scheme="blue",
            data_testid="commit-btn",
        ),
    )


def _preview_panel() -> rx.Component:
    return rx.cond(
        ImportState.preview_has_data,
        rx.vstack(
            rx.heading("Preview", size="5"),
            # Already imported — orange banner, commit blocked
            rx.cond(
                ImportState.preview_already_imported,
                rx.callout(
                    rx.hstack(
                        rx.icon("triangle-alert", size=16),
                        rx.text(
                            "This file has already been imported for this account. "
                            "Importing again would create duplicates.",
                            size="2",
                        ),
                        align="center",
                        gap="0.5em",
                    ),
                    color_scheme="orange",
                    width="100%",
                ),
                rx.fragment(),
            ),
            # Same file, different account — warning with acknowledgement checkbox
            rx.cond(
                ImportState.preview_same_file_different_account,
                rx.callout(
                    rx.vstack(
                        rx.hstack(
                            rx.icon("triangle-alert", size=16),
                            rx.text(
                                "This file was previously imported for a different account.",
                                size="2",
                                weight="medium",
                            ),
                            align="center",
                            gap="0.5em",
                        ),
                        rx.hstack(
                            rx.checkbox(
                                checked=ImportState.same_account_warning_ack,
                                on_change=ImportState.set_same_account_warning_ack,
                            ),
                            rx.text(
                                "I understand and want to proceed with this account.",
                                size="2",
                            ),
                            align="center",
                            gap="0.5em",
                        ),
                        align="start",
                        gap="0.5em",
                    ),
                    color_scheme="yellow",
                    width="100%",
                ),
                rx.fragment(),
            ),
            # Summary stats
            _summary_row(),
            # Balance check
            _balance_check_indicator(),
            # Parse errors
            _parse_errors_list(),
            # Row table
            _row_table(),
            # Commit controls
            _commit_button(),
            align="start",
            gap="1em",
            width="100%",
        ),
        rx.fragment(),
    )


# ---------------------------------------------------------------------------
# UI components — commit summary
# ---------------------------------------------------------------------------


def _commit_summary_panel() -> rx.Component:
    return rx.cond(
        ImportState.commit_result_has_data,
        rx.vstack(
            rx.heading("Import complete", size="5"),
            rx.callout(
                rx.vstack(
                    rx.hstack(
                        rx.icon("circle-check-big", size=16, color="var(--green-9)"),
                        rx.text("Transactions committed successfully.", weight="medium", size="2"),
                        align="center",
                        gap="0.5em",
                    ),
                    rx.hstack(
                        _stat_tile("New transactions", ImportState.commit_result_new_transactions),
                        _stat_tile("Reused", ImportState.commit_result_reused_transactions),
                        _stat_tile("Pending observations", ImportState.commit_result_pending_observations),
                        _stat_tile("Enqueued jobs", ImportState.commit_result_enqueued_jobs),
                        gap="2em",
                        flex_wrap="wrap",
                    ),
                    align="start",
                    gap="0.75em",
                ),
                color_scheme="green",
                width="100%",
                data_testid="success-banner",
            ),
            align="start",
            gap="0.75em",
            width="100%",
        ),
        rx.fragment(),
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@rx.page(
    route="/import",
    title="Import | Home Finance",
    on_load=ImportState.load_accounts,
)
def import_page() -> rx.Component:
    return shell(
        rx.vstack(
            rx.heading("Import", size="7"),
            _error_banner(),
            _account_selector(),
            _upload_area(),
            _coverage_fields(),
            _completeness_checkbox(),
            _preview_panel(),
            _commit_summary_panel(),
            align="start",
            gap="1.5em",
            width="100%",
            max_width="900px",
        )
    )
