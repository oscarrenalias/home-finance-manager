---
id: spec-05-classification-engine
status: done
---

# Classification Engine

## Objective

Implement the backend classification engine: rules storage, precedence resolution, manual overrides, transfer linking, and post-import rule application — covering acceptance criteria A08–A12 at the domain and service layer, with no UI work.

## Background

The `Classification`, `AuditEvent`, and `Transaction` models are already defined in `storage/models.py`. The import pipeline (`services/import_service.py`) already creates transactions and enqueues jobs, but it does not classify them. Two models are missing: `ClassificationRule` (text/merchant pattern → type + category + merchant) and `TransferLink` (confirmed transfer pair between two transactions). No classification domain or service modules exist yet.

The classification precedence is:
1. Manual override (`source=manual`, `review_state=accepted`)
2. User-confirmed rules (ordered by `priority` desc, then `created_at` asc)
3. Validated LLM suggestion (`source=llm`, `review_state=accepted`)
4. `unknown` / needs review

Transfer detection is separate: a `TransferLink` between two transactions overrides both to `internal_transfer` regardless of other rules.

## Acceptance Criteria

- **AC-1** (models + migration): `ClassificationRule` and `TransferLink` tables exist after `alembic upgrade head`. `ClassificationRule` has `id`, `priority` (int, higher wins), `pattern_type` (`text_contains` | `merchant_exact` | `merchant_contains`), `pattern_value`, `transaction_type`, `category_id` (nullable), `merchant` (nullable), `is_confirmed` (bool), `created_at`, `updated_at`. `TransferLink` has `id`, `transaction_a_id` (FK → transactions), `transaction_b_id` (FK → transactions), `confirmed_by` (`manual` | `rule`), `created_at`. Downgrade removes both tables cleanly.

- **AC-2** (rules engine): `domain/classification.py` exposes `match_rules(display_text: str, merchant: str | None, rules: list[ClassificationRule]) -> ClassificationRule | None` — returns the highest-priority confirmed rule whose pattern matches, or `None`. Pattern matching is case-insensitive substring (`text_contains`, `merchant_contains`) or exact (`merchant_exact`). The function is pure — no database access.

- **AC-3** (precedence resolver): `domain/classification.py` exposes `resolve_active(classifications: list[Classification], has_transfer_link: bool) -> Classification | None` — returns the single effective classification per precedence. If `has_transfer_link` is `True`, the result always has `transaction_type=internal_transfer` regardless of stored classifications. Returns `None` when the list is empty (caller treats as `unknown/needs_review`).

- **AC-4** (classification service): `services/classification_service.py` exposes:
  - `apply_classification(session, transaction_id, source, transaction_type, category_id, merchant, rationale) -> Classification` — inserts a `Classification` row and a corresponding `AuditEvent` with `action=classified`, `actor=source`. Raises `ValueError` for unknown `source` or `transaction_type`.
  - `manual_override(session, transaction_id, transaction_type, category_id, merchant) -> Classification` — calls `apply_classification` with `source=manual`, sets `review_state=accepted` on the new row, writes audit event with `actor=manual`.
  - `resolve_active_classification(session, transaction_id) -> Classification | None` — loads all `Classification` rows and any `TransferLink` for the transaction and delegates to `domain.classification.resolve_active`.
  - `create_rule(session, pattern_type, pattern_value, transaction_type, category_id, merchant, priority) -> ClassificationRule` — inserts a confirmed (`is_confirmed=True`) rule row. Raises `ValueError` for unknown `pattern_type` or `transaction_type`. This is an admin/seed path used by tests, seed scripts, and the future rule management UI.

- **AC-5** (transfer service): `services/transfer_service.py` exposes:
  - `find_transfer_candidates(session, transaction_id) -> list[Transaction]` — returns transactions in a different account with the opposite signed amount, within ±3 calendar days of the source transaction's date, not already linked.
  - `confirm_transfer(session, transaction_a_id, transaction_b_id) -> TransferLink` — creates a `TransferLink` (confirmed_by=manual), calls `apply_classification` for both transactions with `source=rule`, `transaction_type=internal_transfer`, writes one `AuditEvent` per transaction with `action=transfer_linked`.
  - `undo_transfer(session, link_id)` — deletes the `TransferLink`, writes `AuditEvent` with `action=transfer_unlinked` for both transactions. Does not delete the `Classification` rows created during confirm; they remain in history, but the transfer link's removal causes `resolve_active` to stop forcing `internal_transfer`.

- **AC-6** (post-import rule application): After `import_service.commit()` inserts new transactions, it applies all `is_confirmed=True` `ClassificationRule` rows to each newly inserted executed transaction. Matches call `apply_classification` with `source=rule`. Un-matched transactions receive no `Classification` row (they remain `unknown/needs_review` by default). This happens within the same database session as the commit, before `CommitResult` is returned.

- **AC-7** (A11 — manual override survives reimport): When a transaction already has a `Classification` row with `source=manual`, importing the same CSV again does not add a new classification or change the existing one. Verified by a test: commit CSV → manual_override → reimport same CSV → resolve_active still returns the manual classification.

- **AC-8** (A08 — transfer excluded from spending): A helper function `domain/classification.py::affects_spending(classification: Classification | None) -> bool` returns `True` only when `transaction_type in {"expense", "refund"}`. Transfers, contributions, income, and `unknown` return `False`. Both `expense` and `refund` return `True` so callers can select them together and subtract refunds via signed arithmetic. Verified by a test with a confirmed transfer pair.

- **AC-9** (A10 — ambiguous descriptions stay unknown): A transaction whose `display_text` matches no confirmed rule and has no manual override resolves to `None` from `resolve_active` (equivalent to `unknown/needs_review`). The service must not invent a classification.

- **AC-10** (A12 — refund is not expense): `affects_spending` returns `True` for `refund` (so refunds can be subtracted from gross expense totals), and `False` for any type not in `{"expense", "refund"}`. A test verifies that a -100 expense + +20 refund pair yields gross outflow 100, refund 20, net spending 80 when aggregated by the caller using signed amounts and `affects_spending` as a filter.

## Scope

**In scope:**
- `ClassificationRule` and `TransferLink` ORM models in `storage/models.py`
- Alembic migration adding both tables
- `domain/classification.py` — pure functions: `match_rules`, `resolve_active`, `affects_spending`
- `services/classification_service.py` — `apply_classification`, `manual_override`, `resolve_active_classification`
- `services/transfer_service.py` — `find_transfer_candidates`, `confirm_transfer`, `undo_transfer`
- Post-import rule application in `services/import_service.py`
- Unit tests for all domain functions and service operations (synthetic fixtures, no sample-data)

**Out of scope:**
- LLM-based classification (spec 7)
- Review page UI (spec 06)
- Reporting engine / spending aggregation beyond `is_spending` (spec 07)
- Rule management UI (deferred)
- Background job worker for classification (spec 07)

## Files to Add/Modify

- `storage/models.py` — add `ClassificationRule` and `TransferLink` classes
- `storage/migrations/versions/<rev>_add_classification_rules_transfer_links.py` — new Alembic migration
- `domain/classification.py` — new module: `match_rules`, `resolve_active`, `is_spending`
- `services/classification_service.py` — new module
- `services/transfer_service.py` — new module
- `services/import_service.py` — add post-commit rule application
- `tests/test_classification.py` — new test file
- `tests/test_transfer_service.py` — new test file

## Notes for Implementation

- `TransferLink` uniqueness: add a DB unique constraint on `(transaction_a_id, transaction_b_id)` — order them lexicographically before inserting so `(A, B)` and `(B, A)` collapse to one row.
- `resolve_active` precedence: manual beats rule beats llm beats nothing. Within a source tier, highest `created_at` wins (last write wins within tier). Do not sort by `review_state` — that is a display concern, not a precedence concern.
- `find_transfer_candidates` must not return transactions already linked via a `TransferLink`.
- `undo_transfer` must not fail if the `TransferLink` no longer exists (idempotent).
- The migration must be reversible: downgrade drops `transfer_links` first, then `classification_rules`.
- Post-import rule application must be atomic with the commit transaction: if rule application fails, the import commit rolls back too. To avoid concurrent-import races, rule application reads confirmed rules once per import session and applies them only to the `transaction_id` values inserted by the current batch (not a global re-scan). Concurrent-writer hardening beyond this basic scoping is deferred.
- `create_rule` is an admin/seed function — no access-control or rate-limiting needed in release 1. Mark it as internal in the module docstring.
- All pattern comparisons must normalise whitespace and strip cosmetic suffixes (e.g. `))))`) from `display_text` before matching. The parser already produces `display_text` stripped of cosmetic suffixes; pass it as-is.
