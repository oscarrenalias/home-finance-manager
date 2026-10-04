"""Smoke tests: storage models importable without Reflex, all tables present, categories load."""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect


EXPECTED_TABLES = frozenset({
    "accounts",
    "import_batches",
    "source_observations",
    "transactions",
    "classifications",
    "audit_events",
    "jobs",
})

EXPECTED_CATEGORY_COUNT = 16


def test_storage_models_importable_without_reflex():
    from storage import models

    assert hasattr(models, "Account")
    assert hasattr(models, "ImportBatch")
    assert hasattr(models, "SourceObservation")
    assert hasattr(models, "Transaction")
    assert hasattr(models, "Classification")
    assert hasattr(models, "AuditEvent")
    assert hasattr(models, "Job")
    assert "reflex" not in sys.modules, "storage.models must not import Reflex"


def test_all_tables_present_after_migration(temp_db: str):
    engine = create_engine(temp_db, connect_args={"check_same_thread": False})
    try:
        actual = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    missing = EXPECTED_TABLES - actual
    assert not missing, f"Tables missing after migration: {missing}"


def test_categories_load_from_yaml():
    from config.categories import load_categories

    categories = load_categories()
    assert len(categories) == EXPECTED_CATEGORY_COUNT, (
        f"Expected {EXPECTED_CATEGORY_COUNT} categories, got {len(categories)}"
    )
