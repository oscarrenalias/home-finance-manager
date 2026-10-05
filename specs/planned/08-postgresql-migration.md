---
id: spec-08-postgresql-migration
status: planned
---

# PostgreSQL Migration

## Objective

Replace SQLite with PostgreSQL as the production database, updating docker-compose, connection config, and Alembic migrations, while keeping unit tests fast by retaining SQLite for the non-UI test suite.

## Background

The app currently defaults to `sqlite:///./home_finances.db`. SQLite works for a single user but has concurrency limits and lacks features needed for production reliability. PostgreSQL is the target for release 1. This spec depends on spec-07 (src layout) being merged first.

## Acceptance Criteria

- **AC-1** (docker-compose): A `db` service using `postgres:16-alpine` is added to `docker-compose.yml`. The `app`, `worker`, and `migrate` services declare a `depends_on` the `db` service. The `db` service uses a named volume `pg_data` for persistence.

- **AC-2** (DATABASE_URL): `.env.example` shows `DATABASE_URL=postgresql://finances:finances@db:5432/finances`. The `db` service environment sets `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` matching that URL. `.env` is updated to use the same URL for Docker use; `storage/database.py` retains SQLite as the fallback default only for local `uv run` without Docker (so tests still work without Postgres).

- **AC-3** (connection code): `storage/database.py` removes the `check_same_thread` SQLite-only connect arg — it must not be passed to PostgreSQL engines. Use `connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}` (already present; verify it is retained correctly).

- **AC-4** (alembic): `alembic/env.py` removes `render_as_batch=True` from the online migration context — this is a SQLite-only workaround not needed (and potentially harmful) on PostgreSQL. A new initial migration is generated (`uv run alembic revision --autogenerate -m "initial postgres schema"`) that creates all tables from scratch for a clean Postgres deploy. Existing SQLite migrations remain in the history for reference but the new migration is the recommended starting point for fresh installs.

- **AC-5** (healthcheck): The `db` service has a `pg_isready` healthcheck. The `migrate` service `depends_on` the `db` service with `condition: service_healthy` so migrations never run before Postgres accepts connections.

- **AC-6** (unit tests unchanged): `uv run pytest tests/ --ignore=tests/ui` passes without a running Postgres instance. Unit tests continue to use the SQLite fixture in `tests/conftest.py`. The `DATABASE_URL` env var is not set during unit tests, so the fallback SQLite path is exercised.

- **AC-7** (docker-compose up works): `docker-compose up --build` starts cleanly: `db` becomes healthy, `migrate` runs all migrations successfully, `app` starts and serves `/` without the `no such table` error seen previously.

## Scope

**In scope:**
- `docker-compose.yml` — add `db` service, update `depends_on`, add `pg_data` volume
- `.env` and `.env.example` — Postgres `DATABASE_URL`
- `storage/database.py` — verify SQLite guard on `check_same_thread`
- `storage/migrations/env.py` — remove `render_as_batch=True`
- New Alembic migration for clean Postgres schema
- `Dockerfile` — add `libpq-dev` if needed for `psycopg2` (or use `psycopg2-binary`)
- `pyproject.toml` — add `psycopg2-binary` dependency

**Out of scope:**
- Changing the unit test fixture to use Postgres
- Data migration from an existing SQLite database
- Connection pooling configuration (use SQLAlchemy defaults)

## Files to Add/Modify

- `docker-compose.yml`
- `.env`, `.env.example`
- `src/storage/database.py`
- `src/storage/migrations/env.py`
- `src/storage/migrations/versions/` — new migration file
- `pyproject.toml`
- `Dockerfile`
