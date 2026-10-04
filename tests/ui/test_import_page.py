"""Playwright browser tests for the Import page."""
from __future__ import annotations

import textwrap
import uuid
from pathlib import Path

from playwright.sync_api import Page, expect

# Deterministic testid for the Common account option, derived from the seed migration
# (c1a2b3d4e5f6_seed_initial_accounts.py) which uses uuid5(NAMESPACE_DNS, "home-finances.common").
_COMMON_ACCOUNT_TESTID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.common"))

# 3-row CSV with 2022 dates and descriptions prefixed with the test name.
# Dates and descriptions are unique to this test to avoid collision with other tests.
_REIMPORT_CSV = textwrap.dedent("""\
    "Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
    "01.06.2022";"Groceries";"Food";"test_reimport_same_file_blocked Supermarket";"-12,00";"600,00";"Executed";""
    "02.06.2022";"Transport";"Bus";"test_reimport_same_file_blocked Transit";"-6,00";"594,00";"Executed";""
    "03.06.2022";"Shopping";"Other";"test_reimport_same_file_blocked Store";"-9,00";"585,00";"Executed";""
""")

# 3-row CSV with 2021 dates and descriptions prefixed with the test name.
# Balance values are internally consistent.
_SUCCESS_COUNT_CSV = textwrap.dedent("""\
    "Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
    "01.03.2021";"Groceries";"Food";"test_import_success_shows_new_count Supermarket";"-10,00";"500,00";"Executed";""
    "02.03.2021";"Transport";"Bus";"test_import_success_shows_new_count Transit";"-5,00";"495,00";"Executed";""
    "03.03.2021";"Shopping";"Other";"test_import_success_shows_new_count Store";"-8,00";"487,00";"Executed";""
""")

# Valid Finnish-bank-format CSV with 5 executed rows.
_VALID_CSV = textwrap.dedent("""\
    "Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
    "01.01.2026";"Groceries";"Food";"K-Supermarket";"-25,50";"1.100,00";"Executed";""
    "02.01.2026";"Transport";"Bus";"HSL";"-3,50";"1.096,50";"Executed";""
    "03.01.2026";"Groceries";"Food";"Prisma";"-45,20";"1.051,30";"Executed";""
    "04.01.2026";"Shopping";"Other";"Test Shop";"-15,00";"1.036,30";"Executed";""
    "05.01.2026";"Utilities";"Other";"Utility Store";"-10,00";"1.026,30";"Executed";""
""")

# CSV whose header uses comma separators and wrong column names — triggers header errors.
_BAD_HEADER_CSV = textwrap.dedent("""\
    Date,Category,Description
    2026-01-01,Food,K-Supermarket
""")

# 2-row CSV with 2024 dates where both rows share identical date, amount, and description.
# Unique year (2024) and test-name prefix prevent collision with other tests.
_DUPLICATE_ROWS_CSV = textwrap.dedent("""\
    "Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
    "15.04.2024";"Groceries";"Food";"test_import_duplicate_rows_both_visible Supermarket";"-33,00";"700,00";"Executed";""
    "15.04.2024";"Groceries";"Food";"test_import_duplicate_rows_both_visible Supermarket";"-33,00";"667,00";"Executed";""
""")

# 2-row CSV with 2025 dates where row 2's balance is deliberately inconsistent.
# Row 1: balance=800,00; Row 2: amount=-15,00 → correct balance would be 785,00,
# but the CSV supplies 790,00, creating an arithmetic mismatch.
# Unique year (2025) and test-name prefix prevent collision with other tests.
_BALANCE_MISMATCH_CSV = textwrap.dedent("""\
    "Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
    "10.08.2025";"Groceries";"Food";"test_import_balance_mismatch_shows_warning Supermarket";"-20,00";"800,00";"Executed";""
    "11.08.2025";"Transport";"Bus";"test_import_balance_mismatch_shows_warning Transit";"-15,00";"790,00";"Executed";""
""")

# 3-row CSV with 2023 dates, all rows Pending with empty Balance.
# Dates and descriptions are unique to this test to avoid collision with other tests.
_PENDING_ONLY_CSV = textwrap.dedent("""\
    "Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
    "01.07.2023";"Groceries";"Food";"test_import_pending_only_shows_pending_stat Supermarket";"-18,00";"";"Pending";""
    "02.07.2023";"Transport";"Bus";"test_import_pending_only_shows_pending_stat Transit";"-7,00";"";"Pending";""
    "03.07.2023";"Shopping";"Other";"test_import_pending_only_shows_pending_stat Store";"-11,00";"";"Pending";""
""")

_UPLOAD_TIMEOUT_MS = 30_000
_COMMIT_TIMEOUT_MS = 30_000


def test_import_golden_path(page: Page, app_server: str, tmp_path: Path) -> None:
    """Upload a valid CSV, confirm preview appears, commit, confirm success banner."""
    csv_file = tmp_path / "valid.csv"
    csv_file.write_text(_VALID_CSV, encoding="utf-8")

    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    # Wait for accounts to load from backend (trigger transitions away from placeholder).
    # Without this wait, clicking the trigger may open an empty dropdown while the
    # Reflex state update is still in flight, which causes the re-render to close it.
    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=15_000
    )

    # Select "Common account" from the Radix Select dropdown.
    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_TESTID).click()

    # Upload the synthetic CSV via the hidden file input inside the drop zone.
    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    # Preview table must appear before commit is possible.
    expect(page.get_by_test_id("preview-table")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )

    # Commit the import.
    page.get_by_test_id("commit-btn").click()

    # Success banner confirms the commit completed.
    expect(page.get_by_test_id("success-banner")).to_be_visible(
        timeout=_COMMIT_TIMEOUT_MS
    )


def test_import_bad_header(page: Page, app_server: str, tmp_path: Path) -> None:
    """Upload a CSV with a malformed header; expect the error banner to appear."""
    csv_file = tmp_path / "bad_header.csv"
    csv_file.write_text(_BAD_HEADER_CSV, encoding="utf-8")

    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    # Account is auto-selected by the page on load (first active account).
    # Upload the malformed CSV; header validation will fail and surface an error.
    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    expect(page.get_by_test_id("error-banner")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )


def test_import_success_shows_new_count(
    page: Page, app_server: str, tmp_path: Path
) -> None:
    """Commit a 3-row CSV and verify the success banner reports exactly 3 new transactions."""
    csv_file = tmp_path / "success_count.csv"
    csv_file.write_text(_SUCCESS_COUNT_CSV, encoding="utf-8")

    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    # Wait for accounts to load before interacting with the dropdown.
    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=15_000
    )

    # Select the Common account.
    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_TESTID).click()

    # Upload the 3-row CSV.
    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    # Wait for preview table before committing.
    expect(page.get_by_test_id("preview-table")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )

    page.get_by_test_id("commit-btn").click()

    # Success banner must appear after commit.
    expect(page.get_by_test_id("success-banner")).to_be_visible(
        timeout=_COMMIT_TIMEOUT_MS
    )

    # The new-transaction count tile must report exactly 3.
    expect(page.get_by_test_id("commit-result-new-count")).to_contain_text("3")


def test_reimport_same_file_blocked(
    page: Page, app_server: str, tmp_path: Path
) -> None:
    """Commit a CSV once, then re-upload the identical bytes; assert reimport is blocked."""
    csv_file = tmp_path / "reimport.csv"
    csv_file.write_text(_REIMPORT_CSV, encoding="utf-8")

    # --- First import: commit the file so it is recorded in the DB ---
    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=15_000
    )

    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_TESTID).click()

    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    expect(page.get_by_test_id("preview-table")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )

    page.get_by_test_id("commit-btn").click()

    expect(page.get_by_test_id("success-banner")).to_be_visible(
        timeout=_COMMIT_TIMEOUT_MS
    )

    # --- Second import: re-upload the identical file ---
    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=15_000
    )

    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_TESTID).click()

    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    # Already-imported banner must appear inside the preview panel.
    expect(page.get_by_test_id("already-imported-banner")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )

    # Commit button must be disabled — reimporting the same file is blocked.
    expect(page.get_by_test_id("commit-btn")).to_be_disabled()


def test_import_pending_only_shows_pending_stat(
    page: Page, app_server: str, tmp_path: Path
) -> None:
    """Upload a CSV where every row is Pending with empty Balance.

    Asserts that the preview shows a non-zero pending count and zero new-transaction
    count, and that the commit button is enabled (pending rows are valid to commit).
    """
    csv_file = tmp_path / "pending_only.csv"
    csv_file.write_text(_PENDING_ONLY_CSV, encoding="utf-8")

    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    # Wait for accounts to load before interacting with the dropdown.
    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=15_000
    )

    # Select the Common account.
    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_TESTID).click()

    # Upload the pending-only CSV.
    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    # Preview table must appear before assertions.
    expect(page.get_by_test_id("preview-table")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )

    # Pending stat must be non-zero (3 pending rows).
    pending_stat = page.get_by_test_id("preview-stat-pending")
    expect(pending_stat).to_be_visible()
    pending_text = pending_stat.inner_text()
    assert int(pending_text.strip()) > 0, (
        f"Expected preview-stat-pending to be non-zero, got {pending_text!r}"
    )

    # New-transaction stat must be zero (all rows are pending, none are executed).
    new_stat = page.get_by_test_id("preview-stat-new")
    expect(new_stat).to_be_visible()
    new_text = new_stat.inner_text()
    assert int(new_text.strip()) == 0, (
        f"Expected preview-stat-new to be 0, got {new_text!r}"
    )

    # Commit button must be enabled — pending rows are valid to commit.
    expect(page.get_by_test_id("commit-btn")).to_be_enabled()


def test_import_duplicate_rows_both_visible(
    page: Page, app_server: str, tmp_path: Path
) -> None:
    """Upload a CSV with two rows sharing the same date, amount, and description.

    Asserts that the preview table renders at least 2 rows — confirming neither
    duplicate was collapsed or silently deduplicated in the UI (acceptance criterion A04).
    """
    csv_file = tmp_path / "duplicate_rows.csv"
    csv_file.write_text(_DUPLICATE_ROWS_CSV, encoding="utf-8")

    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    # Wait for accounts to load before interacting with the dropdown.
    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=15_000
    )

    # Select the Common account.
    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_TESTID).click()

    # Upload the duplicate-rows CSV.
    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    # Preview table must appear before row-count assertion.
    expect(page.get_by_test_id("preview-table")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )

    # Both duplicate rows must be visible — neither should be collapsed.
    row_count = page.get_by_test_id("preview-row").count()
    assert row_count >= 2, (
        f"Expected at least 2 preview rows for duplicate CSV, got {row_count}"
    )


def test_import_balance_mismatch_shows_warning(
    page: Page, app_server: str, tmp_path: Path
) -> None:
    """Upload a CSV with a deliberate balance arithmetic inconsistency.

    Row 1 balance (800.00) + row 2 amount (-15.00) = 785.00, but the CSV
    supplies 790.00 for row 2's balance. The preview panel must surface the
    balance-check-mismatch warning before any commit action is taken.
    """
    csv_file = tmp_path / "balance_mismatch.csv"
    csv_file.write_text(_BALANCE_MISMATCH_CSV, encoding="utf-8")

    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    # Wait for accounts to load before interacting with the dropdown.
    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=15_000
    )

    # Select the Common account.
    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_TESTID).click()

    # Upload the CSV with the deliberate balance inconsistency.
    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )

    # Preview table must appear before the mismatch warning assertion.
    expect(page.get_by_test_id("preview-table")).to_be_visible(
        timeout=_UPLOAD_TIMEOUT_MS
    )

    # The balance mismatch warning must be visible before the user commits.
    expect(page.get_by_test_id("balance-check-mismatch")).to_be_visible()
