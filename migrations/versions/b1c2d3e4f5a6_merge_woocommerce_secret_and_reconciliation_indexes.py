"""merge woocommerce secret and reconciliation index heads

Revision ID: b1c2d3e4f5a6
Revises: aa1b2c3d4e5f, f5g6h7i8j9k0
Create Date: 2026-06-13 00:00:00.000000
"""

from typing import Sequence, Union


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, tuple[str, str], None] = (
    "aa1b2c3d4e5f",
    "f5g6h7i8j9k0",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
