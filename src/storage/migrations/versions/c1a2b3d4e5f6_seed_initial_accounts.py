"""Seed the two initial household accounts.

Revision ID: c1a2b3d4e5f6
Revises: 3a8f91c0
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa
import uuid
from datetime import datetime

revision = "c1a2b3d4e5f6"
down_revision = "3a8f91c0"
branch_labels = None
depends_on = None

_NOW = datetime(2026, 10, 4, 0, 0, 0)


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO accounts (id, name, role, currency, active, created_at, updated_at) "
            "VALUES (:id, :name, :role, :currency, :active, :created_at, :updated_at) "
            "ON CONFLICT (id) DO NOTHING"
        ).bindparams(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.common")),
            name="Common account",
            role="common",
            currency="EUR",
            active=True,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO accounts (id, name, role, currency, active, created_at, updated_at) "
            "VALUES (:id, :name, :role, :currency, :active, :created_at, :updated_at) "
            "ON CONFLICT (id) DO NOTHING"
        ).bindparams(
            id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.accrual")),
            name="Accrual account",
            role="accrual",
            currency="EUR",
            active=True,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM accounts WHERE id IN (:common, :accrual)").bindparams(
            common=str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.common")),
            accrual=str(uuid.uuid5(uuid.NAMESPACE_DNS, "home-finances.accrual")),
        )
    )
