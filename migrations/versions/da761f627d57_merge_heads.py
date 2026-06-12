"""merge heads

Revision ID: da761f627d57
Revises: a9b8c7d6e5f4, add_whatsapp_notifications_v1
Create Date: 2026-06-11 09:53:30.058131
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da761f627d57'
down_revision: Union[str, None] = ('a9b8c7d6e5f4', 'add_whatsapp_notifications_v1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
