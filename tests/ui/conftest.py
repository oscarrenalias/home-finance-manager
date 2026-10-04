"""Session-scoped Playwright app server fixture for UI tests."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Stores the test-database URL after app_server sets it up; consumed by app_db_url.
_stored_db_url: list[str] = []


def _kill_group(proc: subprocess.Popen) -> None:
    """Send SIGTERM to the process group, wait, then SIGKILL if needed."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    # Use system Google Chrome (already installed) instead of the Playwright-managed
    # Chromium binary, which requires a CDN download that fails in restricted networks.
    return {**browser_type_launch_args, "channel": "chrome"}


def _free_port() -> int:
    """Ask the OS for a free port via bind-to-0 (best-effort; TOCTOU window before Reflex binds)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    """Launch Reflex against a temp SQLite DB; yield the frontend base URL; shut down after session."""
    frontend_port = _free_port()
    backend_port = _free_port()

    db_dir = tmp_path_factory.mktemp("db")
    imports_dir = tmp_path_factory.mktemp("imports")
    db_url = f"sqlite:///{db_dir / 'test.db'}"
    _stored_db_url.clear()
    _stored_db_url.append(db_url)

    # Set DATABASE_URL in the test process so alembic env.py (which reads os.environ) targets
    # the temp test database rather than the default production database.
    original_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url

    alembic_cfg = Config(_PROJECT_ROOT / "alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.config_file_name = None  # prevent fileConfig from disabling non-alembic loggers
    command.upgrade(alembic_cfg, "head")

    if original_db_url is not None:
        os.environ["DATABASE_URL"] = original_db_url
    else:
        os.environ.pop("DATABASE_URL", None)

    env = {**os.environ, "DATABASE_URL": db_url, "IMPORT_FILES_DIR": str(imports_dir)}

    proc = subprocess.Popen(
        [
            "uv", "run", "reflex", "run",
            "--env", "dev",
            "--frontend-port", str(frontend_port),
            "--backend-port", str(backend_port),
        ],
        cwd=_PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # isolate into its own process group for clean teardown
    )

    base_url = f"http://localhost:{frontend_port}"
    deadline = time.monotonic() + 90
    ready = False
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(base_url, timeout=2)
            ready = True
            break
        except urllib.error.HTTPError:
            # Any HTTP response (including 4xx) means the server is accepting connections.
            ready = True
            break
        except Exception:
            time.sleep(2)

    if not ready:
        _kill_group(proc)
        pytest.fail(f"Reflex did not become ready on port {frontend_port} within 90s")

    yield base_url

    _kill_group(proc)


@pytest.fixture(scope="session")
def app_db_url(app_server: str) -> str:  # noqa: ARG001
    """Return the SQLite DATABASE_URL for the shared test database.

    Depends on app_server to guarantee that _stored_db_url is populated before
    this fixture is resolved. The app_server parameter is used for fixture
    ordering only — its value is not needed here.
    """
    return _stored_db_url[0]
