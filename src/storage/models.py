"""SQLAlchemy ORM models for Account, ImportBatch, SourceObservation, Transaction, Classification, AuditEvent, and Job."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
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
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    account: Mapped[Account] = relationship("Account", back_populates="transactions")
    source_observations: Mapped[list[SourceObservation]] = relationship(
        "SourceObservation", back_populates="transaction"
    )
    classifications: Mapped[list[Classification]] = relationship(
        "Classification", back_populates="transaction", order_by="Classification.created_at"
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


class Classification(Base):
    """Stores one classification decision for a transaction.

    Multiple records per transaction are allowed; the active one is determined by
    Classification precedence rules (manual > rule > llm) and created_at ordering.
    """

    __tablename__ = "classifications"
    __table_args__ = (
        Index("ix_classifications_transaction", "transaction_id"),
        Index("ix_classifications_review_state", "review_state"),
    )

    # Valid values for constrained string fields — enforced in application code, not DB CHECK
    SOURCES = {"manual", "rule", "llm"}
    TRANSACTION_TYPES = {
        "expense", "refund", "internal_transfer", "contribution",
        "income", "external_transfer", "unknown",
    }
    REVIEW_STATES = {"accepted", "needs_review", "rejected"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id"), nullable=False
    )
    # Source of the classification decision: manual override, rule engine, or LLM
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    # String reference to YAML taxonomy; not a DB FK — taxonomy lives in config/categories.yaml
    category_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    merchant: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rule_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    review_state: Mapped[str] = mapped_column(String(20), nullable=False, default="needs_review")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    transaction: Mapped[Transaction] = relationship("Transaction", back_populates="classifications")


class AuditEvent(Base):
    """Immutable record of a state change to a ledger entity.

    Captures before/after JSON snapshots so classification history can be replayed.
    updated_at mirrors created_at; audit rows are never modified after insert.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    # Actor that caused the change: manual/rule/llm/system
    actor: Mapped[str] = mapped_column(String(50), nullable=False)
    before_state: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    after_state: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )


class Job(Base):
    """Durable background job record for classification and other async work.

    The worker claims jobs transactionally via lease_expires_at to survive restarts.
    inputs stores arbitrary JSON so the worker can reconstruct execution context.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_state_kind", "state", "kind"),
        Index("ix_jobs_lease_expires_at", "lease_expires_at"),
    )

    STATES = {"pending", "in_progress", "done", "failed"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inputs: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )


class ClassificationRule(Base):
    """Ordered rule applied during automatic transaction classification.

    Higher priority wins when multiple rules match. pattern_type drives how
    pattern_value is matched against the transaction text or merchant field.
    is_confirmed marks rules that have been validated by a human review.
    """

    __tablename__ = "classification_rules"
    __table_args__ = (
        Index("ix_classification_rules_priority", "priority"),
        Index("ix_classification_rules_pattern_type", "pattern_type"),
    )

    PATTERN_TYPES = {"text_contains", "merchant_exact", "merchant_contains"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pattern_type: Mapped[str] = mapped_column(String(30), nullable=False)
    pattern_value: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    category_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    merchant: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )


class TransferLink(Base):
    """Links two transactions that represent the two sides of an internal transfer.

    The pair is stored in lexicographic order (transaction_a_id < transaction_b_id)
    so the unique constraint covers both orderings without duplication.
    confirmed_by records whether the link was created by a matching rule or a human.
    """

    __tablename__ = "transfer_links"
    __table_args__ = (
        UniqueConstraint("transaction_a_id", "transaction_b_id", name="uq_transfer_links_pair"),
        CheckConstraint("transaction_a_id < transaction_b_id", name="ck_transfer_links_order"),
        Index("ix_transfer_links_transaction_a", "transaction_a_id"),
        Index("ix_transfer_links_transaction_b", "transaction_b_id"),
    )

    CONFIRMED_BY_VALUES = {"manual", "rule"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    transaction_a_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id"), nullable=False
    )
    transaction_b_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transactions.id"), nullable=False
    )
    confirmed_by: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())

    transaction_a: Mapped[Transaction] = relationship(
        "Transaction", foreign_keys=[transaction_a_id]
    )
    transaction_b: Mapped[Transaction] = relationship(
        "Transaction", foreign_keys=[transaction_b_id]
    )
