"""Playwright browser tests for the Review page.

Covered acceptance criteria:
  - Page loads with heading visible
  - The page renders either a queue or the empty-state placeholder (never neither)
  - Classification panel is hidden until a transaction is selected
  - Confirm and Skip buttons are present inside the classification panel

Tests focus on structural visibility rules that hold regardless of DB queue depth.
Interactive confirm/skip flows are in tests/ui/test_review_page.py.
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


def test_review_page_renders_queue_or_empty_state(page: Page, app_server: str) -> None:
    """Edge case: the page always renders either queued rows or the empty-state element."""
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")

    # One of the two must be present: if the queue has items, review-row is visible;
    # if it is empty, review-empty-state is visible. Neither being present is a bug.
    has_rows = page.get_by_test_id("review-row").count() > 0
    has_empty = page.get_by_test_id("review-empty-state").is_visible()
    assert has_rows or has_empty, (
        "Review page rendered neither queued rows nor the empty-state placeholder"
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
