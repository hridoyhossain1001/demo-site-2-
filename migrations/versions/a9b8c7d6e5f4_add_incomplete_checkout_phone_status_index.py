"""add incomplete checkout phone status index

Revision ID: a9b8c7d6e5f4
Revises: z9a8b7c6d5e4
Create Date: 2026-06-11 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "z9a8b7c6d5e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_incomplete_client_phone_status",
        "incomplete_checkouts",
        ["client_id", "phone_hash", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_incomplete_client_phone_status", table_name="incomplete_checkouts")
