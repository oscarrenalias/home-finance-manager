---
id: spec-07-src-layout-and-docs-restructure
status: done
---

# Source Layout and Docs Restructure

## Objective

Move all Python source packages into a `src/` directory and rename `design/` to `docs/` to separate code from reference material, without changing any import statements.

## Background

Currently seven Python packages (`ui`, `domain`, `services`, `storage`, `llm`, `jobs`, `config`) sit at the project root alongside documentation folders (`design/`), takt specs, and config files. Moving source packages to `src/` is standard Python project layout and separates importable code from project scaffolding. Python's `src/` layout does not change package names — `from domain.classification import ...` continues to work unchanged.

## Acceptance Criteria

- **AC-1** (src layout): All seven Python packages (`ui`, `domain`, `services`, `storage`, `llm`, `jobs`, `config`) are located under `src/`. No package remains at the project root.

- **AC-2** (imports unchanged): No Python import statement in the codebase changes. `from domain.x import y`, `from storage.models import Base`, etc. all continue to resolve correctly.

- **AC-3** (pyproject.toml): `[tool.hatch.build.targets.wheel]` is updated to `packages = [{include = "X", from = "src"} ...]` for all seven packages. `[tool.pytest.ini_options]` gains `pythonpath = ["src"]` so pytest resolves imports without installation.

- **AC-4** (alembic): `alembic.ini` `prepend_sys_path` is updated to `. src` so Alembic can import `storage.models` from the new location. `uv run alembic current` runs without error.

- **AC-5** (rxconfig): `rxconfig.py` `app_module_import = "ui.app"` continues to work — Reflex resolves this via the Python path, not a filesystem path. Verify by importing the app module: `uv run python -c "import ui.app"`.

- **AC-6** (docs folder): `design/` is renamed to `docs/`. `CLAUDE.md` design reference paths are updated to point to `docs/`.

- **AC-7** (tests pass): `uv run pytest` passes with 0 failures after the restructure. This is the primary correctness gate.

## Scope

**In scope:**
- `git mv` of the seven packages into `src/`
- `git mv design/ docs/`
- Updates to `pyproject.toml`, `alembic.ini`, `CLAUDE.md`
- No changes to any `import` statement in any Python file

**Out of scope:**
- PostgreSQL migration (separate spec)
- Moving `specs/`, `tests/`, config files at root, or hidden directories
- Any functional changes to the application

## Files to Add/Modify

- `src/` — new directory containing the seven relocated packages
- `docs/` — renamed from `design/`
- `pyproject.toml` — package paths and pytest pythonpath
- `alembic.ini` — prepend_sys_path
- `CLAUDE.md` — update design reference paths
