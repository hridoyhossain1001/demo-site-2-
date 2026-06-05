"""merge site binding and visitor key heads

Revision ID: m0n1o2p3q4r5
Revises: k0l1m2n3o4p5, l0m1n2o3p4q5
Create Date: 2026-06-05 00:00:00.000000
"""

from typing import Sequence, Union


revision: str = "m0n1o2p3q4r5"
down_revision: Union[str, tuple[str, str], None] = (
    "k0l1m2n3o4p5",
    "l0m1n2o3p4q5",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
