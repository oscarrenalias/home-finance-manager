# Home Finance Analysis

A private, self-hosted web application for understanding household finances from manually imported Finnish bank CSV exports. Stores historical transactions, classifies spending, distinguishes transfers from expenses, compares periods, and supports conversational analysis.

## Quick Start

**Prerequisites:** Docker and Docker Compose installed.

1. Copy the environment file and fill in the required values:

   ```sh
   cp .env.example .env
   # Edit .env — at minimum set OPENAI_API_KEY
   ```

2. Build images and start all services (PostgreSQL, migrations, app, worker, LiteLLM):

   ```sh
   docker-compose up --build -d
   ```

   The `migrate` service runs `alembic upgrade head` automatically before the app starts. You do not need to run migrations manually.

3. Open the app at [http://localhost:3000](http://localhost:3000).

   The first start takes a minute while Reflex compiles the frontend.

To stop:

```sh
docker-compose down
```

Data persists in named Docker volumes (`pg_data`, `import_files`). To reset completely (drops all data):

```sh
docker-compose down -v
```

## Running Migrations

Migrations are managed with Alembic. Scripts live in `src/storage/migrations/versions/`.

In the Docker Compose stack, the `migrate` service runs migrations automatically on every `docker-compose up`. You only need to run Alembic manually for local development or when iterating on schema changes.

Apply all pending migrations:

```sh
uv run alembic upgrade head
```

Roll back one migration:

```sh
uv run alembic downgrade -1
```

Show current revision / history:

```sh
uv run alembic current
uv run alembic history
```

### DATABASE_URL convention

The `DATABASE_URL` environment variable controls which database is targeted:

| Context | Value |
| --- | --- |
| Docker Compose (production) | `postgresql://finances:finances@db:5432/finances` (set in `.env`) |
| Local development (no Docker) | Not set — falls back to `sqlite:///./home_finances.db` in the working directory |
| Automated tests | Not set — tests create and migrate a temporary SQLite file; no Postgres needed |

The fallback default is `sqlite:///./home_finances.db`. `psycopg2-binary` is installed as a runtime dependency so the same image works against PostgreSQL without any additional system packages.

## Running Tests

Install dev dependencies first (only needed once):

```sh
uv sync --dev
```

UI tests use system Google Chrome (no Playwright browser download needed). Ensure **Google Chrome** is installed on your machine before running UI tests.

Run the full test suite:

```sh
uv run pytest
```

Tests use a temporary SQLite database created and migrated automatically by the session-scoped fixture in `tests/conftest.py`. **No running Postgres instance is required** — the test suite always uses SQLite regardless of the `DATABASE_URL` environment variable. Do not point tests at `sample-data/`; use synthetic fixtures only.

## Module Overview

All Python packages live under `src/`. Import paths and tooling configuration (`pyproject.toml`, `alembic.ini`) reflect this layout.

| Module | Purpose |
| --- | --- |
| `src/ui/` | Reflex pages, components, state, and event handlers. Each page under `src/ui/pages/` maps to an app route. The generated React frontend is served on port 3000; the Reflex backend API and WebSocket run on port 8000. |
| `src/domain/` | Pure Python business logic: money value type, transaction semantics, matching rules, classification precedence, reporting calculations, and forecasts. Importable without Reflex. |
| `src/services/` | Application-layer operations that coordinate domain logic with persistence. Keeps UI state and database sessions separate. |
| `src/storage/` | SQLAlchemy ORM models (`src/storage/models.py`), session management and engine factory (`src/storage/database.py`), and Alembic migration environment (`src/storage/migrations/`). |
| `src/llm/` | LLM provider adapter, structured output schemas, prompt templates, and tool dispatch. Talks to the LiteLLM sidecar via an OpenAI-compatible endpoint. |
| `src/jobs/` | Durable background job table, worker process (`src/jobs/worker`), acquisition, retry, and recovery logic. No external queue required. |
| `src/config/` | Category taxonomy loaded from `src/config/categories.yaml` at startup. Category IDs are plain string references in the database; the YAML file is the single source of truth. |
| `tests/` | pytest suite with synthetic fixtures and behavioural tests for financial invariants and import identity. |

### Key files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Defines five services: `db` (PostgreSQL 16), `migrate` (one-shot Alembic runner), `app` (Reflex), `worker` (background jobs), and `litellm` (LLM proxy). |
| `Dockerfile` | Multi-stage image using `python:3.11-slim` and `uv` for dependency installation. |
| `litellm_config.yaml` | LiteLLM model routing — maps logical names (`classifier`, `analyst`) to provider models. No secrets; keys come from environment variables. |
| `alembic.ini` | Alembic configuration pointing at `src/storage/migrations/`. |
| `rxconfig.py` | Reflex app configuration (app name, database URL passthrough). |
| `pyproject.toml` | Project metadata, dependency declarations, and pytest configuration. |

## Environment Variables

See `.env.example` for the full list with descriptions. The minimum required variable for production is `OPENAI_API_KEY`. All other variables have defaults suitable for the Docker Compose setup.
