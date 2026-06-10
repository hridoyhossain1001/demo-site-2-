from types import SimpleNamespace

import pytest
from sqlalchemy import inspect
from starlette.requests import Request

from app.models.courier_order import CourierOrder
from app.routers import courier_webhook


class _NoQueryDb:
    def __init__(self):
        self.added = []

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("DB query should not run for ignored status updates")

    def add(self, row):
        self.added.append(row)


class _EmptyResult:
    def scalar_one_or_none(self):
        return None


class _EmptyQueryDb(_NoQueryDb):
    async def execute(self, *_args, **_kwargs):
        return _EmptyResult()


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ResultsDb:
    def __init__(self, *values):
        self.values = iter(values)
        self.committed = False
        self.added = []
        self.statements = []

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        return _Result(next(self.values))

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True


def _order(**overrides):
    values = {
        "courier_provider": "redx",
        "courier_status": "in_transit",
        "order_id": "1001",
        "status_history": [],
        "client_id": 1,
        "pending_event_id": None,
        "purchase_event_sent": False,
        "refund_event_sent": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(
    body: bytes,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/webhook/pathao",
            "headers": headers or [],
            "query_string": query_string,
        },
        receive,
    )


@pytest.mark.asyncio
async def test_pathao_integration_handshake_echoes_secret_without_internal_guard(monkeypatch):
    monkeypatch.setattr(courier_webhook, "PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET", "integration-secret")
    request = _request(
        b'{"event":"webhook_integration"}',
        headers=[(b"x-pathao-signature", b"integration-secret")],
    )

    response = await courier_webhook.pathao_webhook(request, db=None)

    assert response.status_code == 202
    assert response.headers["X-Pathao-Merchant-Webhook-Integration-Secret"] == "integration-secret"


@pytest.mark.asyncio
async def test_steadfast_documented_bearer_callback_uses_consignment_id(monkeypatch):
    order = _order(
        courier_provider="steadfast",
        courier_status="pending",
        courier_order_id="1424107",
        courier_tracking_id="15BAEB8A",
    )
    client = SimpleNamespace(steadfast_webhook_token="encrypted-token")
    db = _ResultsDb(order, client)
    monkeypatch.setattr(courier_webhook, "decrypt_token", lambda *_args, **_kwargs: "steadfast-webhook-token")

    async def fake_status_change(_db, received_order, status):
        assert received_order is order
        assert status == "delivered"
        return {"status": "applied", "current_status": "delivered"}

    monkeypatch.setattr(courier_webhook, "process_courier_status_change", fake_status_change)
    request = _request(
        b'{"consignment_id":1424107,"invoice":"Aa12-das4","status":"delivered","cod_amount":1000,"updated_at":"2026-06-02T00:00:00Z"}',
        headers=[(b"authorization", b"Bearer steadfast-webhook-token")],
    )

    response = await courier_webhook.steadfast_webhook(request, db)

    assert response == {"status": "applied", "current_status": "delivered"}
    assert db.committed is True
    assert "steadfast" in db.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_redx_header_callback_uses_client_webhook_secret(monkeypatch):
    order = _order(courier_provider="redx", courier_status="pending")
    client = SimpleNamespace(redx_webhook_secret="encrypted-token")
    db = _ResultsDb(order, client)
    monkeypatch.setattr(courier_webhook, "decrypt_token", lambda *_args, **_kwargs: "redx-callback-token")
    monkeypatch.setattr(courier_webhook, "REQUIRE_COURIER_WEBHOOK_SECRET", True)

    async def fake_status_change(_db, received_order, status):
        assert received_order is order
        assert status == "agent-hold"
        return {"status": "applied", "current_status": "in_transit"}

    monkeypatch.setattr(courier_webhook, "process_courier_status_change", fake_status_change)
    request = _request(
        b'{"tracking_number":"20A312THJDJ8","timestamp":"2026-06-02T00:00:00Z","status":"agent-hold"}',
        headers=[(b"x-redx-webhook-secret", b"redx-callback-token")],
    )

    response = await courier_webhook.redx_webhook(request, db)

    assert response == {"status": "applied", "current_status": "in_transit"}
    assert db.committed is True
    assert "redx" in db.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_redx_legacy_query_token_still_works_with_warning(monkeypatch, caplog):
    caplog.set_level("WARNING", logger="app.routers.courier_webhook")
    order = _order(courier_provider="redx", courier_status="pending")
    client = SimpleNamespace(redx_webhook_secret="encrypted-token")
    db = _ResultsDb(order, client)
    monkeypatch.setattr(courier_webhook, "decrypt_token", lambda *_args, **_kwargs: "redx-callback-token")
    monkeypatch.setattr(courier_webhook, "REQUIRE_COURIER_WEBHOOK_SECRET", True)

    async def fake_status_change(_db, received_order, status):
        assert received_order is order
        assert status == "agent-hold"
        return {"status": "applied", "current_status": "in_transit"}

    monkeypatch.setattr(courier_webhook, "process_courier_status_change", fake_status_change)
    request = _request(
        b'{"tracking_number":"20A312THJDJ8","timestamp":"2026-06-02T00:00:00Z","status":"agent-hold"}',
        query_string=b"token=redx-callback-token",
    )

    response = await courier_webhook.redx_webhook(request, db)

    assert response == {"status": "applied", "current_status": "in_transit"}
    assert db.committed is True
    assert "legacy query token" in caplog.text
    assert "redx" in db.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_pathao_status_callback_uses_client_webhook_secret_without_internal_guard(monkeypatch):
    order = _order(courier_provider="pathao", courier_status="pending")
    client = SimpleNamespace(pathao_webhook_secret="encrypted-webhook-secret")
    db = _ResultsDb(order, client)

    monkeypatch.setattr(courier_webhook, "decrypt_token", lambda *_args, **_kwargs: "webhook-secret")

    async def fake_status_change(_db, received_order, status):
        assert received_order is order
        assert status == "picked_up"
        return {"status": "applied", "current_status": "in_transit"}

    monkeypatch.setattr(courier_webhook, "process_courier_status_change", fake_status_change)
    request = _request(
        b'{"consignment_id":"DT1","status":"picked_up"}',
        headers=[(b"x-pathao-signature", b"webhook-secret")],
    )

    response = await courier_webhook.pathao_webhook(request, db)

    assert response.status_code == 200
    assert response.headers["X-Pathao-Merchant-Webhook-Integration-Secret"] == "webhook-secret"
    assert db.committed is True
    assert client.pathao_webhook_verified_at is not None
    assert "pathao" in db.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_legacy_public_tracking_returns_not_found_for_ambiguous_reference():
    class _AmbiguousResult:
        def scalars(self):
            return self

        def all(self):
            return [_order(), _order()]

    class _AmbiguousDb:
        async def execute(self, *_args, **_kwargs):
            return _AmbiguousResult()

    response = await courier_webhook.public_courier_tracking("duplicate-id", db=_AmbiguousDb())

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pathao_panel_delivered_test_returns_configured_header_for_unknown_sample_order(monkeypatch):
    monkeypatch.setattr(courier_webhook, "PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET", "configured-integration-secret")
    request = _request(
        b'{"consignment_id":"DL121224VS8TTJ","event":"order.delivered"}',
        headers=[(b"x-pathao-signature", b"different-request-signature")],
    )

    response = await courier_webhook.pathao_webhook(request, _EmptyQueryDb())

    assert response.status_code == 200
    assert response.headers["X-Pathao-Merchant-Webhook-Integration-Secret"] == "configured-integration-secret"
    assert response.body == b'{"status":"ignored","reason":"order not found"}'


@pytest.mark.asyncio
async def test_unknown_status_is_audited_without_regression():
    db = _NoQueryDb()
    order = _order()

    result = await courier_webhook.process_courier_status_change(db, order, "mystery state")

    assert result == {
        "status": "ignored",
        "reason": "unknown_status",
        "current_status": "in_transit",
    }
    assert order.courier_status == "in_transit"
    assert order.status_history[-1]["outcome"] == "ignored"
    assert order.status_history[-1]["reason"] == "unknown_status"
    assert db.added == [order]


@pytest.mark.asyncio
async def test_stale_status_is_audited_without_regression():
    db = _NoQueryDb()
    order = _order(courier_status="delivered")

    result = await courier_webhook.process_courier_status_change(
        db,
        order,
        "delivery-in-progress",
    )

    assert result == {
        "status": "ignored",
        "reason": "stale_transition",
        "current_status": "delivered",
    }
    assert order.courier_status == "delivered"
    assert order.status_history[-1]["reason"] == "stale_transition:delivered->in_transit"
    assert db.added == [order]


@pytest.mark.asyncio
async def test_duplicate_status_returns_without_history_growth():
    db = _NoQueryDb()
    order = _order()

    result = await courier_webhook.process_courier_status_change(
        db,
        order,
        "delivery-in-progress",
    )

    assert result == {"status": "duplicate", "current_status": "in_transit"}
    assert order.status_history == []
    assert db.added == []


@pytest.mark.asyncio
async def test_pathao_polling_statuses_advance_shipment_history():
    db = _EmptyQueryDb()
    order = _order(courier_provider="pathao", courier_status="pending")

    picked_up = await courier_webhook.process_courier_status_change(db, order, "picked_up")
    delivered = await courier_webhook.process_courier_status_change(db, order, "delivered")

    assert picked_up == {
        "status": "applied",
        "previous_status": "pending",
        "current_status": "in_transit",
    }
    assert delivered == {
        "status": "applied",
        "previous_status": "in_transit",
        "current_status": "delivered",
    }
    assert order.courier_status == "delivered"
    assert [entry["status"] for entry in order.status_history] == ["in_transit", "delivered"]


def test_status_history_is_capped():
    order = _order()

    for index in range(105):
        courier_webhook._append_status_history(
            order,
            mapped_status="pending",
            raw_status=f"pending-{index}",
        )

    assert len(order.status_history) == 100
    assert order.status_history[0]["raw_status"] == "pending-5"
    assert order.status_history[-1]["raw_status"] == "pending-104"


def test_status_history_append_marks_json_column_dirty():
    order = CourierOrder(
        client_id=1,
        order_id="1002",
        courier_provider="pathao",
        courier_status="pending",
        status_history=[{"status": "pending"}],
    )

    courier_webhook._append_status_history(
        order,
        mapped_status="in_transit",
        raw_status="picked_up",
    )

    assert inspect(order).attrs.status_history.history.has_changes()
