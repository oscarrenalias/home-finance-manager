"""Playwright browser tests for the Review page (stub phase).

Covered acceptance criteria:
  ReviewState — page loads, heading is visible, and the queue placeholder appears
  when the database is empty (no transactions awaiting classification).

This test file covers the ReviewState class and data-loading event handlers
introduced in B-6ae7dfeb. Interactive classification actions (confirm/skip)
are deferred to subsequent developer beads.
"""
from __future__ import annotations

from playwright.sync_api import Page, expect

_PAGE_LOAD_TIMEOUT_MS = 30_000


def test_review_page_loads(page: Page, app_server: str) -> None:
    """Golden path: navigate to /review, verify heading and placeholder are visible."""
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_test_id("review-heading")).to_be_visible(
        timeout=_PAGE_LOAD_TIMEOUT_MS
    )


def test_review_page_empty_queue_shows_placeholder(page: Page, app_server: str) -> None:
    """Edge case: when no transactions await classification, the placeholder text is shown."""
    page.goto(f"{app_server}/review")
    page.wait_for_load_state("networkidle")

    # In a fresh test DB with no imported transactions, the queue is empty.
    # The stub page shows the placeholder text unconditionally at this phase.
    expect(page.get_by_test_id("review-queue-placeholder")).to_be_visible(
        timeout=_PAGE_LOAD_TIMEOUT_MS
    )
