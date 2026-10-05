---
name: Transaction Ledger
id: spec-965fdef9
description: "Searchable, filterable, paginated Transactions page with a detail view showing source rows, classification history, transfer links, in-place classification editing, and free-text notes."
dependencies:
- spec-09
priority: high
complexity: medium
status: planned
tags:
- ui
- ledger
- transactions
scope:
  in: "[transaction query service, /transactions page, detail panel, manual correction from ledger, transaction notes + migration, URL filter params, UI + unit tests]"
  out: "[reporting totals, budgets, CSV export, pending-reservation view, bulk edits, rule creation]"
feature_root_id: B-20b96cfe
---
# Transaction Ledger

## Objective

Replace the `/transactions` placeholder with a working ledger: every executed transaction can be found, filtered, sorted, and inspected; its classification can be corrected in place; and any transaction can carry a free-text note. This is the drill-down target for the Overview page (spec 11) and the Ask page later, so filters must be addressable by URL.

## Background

Imports (spec 02) create `Transaction` rows linked to `SourceObservation` rows. Classification (specs 05, 09) adds `Classification` rows (one or more per transaction; history is preserved). Transfer confirmation (spec 06) creates `TransferLink` rows. None of this is browsable today — the Review page only shows unaccepted items.

Product spec §11: *"Transactions: filters, sorting, pagination, merchant/category/type fields, review status. Detail view includes source rows, classification history, and transfer links."* Display rules: EUR formatting, day-month-year dates.

## Changes

### 1. Query service — `src/services/transaction_query.py` (new)

Plain Python, no Reflex import. All filtering, sorting, and pagination happen in SQL (LIMIT/OFFSET + COUNT), never by loading the full table into memory — the release target is 100k transactions.

```python
@dataclass(frozen=True)
class LedgerFilters:
    date_from: date | None = None
    date_to: date | None = None          # inclusive
    account_ids: tuple[str, ...] = ()
    transaction_types: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()   # "__none__" matches uncategorised
    review_states: tuple[str, ...] = ()  # "accepted" | "needs_review" | "unclassified"
    search: str = ""                     # case-insensitive substring on display_text, merchant, and note
    include_rejected_deleted: bool = False  # bank status Rejected/Deleted hidden by default

@dataclass(frozen=True)
class LedgerPage:
    rows: list[LedgerRow]
    total_count: int
    page: int
    page_size: int

PAGE_SIZES = (20, 50, 100)

def list_transactions(session, filters, sort="date_desc", page=0, page_size=50) -> LedgerPage  # page_size must be in PAGE_SIZES
def set_note(session, transaction_id, note: str | None) -> None  # caller commits
def get_transaction_detail(session, transaction_id) -> TransactionDetail | None
```

- **Current classification** for a transaction = the most recent non-rejected `Classification` row (same rule as `review.py::_transaction_to_dict`). Move that rule into this service and have the Review page call it, so the two pages can't disagree.
- `review_state` filter value `unclassified` = no classification row at all.
- Sort options: `date_desc` (default), `date_asc`, `amount_desc`, `amount_asc`. Ties break on `created_at desc` then `id` for deterministic order.
- `search` is passed as a bound parameter with `%`/`_` escaped — no string-built SQL.
- `LedgerRow` fields: id, date, account_name, display_text, amount_cents, currency, status, transaction_type, category_id, category_name, merchant, review_state, classification_source, has_note.
- `set_note`: strips whitespace; empty string stores `NULL`; rejects notes over 2000 characters with `ValueError`; writes an `AuditEvent` (`entity_type="transaction"`, `action="note_updated"`, `actor="manual"`, before/after note text) only when the value actually changes.
- `TransactionDetail` adds:
  - `source_rows`: every `SourceObservation` for the transaction — batch filename, import date, row number, raw date/text/amount/balance/status, raw bank category/subcategory.
  - `classification_history`: all `Classification` rows, newest first — type, category, merchant, source (`manual`/`rule`/`llm`), review_state, rationale, model/rule version, created_at.
  - `transfer_links`: linked counterpart(s) — counterpart date, account name, amount, link state.
  - `note`, `note_updated_at`.

### 2. Transaction notes — model + migration

- Add `note: Text, nullable` and `note_updated_at: DateTime, nullable` to `Transaction` in `src/storage/models.py`.
- New Alembic revision in `src/storage/migrations/versions/` with `down_revision` = the current single head. The chain must stay linear (one head), and the revision must be PostgreSQL-compatible (plain `ALTER TABLE ... ADD COLUMN`, both columns nullable, no backfill). Include a working `downgrade()`.
- Notes are on the transaction, not the classification, so they survive reclassification, model reruns, and reimports of the same row (reimport matches the existing transaction and must not touch `note`).
- Notes are user data. Render them as plain text only. Do not send them to the LLM classifier in this spec. Do not log them.

### 3. Transactions page — `src/ui/pages/transactions.py`

- **Filter bar**: date from/to, account (multi), type (multi), category (multi, from `categories.yaml`, plus "Uncategorised"), review status (multi), free-text search, "Show rejected/deleted" toggle (off by default), "Clear filters" button.
- **Table columns**: Date (DD.MM.YYYY), Account, Description, Merchant, Category, Type badge, Review status badge, Amount (EUR, right-aligned, negative in red), and a note indicator icon when `has_note`. Rows with bank status Rejected/Deleted (visible only with the toggle on) show a status badge.
- **Sorting**: click the Date or Amount header to toggle the sort.
- **Pagination**: page-size dropdown (20 / 50 / 100, default 50; changing it resets to page 1), Prev/Next, "Showing X–Y of N".
- **URL state**: filters, sort, page, and page size are read from query params on load (`/transactions?category=groceries&from=2026-09-01&to=2026-09-30&type=expense`) and written back on change, so Overview drill-downs and browser back/forward work.
- **Detail panel**: clicking a row opens an inline panel (same interaction pattern as Review) with the three sections from `TransactionDetail`, plus an **Edit classification** form (type / category / merchant, pre-filled from the current classification). Saving calls `classification_service.manual_override` → `source="manual"`, `review_state="accepted"`; the row updates in place. This counts as a manual override, so it survives reimports and model reruns (A11).
- **Note editor** in the detail panel: textarea pre-filled with the current note, Save and Clear buttons, a character counter (max 2000), and "last edited" timestamp. Saving calls `transaction_query.set_note`; the row's note indicator updates in place. Validation errors show inline.
- **States**: empty ledger ("No transactions yet — import a CSV", link to /import), no filter matches ("No transactions match these filters" + Clear), error banner on DB failure.
- Never hold a session in state: open, query, convert to plain dicts, close — same as `review.py`.
- Rationale and source text are rendered as plain text only (A17 — untrusted data).

### 4. Review page refactor

`src/ui/pages/review.py` uses the shared "current classification" helper from `transaction_query`. Review behaviour does not change.

## Files to Modify

| File | Change |
|---|---|
| `src/services/transaction_query.py` | New — filters, sort, pagination, detail, notes, shared current-classification rule |
| `src/storage/models.py` | Add `Transaction.note`, `Transaction.note_updated_at` |
| `src/storage/migrations/versions/<new>_add_transaction_notes.py` | New Alembic revision (linear head, PostgreSQL-compatible, with downgrade) |
| `src/ui/pages/transactions.py` | Replace placeholder with ledger + detail panel |
| `src/ui/pages/review.py` | Use the shared current-classification helper |
| `tests/test_transaction_query.py` | New — unit tests (SQLite, synthetic data) |
| `tests/test_migrations.py` | Extend — single head still holds; note columns exist after upgrade |
| `tests/ui/test_transactions_page.py` | New — Playwright tests |

## Acceptance Criteria

- **AC-1** (default list): `/transactions` shows executed transactions across all accounts, newest first, 50 per page, with the columns listed above. Bank status Rejected/Deleted rows are hidden unless the toggle is on (unit test on `include_rejected_deleted`). `data-testid="ledger-row"` per row, `data-testid="ledger-total-count"` showing N.
- **AC-2** (filters): each filter narrows results correctly, and combined filters AND together. Unit tests cover each filter on its own, a combination, the `__none__` category, the `unclassified` review state, and a date range with inclusive bounds.
- **AC-3** (search): search matches case-insensitive substrings in `display_text`, `merchant`, and `note`. A search containing `%` or `_` matches those characters literally (unit test).
- **AC-4** (sort): all four sort orders are deterministic, including ties (unit test with equal dates and amounts).
- **AC-5** (pagination in SQL): `list_transactions` returns `total_count` from a COUNT query and only `page_size` rows. A unit test seeds 120 rows and checks pages 0, 1 and 2 (50/50/20) with no overlap, page sizes 20 and 100 also work, and an unsupported page size raises `ValueError`. The page-size dropdown (`data-testid="ledger-page-size"`) changes the row count and resets to page 1 (Playwright).
- **AC-6** (URL drill-down): loading `/transactions?type=expense&category=groceries` pre-applies both filters (Playwright). Changing a filter updates the URL.
- **AC-7** (detail view): the detail panel shows source rows with batch filename and raw fields, the full classification history newest first, and transfer links when present (unit test on `get_transaction_detail` plus Playwright golden path).
- **AC-8** (correction from ledger): saving an edit writes a `manual`/`accepted` Classification and keeps the earlier rows in history. Re-running `handle_classify_batch` on the same batch leaves it unchanged (A11 unit test).
- **AC-9** (A04 multiplicity): two identical purchases on the same date appear as two rows.
- **AC-10** (A17): a transaction whose description contains HTML/script-like text renders it literally (Playwright asserts the text node, and no injected element exists).
- **AC-11** (consistency): the Review page and ledger show the same current type/category for the same transaction (unit test calling the shared helper).
- **AC-12** (Playwright): golden path (filter → open detail → edit classification → row updates) plus edge cases (empty ledger state, no-match state with Clear). `data-testid` locators only.

- **AC-13** (notes — service): `set_note` stores, updates, and clears a note (empty → `NULL`), sets `note_updated_at`, rejects over 2000 characters, and writes exactly one `note_updated` AuditEvent per actual change (none when unchanged). Unit tests.
- **AC-14** (notes survive): a note is unchanged after (a) re-importing the same CSV, (b) re-running `handle_classify_batch`, and (c) a manual classification edit. Unit test.
- **AC-15** (notes — migration): `alembic upgrade head` adds both columns, `downgrade -1` removes them, and the revision chain still has a single head. Extend `tests/test_migrations.py`.
- **AC-16** (notes — UI): Playwright golden path: open detail → type a note → Save → the indicator appears on the row → reload page → the note persists. Edge case: a note containing `<script>` renders literally. `data-testid`: `note-input`, `note-save-btn`, `note-clear-btn`, `note-indicator`, `note-error`.

## Out of Scope

- Totals, spending sums, period comparisons (spec 11 reporting engine)
- Pending reservations view (shown separately later; ledger is executed transactions only)
- CSV export (needs formula-injection protection — separate spec with Settings/export)
- Bulk edit, rule creation from a transaction, transfer linking from the ledger (Review owns linking)
- Multiple/threaded notes per transaction, and feeding notes to the LLM or Ask tools (possible later)

## Decisions

1. **Page size**: default 50; dropdown offers 20 / 50 / 100.
2. **Rejected / Deleted bank-status rows**: hidden by default; "Show rejected/deleted" toggle reveals them with a status badge.
3. **Editing in the ledger**: included — edit classification (manual override) and free-text notes.
4. **Notes model**: one note per transaction, stored as a column on `transactions`, max 2000 characters, audited on change.
