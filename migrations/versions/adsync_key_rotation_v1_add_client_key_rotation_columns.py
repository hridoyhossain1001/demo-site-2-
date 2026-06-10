"""add client api key rotation columns

Revision ID: adsync_key_rotation_v1
Revises: adsync_spend_v1
Create Date: 2026-06-10 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "adsync_key_rotation_v1"
down_revision: Union[str, None] = "adsync_spend_v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("old_api_key", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("api_key_rotated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_clients_old_api_key", "clients", ["old_api_key"])


def downgrade() -> None:
    op.drop_constraint("uq_clients_old_api_key", "clients", type_="unique")
    op.drop_column("clients", "api_key_rotated_at")
    op.drop_column("clients", "old_api_key")
