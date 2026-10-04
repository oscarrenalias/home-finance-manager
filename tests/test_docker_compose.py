"""Regression test: docker-compose.yml must define a migrate service that runs
before app and worker start, so Alembic migrations are applied automatically at boot."""

import re
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).parent.parent / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose_text():
    return COMPOSE_FILE.read_text()


def _service_block(compose_text: str, service: str) -> str:
    """Extract the YAML block for a top-level service (stops at the next top-level key)."""
    pattern = rf"(?m)^  {re.escape(service)}:\n((?:    .*\n?)*)"
    m = re.search(pattern, compose_text)
    assert m, f"Service '{service}' not found in docker-compose.yml"
    return m.group(0)


def test_migrate_service_exists(compose_text):
    assert "  migrate:" in compose_text, "migrate service missing from docker-compose.yml"


def test_migrate_runs_alembic_upgrade_head(compose_text):
    block = _service_block(compose_text, "migrate")
    assert "alembic upgrade head" in block, (
        "migrate service command must include 'alembic upgrade head'"
    )


def test_migrate_restart_is_no(compose_text):
    block = _service_block(compose_text, "migrate")
    assert 'restart: "no"' in block, "migrate service must have restart: \"no\""


def test_app_depends_on_migrate_completed_successfully(compose_text):
    block = _service_block(compose_text, "app")
    assert "service_completed_successfully" in block, (
        "app service must declare depends_on: migrate: condition: service_completed_successfully"
    )
    assert "migrate:" in block, "app depends_on block must reference migrate"


def test_worker_depends_on_migrate_completed_successfully(compose_text):
    block = _service_block(compose_text, "worker")
    assert "service_completed_successfully" in block, (
        "worker service must declare depends_on: migrate: condition: service_completed_successfully"
    )
    assert "migrate:" in block, "worker depends_on block must reference migrate"
