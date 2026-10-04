---
name: "Project Scaffold & Infrastructure"
id: spec-22c4c181
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
# Project Scaffold & Infrastructure

## Objective

Set up the project skeleton — module layout, database models, migrations, docker-compose, and category configuration — so that subsequent specs can build features on a stable foundation without revisiting infrastructure concerns.

## Acceptance Criteria

- `docker-compose up` starts all three services (app, worker, LiteLLM sidecar) without error
- Alembic migrations run cleanly against a fresh SQLite database (`alembic upgrade head` exits 0)
- All SQLAlchemy models can be imported from the `storage/` module without importing Reflex
- Domain modules (`domain/`, `services/`) can be imported and tested without importing Reflex
- `config/categories.yaml` loads and validates against a Pydantic schema; all 15 initial categories parse correctly
- `pytest` discovers and passes a smoke test confirming the DB schema matches the models
- The Reflex app starts and serves an empty shell with navigation (Overview, Transactions, Import, Review, Budgets, Ask, Settings); all pages render without error, content can be placeholder
- `docker-compose` mounts a named persistent volume for the SQLite database and raw import files

## Scope

**In scope:**
- `pyproject.toml` with pinned dependencies: Reflex, SQLAlchemy, Alembic, Pydantic, LangChain, LiteLLM, pytest
- Python module structure: `ui/`, `domain/`, `services/`, `storage/`, `llm/`, `jobs/`, `tests/`
- SQLAlchemy models: Account, ImportBatch, SourceObservation, Transaction, Classification, Job, AuditEvent
- Alembic setup with an initial migration covering all models above
- `docker-compose.yml`: Reflex app container, background worker container, LiteLLM sidecar
- `Dockerfile` for the app (used by both app and worker containers)
- `config/categories.yaml`: all 15 initial categories with `id`, `name`, `parent` (optional), `guidance`, and `examples`
- Pydantic schema for loading and validating `categories.yaml`
- `.env.example` documenting required environment variables (LiteLLM endpoint, model names, DB path, import storage path) — no secrets
- `tests/test_smoke.py`: imports all models, runs `alembic upgrade head` on a temp DB, asserts all tables exist
- Reflex app shell: navigation sidebar, empty page stubs for all 7 pages

**Out of scope:**
- CSV parsing and import logic (next spec)
- Any page content beyond navigation and placeholder headings
- Authentication
- Transfer link model (added in iteration 3)
- Reserve and forecast models (release 2)
- LiteLLM routing rules (configured when classification is built)

## Initial Category Taxonomy

The following must appear in `config/categories.yaml`:

| ID | Name | Parent |
|---|---|---|
| `groceries` | Groceries | — |
| `restaurants_cafes` | Restaurants & cafes | — |
| `children_activities` | Children / Activities | — |
| `children_clothing` | Children / Clothing | — |
| `clothing` | Clothing | — |
| `health_wellness` | Health & wellness | — |
| `housing_maintenance` | Housing / Maintenance | — |
| `electricity_energy` | Electricity / Energy | `utilities` |
| `electricity_network` | Electricity / Network | `utilities` |
| `insurance` | Insurance | — |
| `transport` | Transport | — |
| `leisure_entertainment` | Leisure & entertainment | — |
| `household_purchases` | Household purchases | — |
| `subscriptions` | Subscriptions | — |
| `fees_charges` | Fees & charges | — |
| `other_review` | Other / Review | — |

Each entry must include a `guidance` string (one sentence describing what belongs here) and an `examples` list (2–4 representative Finnish merchant names or description fragments).

## Data Model Notes

Money amounts are stored as signed integer cents. Dates are stored as calendar dates (no time component). Every table has `created_at` and `updated_at` timestamps. Use generated stable UUIDs for primary keys.

The `Classification` model must record: `source` (one of: `manual`, `rule`, `llm`), `category_id` (string — references the YAML taxonomy, not a FK), `transaction_type` (one of: `expense`, `refund`, `internal_transfer`, `contribution`, `income`, `external_transfer`, `unknown`), `rule_version`, `model_version`, `rationale`, `review_state` (one of: `accepted`, `needs_review`, `rejected`).

The `Job` model must record: `kind`, `state` (one of: `pending`, `in_progress`, `done`, `failed`), `retry_count`, `inputs` (JSON), `error_summary`, `lease_expires_at`.

## Files to Add/Modify

- `pyproject.toml`
- `Dockerfile`
- `docker-compose.yml`
- `.env.example`
- `config/categories.yaml`
- `config/categories.py` (Pydantic loader)
- `storage/__init__.py`, `storage/models.py`, `storage/database.py`
- `storage/migrations/` (Alembic env + initial migration)
- `domain/__init__.py`, `domain/money.py` (integer-cent Money type and formatting helpers)
- `services/__init__.py`
- `llm/__init__.py`
- `jobs/__init__.py`
- `ui/__init__.py`, `ui/app.py` (Reflex app entry point)
- `ui/pages/overview.py`, `transactions.py`, `import_.py`, `review.py`, `budgets.py`, `ask.py`, `settings.py`
- `tests/__init__.py`, `tests/test_smoke.py`
- `tests/conftest.py` (temp DB fixture)
