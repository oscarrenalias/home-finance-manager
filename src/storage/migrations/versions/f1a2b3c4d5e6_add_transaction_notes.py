"""Add note columns to transactions table.

Revision ID: f1a2b3c4d5e6
Revises: e4f5a6b7c8d9
Create Date: 2026-10-05

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("transactions", sa.Column("note_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "note_updated_at")
    op.drop_column("transactions", "note")
