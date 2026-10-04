---
name: CSV Import Pipeline
id: spec-2ff6a3c2
description: null
dependencies: null
priority: null
complexity: null
status: planned
tags: []
scope:
  in: null
  out: null
feature_root_id: null
---
# CSV Import Pipeline

## Objective

Implement end-to-end CSV import: parse Finnish bank exports, match against existing ledger rows to expose deduplication uncertainty, commit atomically, store pending reservations separately, and validate balances. The Import page drives the workflow. Acceptance criteria A01–A07 and A18 must pass after this spec.

## Background

The scaffold created the database models (`Account`, `ImportBatch`, `SourceObservation`, `Transaction`, `Classification`) and `domain/money.py`. No parser, import service, file storage, or UI logic exists yet. The Import page stub in `ui/pages/import_page.py` renders an empty placeholder.

The Finnish CSV format uses semicolons, quoted fields, decimal commas, and `DD.MM.YYYY` dates. Category and subcategory fields are padded with trailing spaces. Merchant names may end with cosmetic `))))` suffixes. Balance is empty for pending rows. See `CLAUDE.md §CSV format` for the full parsing rules.

## Beads

### B1 — CSV parser (`domain/parser.py`)

Pure Python module. No database dependency. No Reflex import.

**Deliver:**

- `ParsedRow` dataclass: `row_number`, `raw_date`, `raw_category`, `raw_subcategory`, `raw_text`, `raw_amount`, `raw_balance`, `raw_status`, `raw_reconciled`, `parsed_date` (`date`), `parsed_amount_cents` (`int`), `parsed_balance_cents` (`int | None`), `is_pending` (`bool`), `display_text` (`str`), `parse_errors` (`list[str]`)
- `ParseResult` dataclass: `rows` (`list[ParsedRow]`), `header_errors` (`list[str]`), `parser_version` (`str`)
- `parse_csv(data: bytes) -> ParseResult` — accepts raw file bytes, handles UTF-8 BOM, falls back to latin-1 if UTF-8 decoding fails (logs warning, does not raise)
- Amount parsing: `Decimal` only, no float; decimal comma; optional thousands-separator dot; store as signed integer cents; raise `ValueError` for malformed amounts
- Date parsing: `DD.MM.YYYY` → `datetime.date`; invalid dates populate `parse_errors` on the row, do not raise
- Status mapping: `Executed` → `is_pending=False`; `Pending` → `is_pending=True`; `Rejected` and `Deleted` → `is_pending=False` with status preserved verbatim
- `display_text`: strip trailing whitespace and strip trailing `)` runs (cosmetic suffix); preserve `original_text` (= `raw_text`) unchanged
- Validate header row matches expected columns exactly; missing/extra columns → header error, stop parsing
- Empty file, header-only file, and files with only pending rows are valid inputs
- `parser_version`: a short string constant in the module (e.g. `"1.0"`) — bump when parsing logic changes
- `PARSER_VERSION` module-level constant used by the import service

**Must not** use the `csv` module's `DictReader` with a float default for any numeric field.

---

### B2 — Import file store (`storage/file_store.py`)

Stores raw upload bytes outside the database, outside any web-accessible directory.

**Deliver:**

- `FileStore` class initialized with a root directory path (from env `IMPORT_FILES_DIR`, default `/data/imports`)
- `save(account_id: str, filename: str, data: bytes) -> tuple[str, str]` — writes `<root>/<account_id>/<sha256>.bin`, returns `(path, sha256_hex)`; uses `sha256` of the raw bytes as the filename; atomic write (write to `.tmp` then rename)
- `exists(sha256_hex: str, account_id: str) -> bool`
- Path traversal prevention: reject filenames containing `..` or `/`; sanitize account_id to alphanumeric + `-`

---

### B3 — Duplicate matcher (`domain/matching.py`)

Stateless matching logic. No database calls. Takes parsed rows and existing transaction records as inputs.

**Deliver:**

- `MatchCandidate` dataclass: `parsed_row` (`ParsedRow`), `existing_transaction_id` (`str | None`), `confidence` (`Literal["exact", "probable", "ambiguous", "new"]`), `match_evidence` (`list[str]`)
- `match_rows(parsed_rows: list[ParsedRow], existing: list[ExistingRecord]) -> list[MatchCandidate]`
- `ExistingRecord` dataclass: `transaction_id`, `date`, `amount_cents`, `display_text`, `balance_after`, `status`
- Matching evidence (all for executed rows): exact date + exact amount + normalized text match + balance match → `"exact"`; date + amount + text without balance (or balance missing on one side) → `"probable"`; date + amount only, multiple candidates → `"ambiguous"`; no match → `"new"`
- One-to-one matching: once a candidate is matched, remove it from the pool; preserve multiplicity (two identical rows both remain)
- Pending rows (`is_pending=True`) are always `"new"` — never match to existing transactions
- Text normalization for matching: lowercase, collapse whitespace, strip cosmetic suffix — same logic as `display_text` in the parser; do not use the bank category or app classification as matching evidence

---

### B4 — Import service (`services/import_service.py`)

Coordinates parser, matcher, file store, and database. Entry point for the Import UI.

**Deliver:**

`ImportService` class with:

- `preview(account_id: str, filename: str, data: bytes, coverage_start: date | None, coverage_end: date | None, completeness: bool) -> ImportPreview`

  Parses and matches without writing to the database. Returns:

  - `file_hash`, `parser_version`, `idempotency_token` (UUID, generated once per preview call)
  - `already_imported: bool` — True when `file_hash` matches an existing committed batch for this account
  - `same_file_different_account: bool` — True when hash matches a batch on a different account
  - `executed_count`, `pending_count`, `new_count`, `exact_match_count`, `probable_match_count`, `ambiguous_count`, `error_count`
  - `row_previews: list[RowPreview]` — one per parsed row: `row_number`, `display_text`, `parsed_date`, `amount_cents`, `is_pending`, `match_candidate` (`MatchCandidate`)
  - `parse_errors: list[str]` — header-level errors
  - `balance_check: BalanceCheckResult` (see B5)

- `commit(account_id: str, idempotency_token: str, preview: ImportPreview, coverage_start: date | None, coverage_end: date | None, completeness: bool) -> CommitResult`

  Idempotent. If a batch with this `idempotency_token` already exists and is `committed`, return its `CommitResult` immediately (A02 / A18).

  Within a single database transaction:
  1. Re-run matching against current ledger state (ledger may have changed since preview).
  2. Create `ImportBatch` with state `"committed"`.
  3. For each `"exact"` match: create `SourceObservation` linked to the existing `Transaction`, no new transaction.
  4. For `"probable"` and `"ambiguous"` executed rows: create new `Transaction` + `SourceObservation` with `transaction_type="unknown"`, enqueue a `Job(kind="classify", inputs={"transaction_id": ...})`.
  5. For `"new"` executed rows: same as above.
  6. For pending rows: create `SourceObservation(is_pending=True, transaction_id=None)`.
  7. Update batch counts.
  8. Emit `AuditEvent` for the batch commit (`AuditEvent` is already modelled in `storage/models.py` from the scaffold; use `entity_type="import_batch"`, `action="committed"`, `actor="system"`).

  `CommitResult`: `batch_id`, `new_transactions`, `reused_transactions`, `pending_observations`, `enqueued_jobs`, `ambiguous_count`. Parse errors and balance check results are preview-only — `CommitResult` is intentionally minimal; the caller already holds the preview.

- Serialize imports per account: use a module-level `threading.Lock` keyed by `account_id` (held only during the database transaction) to prevent concurrent commits for the same account (A18). SQLite does not support row-level locks; a threading lock is sufficient for a single-process deployment.

- `ImportPreview` and `CommitResult` are plain dataclasses; no Reflex dependency.

---

### B5 — Balance validator (`domain/balance_check.py`)

**Deliver:**

- `BalanceCheckResult` dataclass: `checked_pairs: int`, `mismatches: list[BalanceMismatch]`, `inconclusive_reasons: list[str]`
- `BalanceMismatch` dataclass: `row_number_a`, `row_number_b`, `expected_cents`, `actual_cents`
- `check_balances(rows: list[ParsedRow]) -> BalanceCheckResult`
  - Works only on executed rows with non-null `parsed_balance_cents`
  - Handles both ascending and descending date order (detect from the data)
  - For each consecutive pair: `balance_a + amount_b == balance_b`; mismatch → append to `mismatches`
  - Same-day ambiguous ordering → append to `inconclusive_reasons`, skip the pair
  - Fewer than two usable rows → return zero checked pairs with no error

---

### B6 — Import UI (`ui/pages/import_page.py`)

Replace the placeholder stub. No new Reflex patterns — follow the existing page structure.

**Deliver:**

State class `ImportState(rx.State)`:
- Fields: `account_options: list[tuple[str,str]]` (id, name), `selected_account_id: str`, `filename: str`, `coverage_start: str`, `coverage_end: str`, `completeness: bool`, `preview: ImportPreview | None`, `committing: bool`, `commit_result: CommitResult | None`, `error_message: str`
- `@rx.event_handler load_accounts()` — queries `Account` table, populates `account_options`
- `@rx.background_event handle_upload(files)` — reads first file, calls `ImportService.preview(...)`, updates state; sets `error_message` on exception
- `@rx.background_event handle_commit()` — calls `ImportService.commit(...)`, updates state; clears preview on success
- No file bytes stored in state; pass to service immediately and discard

Page layout (top to bottom):
1. **Account selector** — dropdown bound to `account_options`
2. **Upload area** — `rx.upload` accepting `.csv` files, single file, max 10 MB
3. **Coverage fields** — optional start/end date inputs + completeness checkbox; show tooltip explaining what "complete export" means
4. **Preview panel** — shown only when `preview` is set:
   - Summary row: total rows / new / reused / ambiguous / pending / errors
   - `already_imported` banner when True (orange, not an error)
   - `same_file_different_account` warning when True (requires explicit acknowledgement before commit)
   - Balance check: green tick if zero mismatches and >0 checked pairs; orange "inconclusive" if no pairs checked; red list of mismatches
   - Row table: `row_number`, `date`, `display_text`, `amount` (EUR formatted), `status`, `match` badge (`new` / `exact` / `probable` / `ambiguous`)
   - Commit button (disabled when `already_imported` or parse errors present)
5. **Progress indicator** — spinner visible while `committing`
6. **Commit summary** — shown after successful commit: new transactions, reused, pending, jobs enqueued

Error states: parse failure, service error, network error — shown in `error_message` banner.

---

### B7 — Tests (A01–A07, A18)

Use synthetic CSV bytes, not `sample-data/`. Domain modules only (no Reflex import in tests). Use the in-memory SQLite database from `tests/conftest.py`.

**Cover:**

| AC | Test |
|----|------|
| A01 | Parse a synthetic CSV with decimal commas, whitespace padding, BOM, null balance, and all status values; assert `ParsedRow` fields |
| A02 | Commit a batch, commit the identical bytes again; assert second commit returns same `batch_id`, zero `new_transactions` |
| A03 | Two overlapping complete extracts — shared rows reused, genuinely new rows inserted once |
| A04 | Two identical purchases (same date, amount, text) in one CSV — both committed as separate transactions |
| A05 | CSV with missing balances and indistinguishable rows — `ambiguous_count > 0`, no silent merge |
| A06 | Pending row in first import disappears in second; a similar executed row appears — executed row counted once, no assumed identity |
| A07 | Pending-only CSV (no executed rows) — imports cleanly, `executed_count == 0`, `pending_count > 0` |
| A18 | Two concurrent `commit()` calls for the same account with the same `idempotency_token` — exactly one set of transactions inserted |

Also add unit tests for:
- `parse_csv` with malformed amounts, bad dates, wrong header, encoding fallback
- `match_rows` — exact, probable, ambiguous, multiplicity preservation
- `check_balances` — ascending and descending order, same-day inconclusive, mismatch detection

## Dependencies

- Depends on spec-22c4c181 (scaffold) — merged.
- B1 and B2 have no inter-dependencies; both can start immediately.
- B3 depends on B1's `ParsedRow` interface being stable.
- B4 depends on B1, B2, B3, B5.
- B6 depends on B4.
- B7 depends on B1, B3, B4, B5.

## Out of scope

- LLM classification (the `classify` job is enqueued but not executed)
- Classification UI or review queue
- Transfer matching
- Account creation UI (accounts are seeded via migration or settings; settings page is a later spec)
- Chart visualizations
