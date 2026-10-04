"""Session-scoped Playwright app server fixture for UI tests."""
from __future__ import annotations

import os
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


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    # Use system Google Chrome instead of the Playwright-managed Chromium download.
    # Chrome is already installed; the Playwright CDN download often fails in restricted networks.
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

    alembic_cfg = Config(_PROJECT_ROOT / "alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.config_file_name = None  # prevent fileConfig from disabling non-alembic loggers
    command.upgrade(alembic_cfg, "head")

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
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.fail(f"Reflex did not become ready on port {frontend_port} within 90s")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
