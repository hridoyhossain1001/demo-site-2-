"""
Event Deduplication Service — Shared by events.py and tracker.py.

PostgreSQL এ INSERT ... ON CONFLICT DO NOTHING ... RETURNING ব্যবহার করে,
SQLite এ fallback logic ব্যবহার করে।
"""
import logging
import os
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine
from app.models.event_dedup import EventDedup
from app.services.redis_pool import get_redis, record_redis_fallback

logger = logging.getLogger(__name__)
DEDUP_TTL_SECONDS = int(os.getenv("DEDUP_TTL_SECONDS", "172800"))


def _get_redis():
    return get_redis()


async def _reserve_via_redis(client_id: int, candidate_ids: list[str]) -> set[str] | None:
    """Reserve dedup keys in Redis; return None when Redis is unavailable."""
    if not candidate_ids:
        return set()

    r = _get_redis()
    if r is None:
        return None

    try:
        pipe = r.pipeline()
        for event_id in candidate_ids:
            pipe.set(f"dedup:{client_id}:{event_id}", "1", nx=True, ex=DEDUP_TTL_SECONDS)
        results = await pipe.execute()
        return {
            event_id
            for event_id, reserved in zip(candidate_ids, results)
            if reserved
        }
    except Exception as exc:
        logger.warning(f"Redis dedup reserve failed: {exc}")
        record_redis_fallback("dedup_reserve")
        return None


async def rollback_redis_dedup(
    client_id: int,
    candidate_ids: list[str] | set[str],
) -> None:
    """
    Rollback/delete reserved dedup keys from Redis on database write or commit failure.
    """
    if not candidate_ids:
        return

    r = _get_redis()
    if r is None:
        return

    try:
        pipe = r.pipeline()
        for event_id in candidate_ids:
            pipe.delete(f"dedup:{client_id}:{event_id}")
        await pipe.execute()
    except Exception as exc:
        logger.warning(f"Redis dedup rollback failed: {exc}")
        record_redis_fallback("dedup_rollback")



async def reserve_unique_event_ids(
    db: AsyncSession,
    client_id: int,
    candidate_ids: list[str],
) -> set[str]:
    """
    Atomically reserve event IDs for deduplication.
    Returns set of successfully reserved (new) event IDs.

    caller নিজে transaction manage করবে — এখানে commit/rollback হয় না।
    """
    if not candidate_ids:
        return set()

    redis_reserved = await _reserve_via_redis(client_id, candidate_ids)
    target_ids = list(redis_reserved) if redis_reserved is not None else candidate_ids

    if not target_ids:
        return set()

    db_reserved = set()
    if engine.dialect.name == "postgresql":
        rows = [{"client_id": client_id, "event_id": eid} for eid in target_ids]
        stmt = (
            pg_insert(EventDedup)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["client_id", "event_id"])
            .returning(EventDedup.event_id)
        )
        result = await db.execute(stmt)
        db_reserved = set(result.scalars().all())
    else:
        # SQLite fallback
        stmt = select(EventDedup.event_id).where(
            and_(
                EventDedup.client_id == client_id,
                EventDedup.event_id.in_(target_ids),
            )
        )
        res = await db.execute(stmt)
        existing_ids = set(res.scalars().all())
        for event_id in target_ids:
            if event_id not in existing_ids:
                db.add(EventDedup(client_id=client_id, event_id=event_id))
                db_reserved.add(event_id)
        if db_reserved:
            await db.flush()

    if redis_reserved is not None:
        conflicted_ids = redis_reserved - db_reserved
        if conflicted_ids:
            await rollback_redis_dedup(client_id, conflicted_ids)

    return db_reserved
