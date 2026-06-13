"""add incomplete reconciliation indexes

Revision ID: f5g6h7i8j9k0
Revises: e4f5g6h7i8j9
Create Date: 2026-06-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f5g6h7i8j9k0"
down_revision: Union[str, None] = "e4f5g6h7i8j9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_incomplete_status_activity",
        "incomplete_checkouts",
        ["status", "last_activity_at"],
        unique=False,
    )
    op.create_index(
        "ix_pending_client_created",
        "pending_events",
        ["client_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pending_client_created", table_name="pending_events")
    op.drop_index("ix_incomplete_status_activity", table_name="incomplete_checkouts")
