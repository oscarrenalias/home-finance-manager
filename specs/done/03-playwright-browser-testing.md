---
name: Playwright Browser Testing Infrastructure
id: spec-649a67c4
description: null
dependencies: null
priority: null
complexity: null
status: done
tags: []
scope:
  in: null
  out: null
feature_root_id: null
---
# Playwright Browser Testing Infrastructure

## Objective

Add Playwright-based browser tests that run as part of the `takt merge` test gate, establish the test harness so all future specs can include UI tests, and update agent guidance so tester beads write Playwright tests whenever a developer bead touches `ui/`.

## Background

The project currently has 156 unit/integration tests (`tests/`) that run in ~1s. There is no browser-level test coverage. The import page exists and works but is untested at the browser layer. We need:

1. **Infrastructure**: `pytest-playwright`, a session-scoped fixture that starts Reflex on a free port with a temporary SQLite database and waits for the app to be ready.
2. **Integration**: `tests/ui/` directory inside the existing `testpaths = ["tests"]`, so `uv run pytest` picks up both unit and browser tests automatically.
3. **First tests**: Smoke tests for the Import page that exercise the golden path end-to-end in a browser.
4. **Agent guidance**: CLAUDE.md and HOWTO.md updated so tester beads know when and how to write UI tests in future specs.

The takt `test_command` (`uv run pytest`) already covers `tests/` via `pyproject.toml`, so no takt config changes are needed — just add `tests/ui/`.

## Acceptance Criteria

- `uv run pytest tests/ui/` passes with a running Chromium browser (headless)
- `uv run pytest` (full gate, including `tests/ui/`) passes with 0 failures
- The `app_server` fixture starts Reflex on two dynamically chosen free ports (frontend and backend allocated independently); port collisions across parallel worktrees are unlikely in practice but not formally prevented
- The fixture creates a temporary SQLite database, runs `alembic upgrade head`, seeds the two household accounts, then tears down the DB and process when the session ends
- If Reflex fails to start within 90 seconds, the fixture raises `pytest.fail(...)` with a clear message — tests do not silently skip
- At least one browser test covers the Import page golden path: navigate → select account → upload a valid CSV → preview rows appear → commit → success banner visible
- At least one test covers the error case: upload a file with a bad header → error message appears
- CLAUDE.md has a "UI Testing" section that tells tester agents: when a bead's `expected_files` includes any path under `ui/`, write at least one Playwright test in `tests/ui/test_<page>.py` covering the golden path and one error case
- HOWTO.md gains a "UI Test Deliverables" subsection showing the fixture import and a minimal Playwright test stub that tester agents can follow

## Scope

**In scope:**
- `pytest-playwright` added to `[project.optional-dependencies] dev` in `pyproject.toml`
- `playwright install chromium` documented in `README.md` (or `CLAUDE.md` dev setup section) and run as a one-time step
- `tests/ui/__init__.py` (empty)
- `tests/ui/conftest.py` — session-scoped `app_server` fixture:
  - Picks two independent free TCP ports: `frontend_port = _free_port()`, `backend_port = _free_port()` (each via a separate bind-to-0 call; this is best-effort OS assignment — port collisions are unlikely but not impossible under heavy parallel load)
  - Uses `tmp_path_factory.mktemp("db")` for the SQLite DB directory and `tmp_path_factory.mktemp("imports")` for `IMPORT_FILES_DIR` (session-scoped; `tmp_path` cannot be used from a session-scoped fixture)
  - Runs `alembic upgrade head` against the temp DB
  - Seeds both accounts using the same deterministic UUIDs as the migration (`uuid5(NAMESPACE_DNS, "home-finances.common")` and `"home-finances.accrual"`)
  - Launches `uv run reflex run --env dev --backend-port <backend_port> --frontend-port <frontend_port>` via `subprocess.Popen` with the test env vars
  - Polls `http://localhost:<frontend_port>` every 2s until HTTP 200 or 90s timeout
  - Yields `http://localhost:<frontend_port>` to tests
  - On teardown: `proc.terminate()`, `proc.wait(timeout=10)`, then `proc.kill()` if still running
- `tests/ui/test_import_page.py` — Playwright tests for the Import page
- `CLAUDE.md` — new "UI Testing" section
- `specs/HOWTO.md` — "UI Test Deliverables" subsection

**Out of scope:**
- Firefox or WebKit browsers (Chromium only)
- Visual snapshot / screenshot regression testing
- Testing any page other than Import in this spec (later specs add their own UI tests)
- Docker/CI Playwright install (this spec targets local dev only; container CI is a later concern)
- Any change to `.takt/config.yaml`

## Files to Add/Modify

- `pyproject.toml` — add `pytest-playwright` to dev extras
- `tests/ui/__init__.py` — new (empty)
- `tests/ui/conftest.py` — new, session-scoped `app_server` fixture
- `tests/ui/test_import_page.py` — new, Playwright tests for the import page
- `CLAUDE.md` — add "UI Testing" section (agent guidance)
- `specs/HOWTO.md` — add "UI Test Deliverables" subsection with fixture import and stub (this file exists at `specs/HOWTO.md`; add a new subsection at the end)

## Implementation Notes

### Free-port selection (best-effort; TOCTOU aware)
```python
import socket

def _free_port() -> int:
    # bind-to-0 asks the OS for a free port; releasing and reusing it is a
    # best-effort approach — another process can claim it before Reflex binds.
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]

# Call separately for frontend and backend — never derive one from the other.
frontend_port = _free_port()
backend_port = _free_port()
```

### Fixture signature (use `tmp_path_factory`, not `tmp_path`)
```python
import pytest

@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("db")
    imports_dir = tmp_path_factory.mktemp("imports")
    db_url = f"sqlite:///{db_dir}/test.db"
    # ... alembic upgrade, seed, start Reflex ...
```

### Reflex startup wait
```python
import urllib.request, time, pytest

deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        urllib.request.urlopen(f"http://localhost:{frontend_port}", timeout=2)
        break
    except Exception:
        time.sleep(2)
else:
    proc.terminate()
    pytest.fail(f"Reflex did not become ready on port {frontend_port} within 90s")
```

### Import page test structure and locators
Inspect the running app and the mockup at `design/home-finance-mockup.html` to identify correct selectors. As part of the developer bead's work, add `data-testid` attributes to these elements in `ui/pages/import_.py`:
- `data-testid="account-select"` on the account dropdown
- `data-testid="csv-upload"` on the upload zone
- `data-testid="preview-table"` on the preview table
- `data-testid="commit-btn"` on the commit button
- `data-testid="success-banner"` on the success message
- `data-testid="error-banner"` on the error message

Then the test can locate them unambiguously:
```python
def test_import_golden_path(page: Page, app_server: str):
    page.goto(f"{app_server}/import")
    page.get_by_test_id("account-select").select_option(label="Common account")
    # upload via file chooser, assert preview-table visible, click commit-btn, ...
```

Use a small synthetic CSV (5 rows) built inline — do not use files from `sample-data/`.

### tester agent guidance (to add to CLAUDE.md)
When a bead's `expected_files` includes any file under `ui/pages/` or `ui/components/`, the tester bead **must** also deliver `tests/ui/test_<page>.py` with:
- A test using the `page` and `app_server` fixtures that exercises the golden path
- A test that exercises at least one error/edge case visible in the browser
- Both tests must be independent and idempotent (no shared mutable state between tests beyond the session-scoped `app_server`)
- Use `data-testid` attributes for all locators — never CSS class names or element positions
