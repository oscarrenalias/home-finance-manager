"""Add classifications, audit_events, and jobs tables.

Revision ID: 3a8f91c0
Revises: 6d9b6bbbe14a
Create Date: 2026-10-04

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "3a8f91c0"
down_revision = "6d9b6bbbe14a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("transaction_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("transaction_type", sa.String(length=30), nullable=False),
        sa.Column("category_id", sa.String(length=100), nullable=True),
        sa.Column("merchant", sa.String(length=255), nullable=True),
        sa.Column("rule_version", sa.String(length=50), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("review_state", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("classifications") as batch_op:
        batch_op.create_index("ix_classifications_transaction", ["transaction_id"])
        batch_op.create_index("ix_classifications_review_state", ["review_state"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("actor", sa.String(length=50), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.create_index("ix_audit_events_entity", ["entity_type", "entity_id"])
        batch_op.create_index("ix_audit_events_created_at", ["created_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.create_index("ix_jobs_state_kind", ["state", "kind"])
        batch_op.create_index("ix_jobs_lease_expires_at", ["lease_expires_at"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_lease_expires_at")
        batch_op.drop_index("ix_jobs_state_kind")
    op.drop_table("jobs")

    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_index("ix_audit_events_created_at")
        batch_op.drop_index("ix_audit_events_entity")
    op.drop_table("audit_events")

    with op.batch_alter_table("classifications") as batch_op:
        batch_op.drop_index("ix_classifications_review_state")
        batch_op.drop_index("ix_classifications_transaction")
    op.drop_table("classifications")
