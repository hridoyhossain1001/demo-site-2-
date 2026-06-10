"""Shared async Redis client helpers."""
import logging
import os

logger = logging.getLogger(__name__)

_redis_client = None
_redis_fallback_counts: dict[str, int] = {}


def record_redis_fallback(name: str) -> None:
    key = str(name or "unknown")
    _redis_fallback_counts[key] = _redis_fallback_counts.get(key, 0) + 1


def redis_fallback_counts() -> dict[str, int]:
    return dict(_redis_fallback_counts)


def get_redis():
    """Return a process-local Redis client, or None when Redis is not configured."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None

    try:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "150")),
        )
        return _redis_client
    except Exception as exc:
        import re
        safe_msg = re.sub(r":[^/@]+@", ":***@", str(exc))
        logger.warning(f"Redis client init failed: {safe_msg}")
        record_redis_fallback("init")
        return None


async def close_redis() -> None:
    """Close the process-local Redis client during graceful shutdown."""
    global _redis_client
    if _redis_client is None:
        return
    client = _redis_client
    _redis_client = None
    try:
        await client.aclose()
    except Exception as exc:
        import re
        safe_msg = re.sub(r":[^/@]+@", ":***@", str(exc))
        logger.warning(f"Error during Redis close: {safe_msg}")
