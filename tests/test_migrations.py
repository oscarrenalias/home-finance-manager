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
    # Use the explicit SQLite-chain head; "head" is ambiguous now that the
    # postgres branch adds a second root revision.
    command.upgrade(_make_alembic_cfg(fresh_db), "e4f5a6b7c8d9")
    assert ALL_TABLES == _table_names(fresh_db)


def test_downgrade_removes_new_tables(fresh_db):
    # Downgrade past classification_rules/transfer_links (-1), the seed data (-2),
    # and classifications/audit_events/jobs (-3) to reach the initial core-tables
    # revision (6d9b6bbbe14a), leaving only core tables.
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "e4f5a6b7c8d9")
    command.downgrade(cfg, "-3")
    remaining = _table_names(fresh_db)
    assert NEW_TABLES.isdisjoint(remaining), f"New tables still present: {NEW_TABLES & remaining}"
    assert CORE_TABLES.issubset(remaining), f"Core tables missing: {CORE_TABLES - remaining}"


def test_downgrade_base_removes_all_tables(fresh_db):
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "e4f5a6b7c8d9")
    command.downgrade(cfg, "base")
    assert _table_names(fresh_db) == set()


def test_reimport_upgrade_downgrade_upgrade(fresh_db):
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "e4f5a6b7c8d9")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "e4f5a6b7c8d9")
    assert ALL_TABLES == _table_names(fresh_db)


def _column_names(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# AC-15 — note columns migration (f1a2b3c4d5e6)
# ---------------------------------------------------------------------------


def test_upgrade_to_head_adds_note_columns(fresh_db):
    """AC-15: upgrading to head (f1a2b3c4d5e6) adds note + note_updated_at."""
    command.upgrade(_make_alembic_cfg(fresh_db), "f1a2b3c4d5e6")
    cols = _column_names(fresh_db, "transactions")
    assert "note" in cols
    assert "note_updated_at" in cols


def test_downgrade_minus_one_removes_note_columns(fresh_db):
    """AC-15: downgrade -1 from f1a2b3c4d5e6 removes both note columns."""
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "f1a2b3c4d5e6")
    command.downgrade(cfg, "-1")
    cols = _column_names(fresh_db, "transactions")
    assert "note" not in cols
    assert "note_updated_at" not in cols


def test_single_alembic_head(fresh_db):
    """AC-15: alembic history has exactly one head (f1a2b3c4d5e6)."""
    from alembic.script import ScriptDirectory

    cfg = _make_alembic_cfg(fresh_db)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == ["f1a2b3c4d5e6"]


def test_existing_rows_survive_upgrade_with_null_notes(fresh_db):
    """AC-15: rows inserted before upgrade have note=NULL and note_updated_at=NULL after upgrade."""
    import uuid as _uuid

    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "e4f5a6b7c8d9")

    engine = create_engine(fresh_db, connect_args={"check_same_thread": False})
    acct_id = str(_uuid.uuid4())
    tx_id = str(_uuid.uuid4())
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO accounts (id, name, role, currency, active, created_at, updated_at) "
                "VALUES (:id, 'Test', 'common', 'EUR', 1, datetime('now'), datetime('now'))"
            ),
            {"id": acct_id},
        )
        conn.execute(
            text(
                "INSERT INTO transactions (id, account_id, date, amount_cents, currency, "
                "original_text, display_text, status, created_at, updated_at) "
                "VALUES (:id, :acct, '2026-01-01', -100, 'EUR', 'X', 'X', 'Executed', "
                "datetime('now'), datetime('now'))"
            ),
            {"id": tx_id, "acct": acct_id},
        )
        conn.commit()
    engine.dispose()

    command.upgrade(cfg, "f1a2b3c4d5e6")

    engine2 = create_engine(fresh_db, connect_args={"check_same_thread": False})
    try:
        with engine2.connect() as conn:
            row = conn.execute(
                text("SELECT note, note_updated_at FROM transactions WHERE id = :id"),
                {"id": tx_id},
            ).fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is None
    finally:
        engine2.dispose()


def test_fk_classifications_transaction_id_enforced(fresh_db):
    cfg = _make_alembic_cfg(fresh_db)
    command.upgrade(cfg, "e4f5a6b7c8d9")

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
