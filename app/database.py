import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# DATABASE_URL-এ postgres:// দিয়ে শুরু হলে আমরা postgresql+asyncpg:// তে convert করি
# SQLAlchemy async driver-এর জন্য postgresql+asyncpg:// format প্রয়োজন
raw_url = os.getenv("DATABASE_URL", "")
if not raw_url:
    raise RuntimeError("⛔ DATABASE_URL environment variable is required!")

if raw_url.startswith("postgres://"):
    raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif raw_url.startswith("postgresql://"):
    raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif raw_url.startswith("sqlite:///"):
    raw_url = raw_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

DATABASE_URL = raw_url

DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "3"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "2"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "300"))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "10"))

engine_options = {
    "echo": False,
    "pool_pre_ping": True,   # Dead connection auto-detect — avoids "connection reset" errors
}

if DATABASE_URL.startswith("sqlite"):
    engine_options.update(
        connect_args={"check_same_thread": False}
    )
else:
    engine_options.update(
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_recycle=DB_POOL_RECYCLE,
        pool_timeout=DB_POOL_TIMEOUT,
        pool_use_lifo=True,   # concentrate connection reuse on the most active connections
    )

engine = create_async_engine(DATABASE_URL, **engine_options)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            try:
                if session.new or session.dirty or session.deleted:
                    import logging
                    logging.getLogger("app.database").warning(
                        "⚠️ Database session closed with uncommitted changes! "
                        f"New: {len(session.new)}, Dirty: {len(session.dirty)}, Deleted: {len(session.deleted)}"
                    )
            except Exception:
                pass
            await session.close()
