"""add whatsapp notifications

Revision ID: add_whatsapp_notifications_v1
Revises: adsync_key_rotation_v1
Create Date: 2026-06-10 12:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "add_whatsapp_notifications_v1"
down_revision: Union[str, None] = "adsync_key_rotation_v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create whatsapp_instances table
    op.create_table(
        "whatsapp_instances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_name", sa.String(length=255), nullable=False),
        sa.Column("phone_number", sa.String(length=50), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="evolution"),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("client_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_name")
    )

    # Create notification_jobs table
    op.create_table(
        "notification_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("whatsapp_instance_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False, server_default="whatsapp"),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="evolution"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=255), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["whatsapp_instance_id"], ["whatsapp_instances.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key")
    )

    op.create_index("ix_notification_jobs_claim", "notification_jobs", ["status", "attempt_count", "next_attempt_at", "locked_until"])
    op.create_index("ix_notification_jobs_client_id", "notification_jobs", ["client_id"])
    op.create_index("ix_notification_jobs_whatsapp_instance_id", "notification_jobs", ["whatsapp_instance_id"])

    # Modify clients table to add notification fields
    op.add_column("clients", sa.Column("owner_notify_whatsapp", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("clients", sa.Column("owner_whatsapp_number", sa.String(length=50), nullable=True))
    op.add_column("clients", sa.Column("whatsapp_instance_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_clients_whatsapp_instance", "clients", "whatsapp_instances", ["whatsapp_instance_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_clients_whatsapp_instance", "clients", type_="foreignkey")
    op.drop_column("clients", "whatsapp_instance_id")
    op.drop_column("clients", "owner_whatsapp_number")
    op.drop_column("clients", "owner_notify_whatsapp")

    op.drop_index("ix_notification_jobs_whatsapp_instance_id", table_name="notification_jobs")
    op.drop_index("ix_notification_jobs_client_id", table_name="notification_jobs")
    op.drop_index("ix_notification_jobs_claim", table_name="notification_jobs")
    op.drop_table("notification_jobs")

    op.drop_table("whatsapp_instances")
