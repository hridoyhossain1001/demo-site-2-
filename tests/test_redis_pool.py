import pytest
import logging
from unittest.mock import AsyncMock
import app.services.redis_pool as redis_pool


@pytest.mark.anyio
async def test_redis_pool_fallback_counts():
    redis_pool._redis_fallback_counts.clear()
    redis_pool.record_redis_fallback("test_write")
    redis_pool.record_redis_fallback("test_write")
    counts = redis_pool.redis_fallback_counts()
    assert counts == {"test_write": 2}


@pytest.mark.anyio
async def test_close_redis_error_masking(monkeypatch, caplog):
    mock_client = AsyncMock()
    mock_client.aclose.side_effect = Exception("Failed to close redis://user:supersecretpassword@localhost:6379/0 connection")

    monkeypatch.setattr(redis_pool, "_redis_client", mock_client)

    with caplog.at_level(logging.WARNING):
        await redis_pool.close_redis()

    assert redis_pool._redis_client is None
    warnings = [rec.message for rec in caplog.records if "Error during Redis close" in rec.message]
    assert len(warnings) == 1
    assert "supersecretpassword" not in warnings[0]
    assert "redis://user:***@localhost:6379/0" in warnings[0]
