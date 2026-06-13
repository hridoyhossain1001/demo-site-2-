from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import tracker


def _request(body: bytes):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/c",
            "headers": [
                (b"content-type", b"application/json"),
                (
                    b"user-agent",
                    b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
                ),
            ],
            "query_string": b"key=public-key",
        },
        receive,
    )


@pytest.mark.asyncio
async def test_collect_event_rolls_back_redis_dedup_on_http_error(monkeypatch):
    client = SimpleNamespace(id=9, name="Tracker Client", domain=None, api_key="secret")
    rollback = AsyncMock()
    reserve = AsyncMock(return_value={"evt-1"})
    usage_error = HTTPException(status_code=429, detail="quota exceeded")

    async def fake_client_by_key(_key, _db):
        return client

    async def fake_check_and_reserve_usage(*_args, **_kwargs):
        raise usage_error

    monkeypatch.setattr(tracker, "_get_client_by_key", fake_client_by_key)
    monkeypatch.setattr(tracker, "_fast_stream_ingest_enabled", lambda: False)
    monkeypatch.setattr(tracker, "reserve_unique_event_ids", reserve)
    monkeypatch.setattr(tracker, "rollback_redis_dedup", rollback)
    monkeypatch.setattr(tracker, "check_and_reserve_usage", fake_check_and_reserve_usage)

    class Db:
        rolled_back = False

        async def rollback(self):
            self.rolled_back = True

    db = Db()
    request = _request(
        b'{"data":[{"event_name":"PageView","event_time":1710000000,"event_id":"evt-1","user_data":{},"custom_data":{}}]}'
    )

    with pytest.raises(HTTPException) as exc:
        await tracker.collect_event(request, key="public-key", db=db)

    assert exc.value is usage_error
    reserve.assert_awaited_once()
    rollback.assert_awaited_once_with(client.id, {"evt-1"})
    assert db.rolled_back is True
