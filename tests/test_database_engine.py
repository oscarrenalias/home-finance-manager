"""Tests for _get_engine() connect_args guard and Alembic migration correctness."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from storage.database import _get_engine, reset_engine

_PROJECT_ROOT = Path(__file__).parent.parent

POSTGRES_TABLES = frozenset({
    "accounts",
    "audit_events",
    "classification_rules",
    "classifications",
    "import_batches",
    "jobs",
    "source_observations",
    "transactions",
    "transfer_links",
})


@pytest.fixture(autouse=True)
def isolated_engine():
    """Reset module-level engine state around each test."""
    saved = os.environ.get("DATABASE_URL")
    os.environ.pop("DATABASE_URL", None)
    reset_engine()
    yield
    reset_engine()
    if saved is not None:
        os.environ["DATABASE_URL"] = saved
    else:
        os.environ.pop("DATABASE_URL", None)


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(_PROJECT_ROOT / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    # Suppress logging.config.fileConfig so it doesn't silence non-alembic loggers
    cfg.config_file_name = None
    return cfg


def _table_names(db_url: str) -> set[str]:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


# --- connect_args tests ---


def test_sqlite_url_produces_check_same_thread():
    """SQLite URLs must include check_same_thread=False in connect_args."""
    os.environ["DATABASE_URL"] = "sqlite:///./test.db"
    with patch("storage.database.create_engine", return_value=MagicMock()) as mock_create:
        _get_engine()
    _, kwargs = mock_create.call_args
    assert kwargs.get("connect_args", {}).get("check_same_thread") is False


def test_postgresql_url_produces_empty_connect_args():
    """PostgreSQL URLs must not carry check_same_thread in connect_args."""
    os.environ["DATABASE_URL"] = "postgresql+psycopg2://user:pass@localhost/db"
    with patch("storage.database.create_engine", return_value=MagicMock()) as mock_create:
        _get_engine()
    _, kwargs = mock_create.call_args
    assert kwargs.get("connect_args", {}) == {}


def test_engine_is_cached_across_calls():
    """_get_engine() returns the same Engine object on repeated calls."""
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    first = _get_engine()
    second = _get_engine()
    assert first is second


# --- migration correctness tests ---


@pytest.fixture()
def fresh_sqlite_db():
    """Yield a sqlite:// URL for a blank, unmigrated DB; clean up after test."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = db_url
    yield db_url
    Path(db_path).unlink(missing_ok=True)


def test_upgrade_head_creates_all_tables(fresh_sqlite_db):
    """Applying upgrade head to a blank DB creates all 9 expected tables."""
    command.upgrade(_make_alembic_cfg(fresh_sqlite_db), "head")
    assert POSTGRES_TABLES == _table_names(fresh_sqlite_db)


def test_downgrade_base_removes_all_tables(fresh_sqlite_db):
    """Downgrading to base after a full upgrade removes all tables."""
    cfg = _make_alembic_cfg(fresh_sqlite_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert _table_names(fresh_sqlite_db) == set()
