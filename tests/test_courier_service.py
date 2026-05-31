import pytest

from app.services import courier_service
from app.services.courier_service import CourierService


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "status": 200,
            "message": "Consignment has been created successfully.",
            "consignment": {
                "consignment_id": 1424107,
                "tracking_code": "15BAEB8A",
            },
        }


class FakeHttpClient:
    def __init__(self):
        self.request = None

    async def post(self, url, json, headers):
        self.request = {"url": url, "json": json, "headers": headers}
        return FakeResponse()


@pytest.mark.asyncio
async def test_send_to_steadfast_uses_documented_response_and_phone_format(monkeypatch):
    http = FakeHttpClient()

    async def fake_get_http_client():
        return http

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)

    result = await CourierService.send_to_steadfast(
        api_key="api-key",
        secret_key="secret-key",
        recipient_name="Customer",
        recipient_phone="+8801837224409",
        recipient_address="Dhaka",
        cod_amount=15,
        merchant_order_id="8927",
    )

    assert result["success"] is True
    assert result["courier_order_id"] == "1424107"
    assert result["tracking_id"] == "15BAEB8A"
    assert http.request["json"]["recipient_phone"] == "01837224409"


@pytest.mark.asyncio
async def test_check_steadfast_status_uses_delivery_status_field(monkeypatch):
    class FakeStatusResponse:
        status_code = 200

        def json(self):
            return {"status": 200, "delivery_status": "in_review"}

    class FakeStatusHttpClient:
        async def get(self, url, headers):
            return FakeStatusResponse()

    async def fake_get_http_client():
        return FakeStatusHttpClient()

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)

    status = await CourierService.check_steadfast_status(
        api_key="api-key",
        secret_key="secret-key",
        tracking_code="15BAEB8A",
    )

    assert status == "in_review"


@pytest.mark.asyncio
async def test_send_to_redx_uses_token_defaults_and_normalized_phone(monkeypatch):
    class FakeRedxResponse:
        status_code = 201
        text = '{"tracking_id":"20A312THJDJ8"}'

        def json(self):
            return {"tracking_id": "20A312THJDJ8"}

    class FakeRedxHttpClient:
        def __init__(self):
            self.request = None

        async def post(self, url, json, headers):
            self.request = {"url": url, "json": json, "headers": headers}
            return FakeRedxResponse()

    http = FakeRedxHttpClient()

    async def fake_get_http_client():
        return http

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)

    result = await CourierService.send_to_redx(
        access_token="token",
        recipient_name="Customer",
        recipient_phone="+8801837224409",
        recipient_address="Dhaka",
        cod_amount=15,
        merchant_order_id="8927",
        delivery_area_id="12",
        delivery_area_name="Mirpur DOHS",
        pickup_store_id="1",
        item_weight=0.5,
    )

    assert result["tracking_id"] == "20A312THJDJ8"
    assert http.request["headers"]["API-ACCESS-TOKEN"] == "Bearer token"
    assert http.request["json"]["customer_phone"] == "01837224409"
    assert http.request["json"]["parcel_weight"] == 500
    assert http.request["json"]["pickup_store_id"] == 1


def test_map_redx_statuses():
    assert CourierService.map_status("redx", "delivery-in-progress") == "in_transit"
    assert CourierService.map_status("redx", "delivered") == "delivered"
    assert CourierService.map_status("redx", "agent-returning") == "returned"
