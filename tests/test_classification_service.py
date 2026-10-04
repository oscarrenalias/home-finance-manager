"""Unit test for A11: Manual classification survives reimport.

AC-6: manual_override() sets source='manual', review_state='accepted'.
A11: Classification row is unchanged after a second import of the same CSV data.
No Reflex imports. No sample-data/ files — synthetic CSV bytes only.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.classification_service as classification_service
from services.import_service import ImportService
from storage.file_store import FileStore
from storage.models import Account, Base, Classification, Transaction

HEADER = '"Date";"Category";"Subcategory";"Text";"Amount";"Balance";"Status";"Reconciled"'


def _row(
    date_str: str = "15.06.2026",
    category: str = "Food",
    subcategory: str = "Groceries",
    text: str = "SUPERMARKET",
    amount: str = "-25,50",
    balance: str = "500,00",
    status: str = "Executed",
    reconciled: str = "No",
) -> str:
    return (
        f'"{date_str}";"{category}";"{subcategory}";"{text}";'
        f'"{amount}";"{balance}";"{status}";"{reconciled}"'
    )


def _csv(*rows: str) -> bytes:
    return ("\n".join([HEADER] + list(rows))).encode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures — function-scoped: fresh in-memory DB per test
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine(tmp_path):
    db_path = tmp_path / "test_cls.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture()
def account(session_factory):
    session = session_factory()
    try:
        acc = Account(id="acct-a11-1", name="A11 Account", role="common", currency="EUR", active=True)
        session.add(acc)
        session.commit()
        return acc
    finally:
        session.close()


@pytest.fixture()
def file_store(tmp_path):
    store_dir = tmp_path / "imports_cls"
    store_dir.mkdir()
    return FileStore(root_dir=str(store_dir))


@pytest.fixture()
def svc(session_factory, file_store):
    return ImportService(session_factory=session_factory, file_store=file_store)


# ---------------------------------------------------------------------------
# A11 — Manual classification survives reimport (AC-6)
# ---------------------------------------------------------------------------


class TestA11ManualClassificationSurvivesReiimport:
    def test_manual_override_preserved_after_reimport(self, svc, session_factory, account):
        """A11: manual_override survives a second commit of the same CSV data.

        1. Import CSV (first commit) → one new Transaction is created.
        2. Call manual_override() on that transaction (source=manual, review_state=accepted).
        3. Re-import the same CSV (new preview → new idempotency_token, second commit).
        4. Assert the Classification row is unchanged: source=manual, review_state=accepted,
           transaction_type=expense. No additional Classification row must exist.
        """
        csv_data = _csv(_row())

        # Step 1: first import — creates one Transaction
        p1 = svc.preview(account.id, "first.csv", csv_data, None, None, False)
        svc.commit(account.id, p1.idempotency_token, p1, None, None, False)

        session = session_factory()
        try:
            txn = session.query(Transaction).filter(Transaction.account_id == account.id).one()
            txn_id = txn.id
        finally:
            session.close()

        # Step 2: manually classify the transaction
        session = session_factory()
        try:
            classification_service.manual_override(
                session=session,
                transaction_id=txn_id,
                transaction_type="expense",
                category_id="groceries",
                merchant="Supermarket",
            )
            session.commit()
            clf_row = (
                session.query(Classification)
                .filter(Classification.transaction_id == txn_id)
                .one()
            )
            original_clf_id = clf_row.id
        finally:
            session.close()

        # Verify the manual classification was written correctly before reimport
        session = session_factory()
        try:
            clf = session.query(Classification).filter(Classification.transaction_id == txn_id).one()
            assert clf.source == "manual"
            assert clf.review_state == "accepted"
            assert clf.transaction_type == "expense"
        finally:
            session.close()

        # Step 3: re-import the same CSV with a fresh idempotency_token
        p2 = svc.preview(account.id, "first.csv", csv_data, None, None, False)
        svc.commit(account.id, p2.idempotency_token, p2, None, None, False)

        # Step 4: assert the Classification row is unchanged after reimport
        session = session_factory()
        try:
            clfs = (
                session.query(Classification)
                .filter(Classification.transaction_id == txn_id)
                .all()
            )
            assert len(clfs) == 1, (
                f"Expected exactly 1 Classification after reimport, found {len(clfs)}"
            )
            clf = clfs[0]
            assert clf.id == original_clf_id, "Classification row identity changed after reimport"
            assert clf.source == "manual", f"Expected source=manual, got {clf.source!r}"
            assert clf.review_state == "accepted", (
                f"Expected review_state=accepted, got {clf.review_state!r}"
            )
            assert clf.transaction_type == "expense", (
                f"Expected transaction_type=expense, got {clf.transaction_type!r}"
            )
        finally:
            session.close()
