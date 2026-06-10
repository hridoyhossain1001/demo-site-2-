import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
from app.services import event_worker
from app.models.event_outbox import EventOutbox


class MockNestedSession:
    def __init__(self):
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rolled_back = True
        return False


class MockDb:
    def __init__(self):
        self.added = []
        self.nested_sessions = []

    def begin_nested(self):
        nested = MockNestedSession()
        self.nested_sessions.append(nested)
        return nested

    def add(self, obj):
        self.added.append(obj)


@pytest.mark.anyio
async def test_mark_dead_triggers_webhook(monkeypatch):
    db = MockDb()
    row = EventOutbox(
        id=42,
        client_id=10,
        event_payload=[{"event_name": "Purchase", "event_id": "shopify_123"}],
        usage_reserved={"reserved": True}
    )
    client = SimpleNamespace(
        id=10,
        name="Test Client",
        webhook_url="https://api.testclient.com/dlq-alerts"
    )

    # Mock usage rollback
    async def mock_rollback(*args):
        pass
    monkeypatch.setattr(event_worker, "rollback_usage_reservation", mock_rollback)

    # Mock send_webhook
    mock_send = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.webhook_service.send_webhook", mock_send)

    # Mock asyncio.create_task to capture the coroutine
    tasks = []
    def mock_create_task(coro):
        tasks.append(coro)
        return MagicMock()
    monkeypatch.setattr("asyncio.create_task", mock_create_task)

    await event_worker._mark_dead(db, row, client, "API error code 500")

    # Assert row properties
    assert row.status == "dead"
    assert row.last_error == "API error code 500"

    # Assert DB began a nested transaction for rollback
    assert len(db.nested_sessions) == 1

    # Assert webhook send was triggered
    assert len(tasks) == 1
    # Execute coroutine to assert call values
    await tasks[0]
    mock_send.assert_called_once_with(
        "https://api.testclient.com/dlq-alerts",
        "dlq_alert",
        {
            "client_id": 10,
            "client_name": "Test Client",
            "event_ids": "shopify_123",
            "event_names": "Purchase",
            "error_message": "API error code 500",
            "status": "dead",
        }
    )
