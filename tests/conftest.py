"""Session-scoped fixture: create a temp SQLite DB, run Alembic migrations, tear down."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from storage.database import reset_engine

_PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def temp_db():
    """Yield a sqlite:// URL pointing at a fully-migrated temp DB; clean up after session."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{db_path}"

    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url

    alembic_cfg = Config(_PROJECT_ROOT / "alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, "head")

    yield db_url

    reset_engine()
    if original_url is not None:
        os.environ["DATABASE_URL"] = original_url
    else:
        os.environ.pop("DATABASE_URL", None)
    Path(db_path).unlink(missing_ok=True)
