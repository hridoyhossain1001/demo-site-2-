"""add pending events velocity ip index

Revision ID: n1o2p3q4r5s6
Revises: m0n1o2p3q4r5
Create Date: 2026-06-10 00:00:00.000000
"""

from alembic import op


revision = "n1o2p3q4r5s6"
down_revision = "m0n1o2p3q4r5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_pending_velocity_ip
            ON pending_events (
                client_id,
                status,
                created_at,
                ((event_data -> 'user_data' ->> 'client_ip_address'))
            )
            """
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_pending_velocity_ip")
