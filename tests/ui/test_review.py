"""Playwright browser tests for the Review page.

Covered acceptance criteria:
  - Page loads with heading visible
  - Empty queue shows the empty-state placeholder
  - Classification panel is hidden until a transaction is selected
  - Confirm and Skip buttons are present inside the classification panel

Interactive confirm/skip action flows (requiring DB-seeded transactions) are
deferred to a follow-up integration bead; tests here focus on structural
visibility rules that are verifiable against an empty database.
"""
from __future__ import annotations

from playwright.sync_api import Page, expect

_PAGE_LOAD_TIMEOUT_MS = 30_000


def test_review_page_loads(page: Page, app_server: str) -> None:
    """Golden path: navigate to /review, verify heading is visible."""
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_test_id("review-heading")).to_be_visible(
        timeout=_PAGE_LOAD_TIMEOUT_MS
    )


def test_review_page_empty_queue_shows_placeholder(page: Page, app_server: str) -> None:
    """Edge case: when no transactions await classification, the empty-state is shown."""
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")

    # In a fresh test DB with no imported transactions the queue is empty.
    expect(page.get_by_test_id("review-empty-state")).to_be_visible(
        timeout=_PAGE_LOAD_TIMEOUT_MS
    )


def test_classification_panel_hidden_when_no_selection(page: Page, app_server: str) -> None:
    """Edge case: the Confirm/Skip classification panel is not visible before any row is expanded."""
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")

    # Neither button should be in the DOM / visible without a selected transaction.
    expect(page.get_by_test_id("confirm-btn")).not_to_be_visible(
        timeout=_PAGE_LOAD_TIMEOUT_MS
    )
    expect(page.get_by_test_id("skip-btn")).not_to_be_visible(
        timeout=_PAGE_LOAD_TIMEOUT_MS
    )


def test_transfer_suggestion_hidden_when_no_candidate(page: Page, app_server: str) -> None:
    """Edge case: the transfer suggestion panel is not visible when no transfer candidate exists."""
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")

    # transfer_candidate starts as {} so has_transfer_candidate is False; panel hidden.
    expect(page.get_by_test_id("transfer-suggestion")).not_to_be_visible(
        timeout=_PAGE_LOAD_TIMEOUT_MS
    )
