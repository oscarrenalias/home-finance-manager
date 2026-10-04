"""Add classification_rules and transfer_links tables.

Revision ID: e4f5a6b7c8d9
Revises: c1a2b3d4e5f6
Create Date: 2026-10-04

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "c1a2b3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classification_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("pattern_type", sa.String(length=30), nullable=False),
        sa.Column("pattern_value", sa.Text(), nullable=False),
        sa.Column("transaction_type", sa.String(length=50), nullable=False),
        sa.Column("category_id", sa.String(length=100), nullable=True),
        sa.Column("merchant", sa.String(length=255), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("classification_rules") as batch_op:
        batch_op.create_index("ix_classification_rules_priority", ["priority"])
        batch_op.create_index("ix_classification_rules_pattern_type", ["pattern_type"])

    op.create_table(
        "transfer_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("transaction_a_id", sa.String(length=36), nullable=False),
        sa.Column("transaction_b_id", sa.String(length=36), nullable=False),
        sa.Column("confirmed_by", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "transaction_a_id < transaction_b_id", name="ck_transfer_links_order"
        ),
        sa.ForeignKeyConstraint(["transaction_a_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["transaction_b_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_a_id", "transaction_b_id", name="uq_transfer_links_pair"
        ),
    )
    with op.batch_alter_table("transfer_links") as batch_op:
        batch_op.create_index("ix_transfer_links_transaction_a", ["transaction_a_id"])
        batch_op.create_index("ix_transfer_links_transaction_b", ["transaction_b_id"])


def downgrade() -> None:
    with op.batch_alter_table("transfer_links") as batch_op:
        batch_op.drop_index("ix_transfer_links_transaction_b")
        batch_op.drop_index("ix_transfer_links_transaction_a")
    op.drop_table("transfer_links")

    with op.batch_alter_table("classification_rules") as batch_op:
        batch_op.drop_index("ix_classification_rules_pattern_type")
        batch_op.drop_index("ix_classification_rules_priority")
    op.drop_table("classification_rules")
