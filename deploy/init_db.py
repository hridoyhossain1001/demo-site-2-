import argparse
import asyncio
import sys
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

# Add parent directory to path so we can import app
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from app.database import engine, Base

# Import all models to ensure they are registered in metadata
import app.models  # noqa: F401


async def existing_tables() -> list[str]:
    async with engine.begin() as conn:
        return await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())


async def create_tables(force_non_empty: bool = False):
    tables = await existing_tables()
    if tables and not force_non_empty:
        table_list = ", ".join(sorted(tables)[:10])
        more = "..." if len(tables) > 10 else ""
        raise SystemExit(
            "Refusing to run create_all + alembic stamp on a non-empty database. "
            f"Existing tables: {table_list}{more}. "
            "Use `alembic upgrade head` for existing databases, or pass "
            "`--force-non-empty` only for a reviewed bootstrap recovery."
        )
    if tables and force_non_empty:
        print("WARNING: forcing create_all + Alembic stamp on a non-empty database.")
    print("Creating all database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created successfully!")


def stamp_schema():
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.stamp(config, "head")
    print("Database schema stamped with the current Alembic head.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap an empty database and stamp Alembic head.")
    parser.add_argument(
        "--force-non-empty",
        action="store_true",
        help="Allow create_all + stamp even when tables already exist. Prefer alembic upgrade head instead.",
    )
    args = parser.parse_args()
    asyncio.run(create_tables(force_non_empty=args.force_non_empty))
    stamp_schema()
