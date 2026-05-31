"""add RedX courier credentials

Revision ID: x2y3z4a5b6c7
Revises: w1x2y3z4a5b6
Create Date: 2026-05-31 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "x2y3z4a5b6c7"
down_revision: Union[str, None] = "w1x2y3z4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REDX_COLUMNS = (
    ("redx_access_token", sa.String()),
    ("redx_pickup_store_id", sa.String()),
    ("redx_delivery_area_id", sa.String()),
    ("redx_delivery_area_name", sa.String()),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        existing = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(clients)").fetchall()}
        for name, column_type in REDX_COLUMNS:
            if name not in existing:
                op.add_column("clients", sa.Column(name, column_type, nullable=True))
        return

    for name, _ in REDX_COLUMNS:
        op.execute(f"ALTER TABLE clients ADD COLUMN IF NOT EXISTS {name} VARCHAR")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for name, _ in reversed(REDX_COLUMNS):
        op.execute(f"ALTER TABLE clients DROP COLUMN IF EXISTS {name}")
