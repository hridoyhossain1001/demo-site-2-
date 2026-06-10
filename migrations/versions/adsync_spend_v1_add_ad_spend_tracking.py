"""add ad spend tracking tables and columns

Revision ID: adsync_spend_v1
Revises: n1o2p3q4r5s6
Create Date: 2026-06-10 15:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'adsync_spend_v1'
down_revision: Union[str, None] = 'n1o2p3q4r5s6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create ad_accounts table
    op.create_table(
        "ad_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("external_account_id", sa.String(length=100), nullable=False),
        sa.Column("account_name", sa.String(length=200), nullable=True),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("account_currency", sa.String(length=10), nullable=False, server_default="USD"),
        sa.Column("account_timezone", sa.String(length=100), nullable=False, server_default="UTC"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_ad_accounts_client_id"), "ad_accounts", ["client_id"], unique=False)

    # 2. Create ad_campaigns table
    op.create_table(
        "ad_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_account_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("external_campaign_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("objective", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["ad_account_id"], ["ad_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_ad_campaigns_ad_account_id"), "ad_campaigns", ["ad_account_id"], unique=False)
    op.create_index(op.f("ix_ad_campaigns_external_campaign_id"), "ad_campaigns", ["external_campaign_id"], unique=False)

    # 3. Create ad_insights_daily table
    op.create_table(
        "ad_insights_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("external_campaign_id", sa.String(length=100), nullable=False),
        sa.Column("insight_date", sa.Date(), nullable=False),
        sa.Column("spend", sa.Numeric(precision=12, scale=4), nullable=False, server_default="0.0"),
        sa.Column("impressions", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("platform_purchases", sa.Integer(), nullable=True),
        sa.Column("platform_revenue", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="USD"),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index(op.f("ix_ad_insights_daily_client_id"), "ad_insights_daily", ["client_id"], unique=False)
    op.create_index(op.f("ix_ad_insights_daily_external_campaign_id"), "ad_insights_daily", ["external_campaign_id"], unique=False)
    op.create_index(op.f("ix_ad_insights_daily_insight_date"), "ad_insights_daily", ["insight_date"], unique=False)
    op.create_index("ix_ad_insights_lookup", "ad_insights_daily", ["client_id", "platform", "external_campaign_id", "insight_date"], unique=True)

    # 4. Add columns to event_logs table
    op.add_column("event_logs", sa.Column("ad_platform", sa.String(length=30), nullable=True))
    op.add_column("event_logs", sa.Column("ad_campaign_id", sa.String(length=100), nullable=True))
    op.add_column("event_logs", sa.Column("ad_adset_id", sa.String(length=100), nullable=True))
    op.add_column("event_logs", sa.Column("ad_id", sa.String(length=100), nullable=True))

    # 5. Create index on event_logs for ad campaign
    op.create_index("ix_event_logs_ad_campaign", "event_logs", ["client_id", "ad_campaign_id", "created_at"], unique=False)


def downgrade() -> None:
    # 1. Drop index on event_logs
    op.drop_index("ix_event_logs_ad_campaign", table_name="event_logs")

    # 2. Drop columns from event_logs
    op.drop_column("event_logs", "ad_id")
    op.drop_column("event_logs", "ad_adset_id")
    op.drop_column("event_logs", "ad_campaign_id")
    op.drop_column("event_logs", "ad_platform")

    # 3. Drop ad_insights_daily
    op.drop_index("ix_ad_insights_lookup", table_name="ad_insights_daily")
    op.drop_index(op.f("ix_ad_insights_daily_insight_date"), table_name="ad_insights_daily")
    op.drop_index(op.f("ix_ad_insights_daily_external_campaign_id"), table_name="ad_insights_daily")
    op.drop_index(op.f("ix_ad_insights_daily_client_id"), table_name="ad_insights_daily")
    op.drop_table("ad_insights_daily")

    # 4. Drop ad_campaigns
    op.drop_index(op.f("ix_ad_campaigns_external_campaign_id"), table_name="ad_campaigns")
    op.drop_index(op.f("ix_ad_campaigns_ad_account_id"), table_name="ad_campaigns")
    op.drop_table("ad_campaigns")

    # 5. Drop ad_accounts
    op.drop_index(op.f("ix_ad_accounts_client_id"), table_name="ad_accounts")
    op.drop_table("ad_accounts")
