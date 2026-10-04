---
id: spec-06-review-page-ui
status: done
---

# Review Page UI

## Objective

Build the Review page (`/review`) where users manually classify transactions and confirm or dismiss suggested transfer matches.

## Background

Spec-05 (classification engine) delivers `services/classification_service.py`, `services/transfer_service.py`, `domain/classification.py`, and `domain/transfer_matching.py`. This spec builds the UI on top of those services.

After import, newly committed transactions have no active `Classification` row (or one with `review_state="needs_review"`). The Review page surfaces these transactions so the user can assign a type and category, or confirm a suggested transfer link. Manual overrides must survive reimport and model reruns (A11).

The page uses the same Reflex state + event handler pattern as `ui/pages/import_.py`. All interactive elements carry `data-testid` attributes.

## Acceptance Criteria

- **AC-1** (review queue): Navigating to `/review` shows a paginated list (20 per page) of transactions that have no accepted classification. Each row displays date, description (truncated), amount, and a type badge showing the current `transaction_type` or "unclassified". The list is ordered by date descending, then by `created_at` descending as a tiebreaker (deterministic order for Playwright assertions).

- **AC-2** (classification panel): Clicking a row in the review queue expands an inline panel with:
  - A type selector showing all 7 transaction types (`expense`, `refund`, `internal_transfer`, `contribution`, `income`, `external_transfer`, `unknown`).
  - A category selector populated from `config/categories.yaml` (top-level category names only).
  - A merchant text input.
  - A "Confirm" button that calls `classification_service.manual_override()` with `source="manual"`, sets `review_state="accepted"`, and removes the transaction from the review queue. On service error, show a `data-testid="review-error-banner"` callout with the error message (same pattern as the import page's `error-banner`).
  - A "Skip" button that closes the panel without changing the classification.
  - `category_id` is optional: for types where categorisation does not apply (`internal_transfer`, `contribution`, `income`, `external_transfer`, `unknown`), the category selector may be left blank and the Confirm button remains enabled.

- **AC-3** (transfer suggestion): When `domain/transfer_matching.py` finds a candidate counterpart for a transaction in another account, the expanded panel shows a "Suggested transfer match" section displaying the candidate's date, account name, and amount. A "Confirm link" button calls `transfer_service.confirm_transfer()` and marks both transactions as `internal_transfer` with `review_state="accepted"`. A "Dismiss" button dismisses the suggestion and leaves the classification unchanged.

- **AC-4** (queue count): A counter element with `data-testid="review-queue-count"` shows the total number of transactions awaiting review. It decrements by one each time a transaction is confirmed or a transfer link is confirmed (both sides).

- **AC-5** (empty state): When the review queue is empty, an element with `data-testid="review-empty-state"` is visible in place of the list.

- **AC-6** (A11 — manual override survives reimport): After a transaction is manually classified via the Review page, importing the same CSV again does not change its `review_state` or `transaction_type`. A pytest unit test (not a browser test) verifies this by calling `import_service.commit()` twice for the same file and asserting the Classification row is unchanged.

- **AC-7** (Playwright — golden path): A browser test uploads a CSV, commits it, navigates to `/review`, verifies the review queue is non-empty (`review-queue-count` > 0), expands the first row, selects a type, and clicks Confirm. The row disappears from the queue and `review-queue-count` decrements.

- **AC-8** (Playwright — transfer confirmation): A browser test seeds two transactions (one per account) with opposite amounts, navigates to `/review`, expands the row that has a transfer suggestion, clicks "Confirm link", and verifies both transactions are no longer in the review queue.

## Scope

**In scope:**
- `ui/pages/review.py` — Reflex page at route `/review`
- `tests/ui/test_review_page.py` — Playwright tests for AC-7 and AC-8
- `tests/test_classification_service.py` — unit test for AC-6 (A11 survival)
- `data-testid` attributes on all interactive elements and assertion targets
- Wiring the Review page into the nav shell (`ui/components/shell.py`)

**Out of scope:**
- LLM-suggested classifications (spec 7)
- Reporting engine or spending calculations (spec 7)
- Bulk classification or rule creation from the Review page (post-release-1)
- Pagination controls beyond a simple "Load more" or page number display

## Files to Add/Modify

- `ui/pages/review.py` — new Reflex page (state, event handlers, queue component, classification panel, transfer suggestion panel)
- `ui/components/shell.py` — add Review to the navigation links
- `tests/ui/test_review_page.py` — new Playwright test file
- `tests/test_classification_service.py` — new or extended unit test file for A11 survival

## Notes for Implementation

- The `ReviewState` Reflex state class must never hold an open SQLAlchemy session across event handler boundaries. Load the queue in `load_review_queue` event, store row dicts in a state var.
- Categories are loaded from `config/categories.yaml` at page load (same pattern as any other startup-loaded config). Store as a flat list of `{id, name}` dicts in state.
- The classification panel is expanded by storing the selected transaction ID in state (`selected_transaction_id: str = ""`). An empty string means no row is expanded.
- For AC-8, the browser test should insert transactions directly via the service layer (or via two sequential CSV imports) rather than relying on UI-only navigation to create the seed data. The `app_server` fixture provides the DB URL via `DATABASE_URL` env var; the test can construct a service and call it directly.
- Transfer suggestion UI is only shown when `transfer_matching.find_transfer_candidates()` returns at least one result for the expanded transaction. The first candidate is shown; if there are multiple, show only the highest-confidence one.
- `data-testid` values: `review-queue-count`, `review-empty-state`, `review-row` (per row), `review-expand-btn` (per row), `type-selector`, `category-selector`, `merchant-input`, `confirm-btn`, `skip-btn`, `transfer-suggestion`, `confirm-transfer-btn`, `dismiss-transfer-btn`, `review-error-banner`.
