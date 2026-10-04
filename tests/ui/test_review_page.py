"""Playwright browser tests for the Review page — interactive flows.

Covered acceptance criteria:
  AC-7 golden path: import a CSV, navigate to /review, classify the first queued
        transaction via the type selector, assert the queue count decrements.
  AC-8 transfer confirmation: seed two mirrored transactions via the service layer,
        navigate to /review, confirm the suggested transfer pair, assert both rows
        are removed from the queue and queue_count decrements by one.

Each test is independent, idempotent, and tears down its own DB state via the
classification/transfer actions (confirmed rows no longer appear in the queue).
"""
from __future__ import annotations

import datetime
import textwrap
import uuid

import sqlalchemy as sa
from playwright.sync_api import Page, expect
from sqlalchemy.orm import sessionmaker

from storage.models import Transaction

# Deterministic account IDs from the seed migration (uuid5 of "home-finances.<slug>").
_COMMON_ACCOUNT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.common"))
_ACCRUAL_ACCOUNT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.accrual"))

# 1-row CSV dated 2027 — unique year avoids collision with import-page test fixtures.
_REVIEW_GOLDEN_PATH_CSV = textwrap.dedent("""\
    "Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"
    "15.06.2027";"Groceries";"Food";"test_review_golden_path Supermarket";"-20,00";"1.000,00";"Executed";""
""")

_UPLOAD_TIMEOUT_MS = 30_000
_COMMIT_TIMEOUT_MS = 30_000
_INTERACT_TIMEOUT_MS = 15_000


def test_review_golden_path(page: Page, app_server: str, tmp_path) -> None:
    """AC-7: import a CSV, classify the first queued row, assert queue count decrements."""
    csv_file = tmp_path / "review_golden_path.csv"
    csv_file.write_text(_REVIEW_GOLDEN_PATH_CSV, encoding="utf-8")

    # --- Step 1: import the 2027-dated CSV via /import ---
    page.goto(f"{app_server}/import")
    page.wait_for_load_state("networkidle")

    # Wait for account dropdown to populate before interacting.
    expect(page.get_by_test_id("account-select")).not_to_have_text(
        "Select account", timeout=_INTERACT_TIMEOUT_MS
    )
    page.get_by_test_id("account-select").click()
    page.get_by_test_id(_COMMON_ACCOUNT_ID).click()

    page.get_by_test_id("csv-upload").locator('input[type="file"]').set_input_files(
        str(csv_file)
    )
    expect(page.get_by_test_id("preview-table")).to_be_visible(timeout=_UPLOAD_TIMEOUT_MS)
    page.get_by_test_id("commit-btn").click()
    expect(page.get_by_test_id("success-banner")).to_be_visible(timeout=_COMMIT_TIMEOUT_MS)

    # --- Step 2: navigate to /review ---
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_test_id("review-heading")).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # The 2027 transaction has no accepted classification, so it appears in the queue.
    count_text = page.get_by_test_id("review-queue-count").inner_text()
    initial_count = int(count_text.split()[0])
    assert initial_count > 0, f"Expected non-empty review queue, got {count_text!r}"

    # --- Step 3: expand the first row (date 2027, most recent in the queue) ---
    page.get_by_test_id("review-expand-btn").first.click()

    # Classification panel must appear after selecting a transaction.
    expect(page.get_by_test_id("type-selector")).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # --- Step 4: select "income" (no category required — confirm_enabled becomes True) ---
    page.get_by_test_id("type-selector").click()
    page.get_by_test_id("type-option-income").click()

    expect(page.get_by_test_id("confirm-btn")).to_be_enabled(timeout=_INTERACT_TIMEOUT_MS)

    # --- Step 5: confirm and assert the queue count decremented by one ---
    page.get_by_test_id("confirm-btn").click()
    expect(page.get_by_test_id("review-queue-count")).to_contain_text(
        str(initial_count - 1), timeout=_INTERACT_TIMEOUT_MS
    )


def test_review_transfer_confirmation(
    page: Page, app_server: str, app_db_url: str
) -> None:
    """AC-8: seed a mirrored transfer pair, confirm via the UI, assert both rows removed."""
    # --- Step 1: seed two transactions with opposite amounts on different accounts ---
    txn_a_id = str(uuid.uuid4())
    txn_b_id = str(uuid.uuid4())
    transfer_date = datetime.date(2030, 1, 1)  # far-future date → top of queue

    engine = sa.create_engine(app_db_url, connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine)()
    try:
        session.add(Transaction(
            id=txn_a_id,
            account_id=_COMMON_ACCOUNT_ID,
            date=transfer_date,
            amount_cents=-15000,
            currency="EUR",
            original_text="test_review_transfer_AC8 common side",
            display_text="test_review_transfer_AC8 common side",
            status="Executed",
        ))
        session.add(Transaction(
            id=txn_b_id,
            account_id=_ACCRUAL_ACCOUNT_ID,
            date=transfer_date,
            amount_cents=15000,
            currency="EUR",
            original_text="test_review_transfer_AC8 accrual side",
            display_text="test_review_transfer_AC8 accrual side",
            status="Executed",
        ))
        session.commit()
    finally:
        session.close()
        engine.dispose()

    # --- Step 2: navigate to /review (on_load triggers load_review_queue) ---
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_test_id("review-heading")).to_be_visible(timeout=_INTERACT_TIMEOUT_MS)

    # Record row count and queue_count before acting.
    rows_before = page.get_by_test_id("review-row").count()
    queue_count_text = page.get_by_test_id("review-queue-count").inner_text()
    queue_count_before = int(queue_count_text.split()[0])
    assert rows_before >= 2, f"Expected at least 2 review rows, got {rows_before}"

    # --- Step 3: expand the first row (date 2030 = most recent; one of our seeded rows) ---
    page.get_by_test_id("review-expand-btn").first.click()

    # The other 2030 transaction (opposite amount, different account) is a transfer candidate.
    expect(page.get_by_test_id("transfer-suggestion")).to_be_visible(
        timeout=_INTERACT_TIMEOUT_MS
    )

    # --- Step 4: confirm the transfer ---
    page.get_by_test_id("confirm-transfer-btn").click()

    # Both rows must leave the visible queue list (queue_items removes both entries).
    expect(page.get_by_test_id("review-row")).to_have_count(
        rows_before - 2, timeout=_INTERACT_TIMEOUT_MS
    )

    # queue_count decrements by one (a transfer pair counts as a single review action).
    expect(page.get_by_test_id("review-queue-count")).to_contain_text(
        str(queue_count_before - 1), timeout=_INTERACT_TIMEOUT_MS
    )

    # Transfer suggestion panel must be cleared after confirming.
    expect(page.get_by_test_id("transfer-suggestion")).not_to_be_visible(
        timeout=_INTERACT_TIMEOUT_MS
    )
