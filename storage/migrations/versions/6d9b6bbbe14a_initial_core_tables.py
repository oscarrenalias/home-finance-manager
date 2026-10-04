"""Initial core tables: accounts, import_batches, transactions, source_observations.

Revision ID: 6d9b6bbbe14a
Revises:
Create Date: 2026-10-04

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "6d9b6bbbe14a"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=True),
        sa.Column("coverage_end", sa.Date(), nullable=True),
        sa.Column("completeness_declared", sa.Boolean(), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("executed_count", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_token", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_token"),
    )
    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.create_index("ix_import_batches_account_hash", ["account_id", "file_hash"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("transaction_type", sa.String(length=50), nullable=True),
        sa.Column("category_id", sa.String(length=100), nullable=True),
        sa.Column("merchant", sa.String(length=255), nullable=True),
        sa.Column("classification_source", sa.String(length=50), nullable=True),
        sa.Column("classification_version", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.create_index("ix_transactions_account_date", ["account_id", "date"])
        batch_op.create_index("ix_transactions_type_date", ["transaction_type", "date"])
        batch_op.create_index("ix_transactions_category_date", ["category_id", "date"])
        batch_op.create_index("ix_transactions_merchant_date", ["merchant", "date"])

    op.create_table(
        "source_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("transaction_id", sa.String(length=36), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_date", sa.String(length=20), nullable=False),
        sa.Column("raw_category", sa.String(length=255), nullable=False),
        sa.Column("raw_subcategory", sa.String(length=255), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("raw_amount", sa.String(length=30), nullable=False),
        sa.Column("raw_balance", sa.String(length=30), nullable=True),
        sa.Column("raw_status", sa.String(length=20), nullable=False),
        sa.Column("raw_reconciled", sa.String(length=10), nullable=False),
        sa.Column("parsed_date", sa.Date(), nullable=True),
        sa.Column("parsed_amount_cents", sa.Integer(), nullable=True),
        sa.Column("parsed_balance_cents", sa.Integer(), nullable=True),
        sa.Column("parsed_status", sa.String(length=20), nullable=True),
        sa.Column("is_pending", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("source_observations") as batch_op:
        batch_op.create_index("ix_source_observations_batch", ["batch_id"])
        batch_op.create_index("ix_source_observations_transaction", ["transaction_id"])


def downgrade() -> None:
    with op.batch_alter_table("source_observations") as batch_op:
        batch_op.drop_index("ix_source_observations_transaction")
        batch_op.drop_index("ix_source_observations_batch")
    op.drop_table("source_observations")

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_index("ix_transactions_merchant_date")
        batch_op.drop_index("ix_transactions_category_date")
        batch_op.drop_index("ix_transactions_type_date")
        batch_op.drop_index("ix_transactions_account_date")
    op.drop_table("transactions")

    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.drop_index("ix_import_batches_account_hash")
    op.drop_table("import_batches")

    op.drop_table("accounts")
