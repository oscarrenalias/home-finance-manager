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
