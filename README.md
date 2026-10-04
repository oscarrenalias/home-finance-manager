# Home Finance Analysis

A private, self-hosted web application for understanding household finances from manually imported Finnish bank CSV exports. Stores historical transactions, classifies spending, distinguishes transfers from expenses, compares periods, and supports conversational analysis.

## Quick Start

**Prerequisites:** Docker and Docker Compose installed.

1. Copy the environment file and add your OpenAI API key:

   ```sh
   cp .env.example .env
   # Edit .env and set OPENAI_API_KEY
   ```

2. Start all services:

   ```sh
   docker-compose up -d
   ```

3. Run database migrations inside the running app container:

   ```sh
   docker-compose exec app uv run alembic upgrade head
   ```

4. Open the app at [http://localhost:3000](http://localhost:3000).

To stop:

```sh
docker-compose down
```

Data persists in named Docker volumes (`db_data`, `import_files`). To reset completely:

```sh
docker-compose down -v
```

## Running Migrations

Migrations are managed with Alembic. The migration scripts live in `storage/migrations/versions/`.

Apply all pending migrations:

```sh
uv run alembic upgrade head
```

Roll back one migration:

```sh
uv run alembic downgrade -1
```

Show current revision:

```sh
uv run alembic current
```

Show migration history:

```sh
uv run alembic history
```

The `DATABASE_URL` environment variable controls which database is targeted. If unset, defaults to `sqlite:///./home_finances.db` in the working directory.

## Running Tests

Install dev dependencies first (only needed once):

```sh
uv sync --dev
```

Install the Playwright browser for UI tests (one-time, after `uv sync --dev`):

```sh
uv run playwright install chromium
```

Run the full test suite:

```sh
uv run pytest
```

Tests use a temporary SQLite database created and migrated automatically by the session-scoped fixture in `tests/conftest.py`. No running services are required. Do not point tests at `sample-data/`; use synthetic fixtures only.

## Module Overview

| Module | Purpose |
| --- | --- |
| `ui/` | Reflex pages, components, state, and event handlers. Each page under `ui/pages/` maps to an app route. The generated React frontend is served on port 3000; the Reflex backend API and WebSocket run on port 8000. |
| `domain/` | Pure Python business logic: money value type, transaction semantics, matching rules, classification precedence, reporting calculations, and forecasts. Importable without Reflex. |
| `services/` | Application-layer operations that coordinate domain logic with persistence. Keeps UI state and database sessions separate. |
| `storage/` | SQLAlchemy ORM models (`storage/models.py`), session management and engine factory (`storage/database.py`), and Alembic migration environment (`storage/migrations/`). |
| `llm/` | LLM provider adapter, structured output schemas, prompt templates, and tool dispatch. Talks to the LiteLLM sidecar via an OpenAI-compatible endpoint. |
| `jobs/` | Durable background job table, worker process (`jobs/worker`), acquisition, retry, and recovery logic. No external queue required. |
| `config/` | Category taxonomy loaded from `config/categories.yaml` at startup. Category IDs are plain string references in the database; the YAML file is the single source of truth. |
| `tests/` | pytest suite with synthetic fixtures and behavioural tests for financial invariants and import identity. |

### Key files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Defines three services: `app` (Reflex), `worker` (background jobs), and `litellm` (LLM proxy). |
| `Dockerfile` | Multi-stage image using `python:3.11-slim` and `uv` for dependency installation. |
| `litellm_config.yaml` | LiteLLM model routing — maps logical names (`classifier`, `analyst`) to provider models. No secrets; keys come from environment variables. |
| `alembic.ini` | Alembic configuration pointing at `storage/migrations/`. |
| `rxconfig.py` | Reflex app configuration (app name, database URL passthrough). |
| `pyproject.toml` | Project metadata, dependency declarations, and pytest configuration. |

## Environment Variables

See `.env.example` for the full list with descriptions. The minimum required variable for production is `OPENAI_API_KEY`. All other variables have defaults suitable for the Docker Compose setup.
