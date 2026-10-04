---
id: spec-04-import-page-ui-coverage
status: done
---

# Import Page UI Coverage — A01 to A07

## Objective

Extend the existing Playwright browser tests to cover the remaining import acceptance criteria (A02–A07) at the UI level, verifying that the Import page surfaces the correct information and controls for each scenario.

## Background

The golden path test (`test_import_golden_path`) and the bad-header test in `tests/ui/test_import_page.py` already cover basic upload→commit success and header error display. The `app_server` session-scoped fixture, the `browser_type_launch_args` Chrome override, and the `data-testid` strategy are all in place.

Several import scenarios are not yet covered at the UI level:
- Reimporting the same file is silently blocked (no test verifies the UI guard).
- Pending rows are shown in the preview stats but no test verifies the count.
- Two identical rows on the same date must both appear in the preview table (not collapsed).
- A balance arithmetic inconsistency must surface a warning before the user can commit.
- The success banner must show the exact new-transaction count after a commit.

Several `data-testid` attributes are missing from the import page that tests will need.

## Acceptance Criteria

- **AC-1** (data-testid): The following elements in `ui/pages/import_.py` carry `data-testid` attributes so tests can locate them:
  - Preview summary stat tiles: `preview-stat-new`, `preview-stat-pending`, `preview-stat-reused`, `preview-stat-ambiguous`
  - Balance check indicator variants: `balance-check-ok`, `balance-check-mismatch`, `balance-check-inconclusive`
  - Already-imported callout: `already-imported-banner`
  - Commit result new-count stat tile: `commit-result-new-count`
  - Each row in the preview table body: `preview-row` (applied to every `rx.table.row` inside `_row_table`'s `rx.foreach`)
  - Commit button: already present as `commit-btn` — no change needed, referenced explicitly in AC-3

- **AC-2** (A01 extended — new count in success banner): Uploading and committing a synthetic 3-row executed CSV causes the success banner to display the value `3` inside the element with `data-testid="commit-result-new-count"`.

- **AC-3** (A02 — reimport guard): After committing a CSV for the Common account, uploading the same CSV file again shows the `data-testid="already-imported-banner"` callout and the element with `data-testid="commit-btn"` has the `disabled` attribute.

- **AC-4** (A07 variant — pending-only preview): Uploading a CSV that contains only pending-status rows (no executed rows) shows a non-zero value in `data-testid="preview-stat-pending"` and zero in `data-testid="preview-stat-new"` in the preview panel. The commit button must be enabled (pending rows are valid to commit as observations).

- **AC-5** (A04 — duplicate rows both visible): Uploading a CSV with two rows that share the same date, amount, and description causes `page.get_by_test_id("preview-row").count()` to return at least 2.

- **AC-6** (balance mismatch — warning before commit): Uploading a CSV where the balance values are arithmetically inconsistent (balance after row N does not equal balance after row N-1 plus the amount of row N) causes `data-testid="balance-check-mismatch"` to be visible in the preview panel before any commit.

## Scope

**In scope:**
- Adding the eight `data-testid` attributes listed in AC-1 to `ui/pages/import_.py`.
- Adding five new test functions to `tests/ui/test_import_page.py`.
- Synthetic CSV data only — no `sample-data/` files in test fixtures.
- Each test must use CSV content with distinct field values so idempotency tokens do not collide across tests.

**Out of scope:**
- A03 (overlapping complete extracts showing reused rows) — requires two sequential commits within one test and careful CSV design; deferred.
- A05 (ambiguous overlap) — triggering the "ambiguous" confidence requires pre-existing DB rows that match a new row equally well; deferred to a fixture-driven unit test.
- A06 (pending-to-executed lifecycle) — requires two import operations from different files; deferred.
- Backend unit tests for these scenarios — already covered in `tests/`.

## Files to Add/Modify

- `ui/pages/import_.py` — add `data_testid` parameter to `_stat_tile`, update call sites in `_summary_row` and `_commit_summary_panel`, add testids to already-imported callout and balance check components, add `data_testid="preview-row"` to the `rx.table.row` inside `_row_table`'s `rx.foreach`.
- `tests/ui/test_import_page.py` — add five test functions: `test_import_success_shows_new_count`, `test_reimport_same_file_blocked`, `test_import_pending_only_shows_pending_stat`, `test_import_duplicate_rows_both_visible`, `test_import_balance_mismatch_shows_warning`.

## Notes for Implementation

- The `app_server` fixture is session-scoped: all UI tests in a session share one running Reflex server and one SQLite database. Tests that commit transactions leave rows in the DB. Each test's CSV must use distinct dates and descriptions (e.g., prefix them with the test name or use a unique year) to ensure distinct idempotency tokens and avoid interference with other tests.
- For AC-3 (reimport guard): the test must first commit the CSV, then navigate to `/import` again and upload the same bytes a second time. The `already-imported-banner` should appear without committing.
- For AC-6 (balance mismatch): construct a CSV where row 2's `Balance` field does not equal row 1's `Balance` plus row 2's `Amount` (after converting from decimal-comma notation). The balance check in `domain/balance_check.py` validates this arithmetic.
- A pending row in a CSV has `Status` = `"Pending"` and an empty `Balance` field.
