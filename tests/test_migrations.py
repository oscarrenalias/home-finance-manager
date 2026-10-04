"""Tests for Alembic migration correctness: downgrade paths, reimport, FK integrity."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text

_PROJECT_ROOT = Path(__file__).parent.parent

CORE_TABLES = frozenset({"accounts", "import_batches", "source_observations", "transactions"})
NEW_TABLES = frozenset({"classifications", "audit_events", "jobs", "classification_rules", "transfer_links"})
ALL_TABLES = CORE_TABLES | NEW_TABLES


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(_PROJECT_ROOT / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    # Prevent env.py from calling logging.config.fileConfig(alembic.ini), which
    # runs with disable_existing_loggers=True and silences all non-alembic loggers
    # (including domain.*) for the remainder of the pytest session.
    cfg.config_file_name = None
    return cfg


def _table_names(db_url: str) -> set[str]:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    try:
        # alembic_version is always present after any migration run — exclude it
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


@pytest.fixture()
def fresh_db():
    """Yield a sqlite:// URL for an empty, unmigrated DB; clean up after test."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{db_path}"
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    yield db_url
    if original_url is not None:
        os.environ["DATABASE_URL"] = original_url
    else:
        os.environ.pop("DATABASE_URL", None)
    Path(db_path).unlink(missing_ok=True)


def test_upgrade_head_creates_all_tables(fresh_db):
    command.upgrade(_make_alembic_cfg(fresh_db), "head")
    assert ALL_TABLES == _table_names(fresh_db)


def test_downgrade_removes_new_tables(fresh_db):
    # Downgrade past classification_rules/transfer_links (-1), the seed data (-2),
    # and classifications/audit_events/jobs (-3) to reach the initial core-tables
    # revision (6d9b6bbbe14a), leaving only core tables.
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-3")
    remaining = _table_names(fresh_db)
    assert NEW_TABLES.isdisjoint(remaining), f"New tables still present: {NEW_TABLES & remaining}"
    assert CORE_TABLES.issubset(remaining), f"Core tables missing: {CORE_TABLES - remaining}"


def test_downgrade_base_removes_all_tables(fresh_db):
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert _table_names(fresh_db) == set()


def test_reimport_upgrade_downgrade_upgrade(fresh_db):
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    assert ALL_TABLES == _table_names(fresh_db)


def test_fk_classifications_transaction_id_enforced(fresh_db):
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")

    engine = create_engine(fresh_db, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(conn, *_):
        conn.execute("PRAGMA foreign_keys=ON")

    try:
        with engine.connect() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "INSERT INTO classifications "
                        "(id, transaction_id, source, transaction_type, review_state, created_at, updated_at) "
                        "VALUES ('abc', 'nonexistent-txn-id', 'rule', 'expense', 'pending', "
                        "datetime('now'), datetime('now'))"
                    )
                )
                conn.commit()
    finally:
        engine.dispose()
