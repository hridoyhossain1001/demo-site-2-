"""add woocommerce webhook secret

Revision ID: aa1b2c3d4e5f
Revises: z9a8b7c6d5e4
Create Date: 2026-06-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "aa1b2c3d4e5f"
down_revision: Union[str, None] = "z9a8b7c6d5e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.execute(sa.text(f"PRAGMA table_info({table_name})")).fetchall()
        return any(row[1] == column_name for row in rows)
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _column_exists("clients", "woocommerce_webhook_secret"):
        op.add_column("clients", sa.Column("woocommerce_webhook_secret", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    if _column_exists("clients", "woocommerce_webhook_secret"):
        op.drop_column("clients", "woocommerce_webhook_secret")
