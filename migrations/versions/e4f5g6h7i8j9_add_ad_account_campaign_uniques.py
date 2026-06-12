"""add ad account and campaign uniqueness constraints

Revision ID: e4f5g6h7i8j9
Revises: da761f627d57
Create Date: 2026-06-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5g6h7i8j9"
down_revision: Union[str, None] = "da761f627d57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _raise_if_duplicates_exist(table_name: str, columns: tuple[str, ...], constraint_name: str) -> None:
    bind = op.get_bind()
    column_expr = ", ".join(columns)
    duplicate_rows = bind.execute(
        sa.text(
            f"""
            SELECT {column_expr}, COUNT(*) AS duplicate_count
            FROM {table_name}
            GROUP BY {column_expr}
            HAVING COUNT(*) > 1
            LIMIT 5
            """
        )
    ).mappings().all()
    if duplicate_rows:
        examples = [
            ", ".join(f"{column}={row[column]!r}" for column in columns)
            + f" count={row['duplicate_count']}"
            for row in duplicate_rows
        ]
        raise RuntimeError(
            f"Cannot create {constraint_name}; duplicate {table_name} rows exist: "
            + "; ".join(examples)
        )


def upgrade() -> None:
    _raise_if_duplicates_exist(
        "ad_accounts",
        ("client_id", "platform", "external_account_id"),
        "uq_ad_account_client_platform_external",
    )
    _raise_if_duplicates_exist(
        "ad_campaigns",
        ("ad_account_id", "external_campaign_id"),
        "uq_ad_campaign_account_external",
    )
    op.create_unique_constraint(
        "uq_ad_account_client_platform_external",
        "ad_accounts",
        ["client_id", "platform", "external_account_id"],
    )
    op.create_unique_constraint(
        "uq_ad_campaign_account_external",
        "ad_campaigns",
        ["ad_account_id", "external_campaign_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ad_campaign_account_external", "ad_campaigns", type_="unique")
    op.drop_constraint("uq_ad_account_client_platform_external", "ad_accounts", type_="unique")
