import asyncio
import argparse
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import update
from app.models.event_outbox import EventOutbox
from app.database import DATABASE_URL

db_url = DATABASE_URL

engine = create_async_engine(db_url)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def reset_stuck(reset_attempts: bool = False):
    async with async_session() as db:
        # Reset any stuck 'processing' rows back to 'queued' and clear locks
        values = {"status": "queued", "locked_at": None, "locked_by": None}
        if reset_attempts:
            values["attempts"] = 0
        stmt = (
            update(EventOutbox)
            .where(EventOutbox.status == 'processing')
            .values(**values)
        )
        result = await db.execute(stmt)
        await db.commit()
        print(f"Successfully reset {result.rowcount} stuck outbox rows.")
        if reset_attempts:
            print("Retry attempts were reset because --reset-attempts was supplied.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Requeue stuck processing outbox rows.")
    parser.add_argument(
        "--reset-attempts",
        action="store_true",
        help="Also reset retry attempt counters to 0. Use only after reviewing poison-message risk.",
    )
    args = parser.parse_args()
    asyncio.run(reset_stuck(reset_attempts=args.reset_attempts))
