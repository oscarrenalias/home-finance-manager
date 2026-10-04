"""SQLAlchemy ORM models for Account, ImportBatch, SourceObservation, and Transaction."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    import_batches: Mapped[list[ImportBatch]] = relationship("ImportBatch", back_populates="account")
    transactions: Mapped[list[Transaction]] = relationship("Transaction", back_populates="account")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        Index("ix_import_batches_account_hash", "account_id", "file_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    coverage_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    coverage_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    completeness_declared: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    executed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_token: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    account: Mapped[Account] = relationship("Account", back_populates="import_batches")
    source_observations: Mapped[list[SourceObservation]] = relationship(
        "SourceObservation", back_populates="batch"
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_account_date", "account_id", "date"),
        Index("ix_transactions_type_date", "transaction_type", "date"),
        Index("ix_transactions_category_date", "category_id", "date"),
        Index("ix_transactions_merchant_date", "merchant", "date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable for pending rows (no confirmed bank balance yet)
    balance_after: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    category_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    merchant: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    classification_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    classification_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    account: Mapped[Account] = relationship("Account", back_populates="transactions")
    source_observations: Mapped[list[SourceObservation]] = relationship(
        "SourceObservation", back_populates="transaction"
    )


class SourceObservation(Base):
    __tablename__ = "source_observations"
    __table_args__ = (
        Index("ix_source_observations_batch", "batch_id"),
        Index("ix_source_observations_transaction", "transaction_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("import_batches.id"), nullable=False)
    # Null until observation is committed as an executed transaction
    transaction_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("transactions.id"), nullable=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_date: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_category: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_subcategory: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[Text] = mapped_column(Text, nullable=False)
    raw_amount: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_balance: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    raw_status: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_reconciled: Mapped[str] = mapped_column(String(10), nullable=False)
    parsed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    parsed_amount_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parsed_balance_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parsed_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    batch: Mapped[ImportBatch] = relationship("ImportBatch", back_populates="source_observations")
    transaction: Mapped[Optional[Transaction]] = relationship(
        "Transaction", back_populates="source_observations"
    )
