import pytest

from app.services import courier_service
from app.services.courier_service import CourierService, CourierServiceException


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
async def test_send_to_steadfast_rejects_success_response_without_tracking_ids(monkeypatch):
    class MissingIdsResponse:
        status_code = 200

        def json(self):
            return {"status": 200, "consignment": {}}

    class MissingIdsHttpClient:
        async def post(self, *_args, **_kwargs):
            return MissingIdsResponse()

    async def fake_get_http_client():
        return MissingIdsHttpClient()

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)

    result = await CourierService.send_to_steadfast(
        api_key="api-key",
        secret_key="secret-key",
        recipient_name="Customer",
        recipient_phone="01837224409",
        recipient_address="Dhaka",
        cod_amount=15,
        merchant_order_id="8927",
    )

    assert result["success"] is False
    assert "tracking IDs" in result["error"]


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
async def test_check_pathao_status_uses_order_status_slug(monkeypatch):
    class FakeStatusResponse:
        status_code = 200

        def json(self):
            return {
                "type": "success",
                "code": 200,
                "data": {
                    "consignment_id": "DT010626DCWRX9",
                    "order_status": "Pending",
                    "order_status_slug": "pending",
                },
            }

    class FakeStatusHttpClient:
        async def get(self, url, headers):
            return FakeStatusResponse()

    async def fake_get_http_client():
        return FakeStatusHttpClient()

    async def fake_get_pathao_token(*_args, **_kwargs):
        return "token"

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(CourierService, "get_pathao_token", fake_get_pathao_token)

    status = await CourierService.check_pathao_status(
        client_id="client-id",
        client_secret="client-secret",
        email="merchant@example.com",
        password="password",
        consignment_id="DT010626DCWRX9",
    )

    assert status == "pending"


@pytest.mark.asyncio
async def test_pathao_live_city_list_uses_documented_endpoint(monkeypatch):
    class FakeCityResponse:
        status_code = 200

        def json(self):
            return {"data": {"data": []}}

    class FakeCityHttpClient:
        def __init__(self):
            self.url = None

        async def get(self, url, headers):
            self.url = url
            return FakeCityResponse()

    http = FakeCityHttpClient()

    async def fake_get_http_client():
        return http

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)
    await CourierService._get_pathao_cities("live-city-token", base_url="https://api-hermes.pathao.com")

    assert http.url == "https://api-hermes.pathao.com/aladdin/api/v1/city-list"


@pytest.mark.asyncio
async def test_send_to_pathao_live_lets_pathao_resolve_optional_location_ids(monkeypatch):
    class FakePathaoResponse:
        status_code = 200

        def json(self):
            return {"data": {"consignment_id": "DT-LIVE-1"}}

    class FakePathaoHttpClient:
        def __init__(self):
            self.request = None

        async def post(self, url, json, headers):
            self.request = {"url": url, "json": json, "headers": headers}
            return FakePathaoResponse()

    http = FakePathaoHttpClient()

    async def fake_get_http_client():
        return http

    async def fake_get_pathao_token(*_args, **_kwargs):
        return "live-token"

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(CourierService, "get_pathao_token", fake_get_pathao_token)

    result = await CourierService.send_to_pathao(
        client_id="client-id",
        client_secret="client-secret",
        email="merchant@example.com",
        password="password",
        store_id="123",
        recipient_name="Customer",
        recipient_phone="01837224409",
        recipient_address="House 1, Dhaka",
        cod_amount=90,
        merchant_order_id="1003",
        base_url="https://api-hermes.pathao.com",
    )

    assert result["success"] is True
    assert "recipient_city" not in http.request["json"]
    assert "recipient_zone" not in http.request["json"]
    assert "recipient_area" not in http.request["json"]


@pytest.mark.asyncio
async def test_pathao_location_resolution_does_not_fallback_to_dhaka(monkeypatch):
    with pytest.raises(CourierServiceException, match="could not be resolved"):
        await CourierService.resolve_pathao_location(
            "token",
            "House 12, Unknown Market",
            base_url="https://courier-api-sandbox.pathao.com",
        )

    async def fake_empty_cities(*_args, **_kwargs):
        return []

    monkeypatch.setattr(CourierService, "_get_pathao_cities", fake_empty_cities)
    with pytest.raises(CourierServiceException, match="city list is empty"):
        await CourierService.resolve_pathao_location(
            "token",
            "House 1, Dhaka",
            base_url="https://courier-api-sandbox.pathao.com",
        )


@pytest.mark.asyncio
async def test_pathao_location_resolution_does_not_default_missing_zone_or_area(monkeypatch):
    async def fake_cities(*_args, **_kwargs):
        return [{"city_id": 7, "city_name": "Dhaka"}]

    async def fake_empty_zones(*_args, **_kwargs):
        return []

    monkeypatch.setattr(CourierService, "_get_pathao_cities", fake_cities)
    monkeypatch.setattr(CourierService, "_get_pathao_zones", fake_empty_zones)

    with pytest.raises(CourierServiceException, match="No Pathao zones"):
        await CourierService.resolve_pathao_location(
            "token",
            "House 1, Dhaka",
            base_url="https://courier-api-sandbox.pathao.com",
        )

    async def fake_zones(*_args, **_kwargs):
        return [{"zone_id": 10, "zone_name": "Mirpur"}]

    async def fake_empty_areas(*_args, **_kwargs):
        return []

    monkeypatch.setattr(CourierService, "_get_pathao_zones", fake_zones)
    monkeypatch.setattr(CourierService, "_get_pathao_areas", fake_empty_areas)

    with pytest.raises(CourierServiceException, match="No Pathao areas"):
        await CourierService.resolve_pathao_location(
            "token",
            "House 1, Mirpur, Dhaka",
            base_url="https://courier-api-sandbox.pathao.com",
        )


@pytest.mark.asyncio
async def test_pathao_location_resolution_requires_zone_and_area_match(monkeypatch):
    async def fake_cities(*_args, **_kwargs):
        return [{"city_id": 7, "city_name": "Dhaka"}]

    async def fake_zones(*_args, **_kwargs):
        return [{"zone_id": 10, "zone_name": "Mirpur"}]

    async def fake_areas(*_args, **_kwargs):
        return [{"area_id": 25, "area_name": "Mirpur DOHS"}]

    monkeypatch.setattr(CourierService, "_get_pathao_cities", fake_cities)
    monkeypatch.setattr(CourierService, "_get_pathao_zones", fake_zones)
    monkeypatch.setattr(CourierService, "_get_pathao_areas", fake_areas)

    with pytest.raises(CourierServiceException, match="zone could not be resolved"):
        await CourierService.resolve_pathao_location(
            "token",
            "House 1, Dhaka",
            base_url="https://courier-api-sandbox.pathao.com",
        )

    with pytest.raises(CourierServiceException, match="area could not be resolved"):
        await CourierService.resolve_pathao_location(
            "token",
            "House 1, Mirpur, Dhaka",
            base_url="https://courier-api-sandbox.pathao.com",
        )

    assert await CourierService.resolve_pathao_location(
        "token",
        "House 1, Mirpur DOHS, Dhaka",
        base_url="https://courier-api-sandbox.pathao.com",
    ) == (7, 10, 25)


@pytest.mark.asyncio
async def test_send_to_pathao_sandbox_requires_explicit_location_ids(monkeypatch):
    async def fake_get_pathao_token(*_args, **_kwargs):
        return "sandbox-token"

    monkeypatch.setattr(CourierService, "get_pathao_token", fake_get_pathao_token)

    result = await CourierService.send_to_pathao(
        client_id="client-id",
        client_secret="client-secret",
        email="merchant@example.com",
        password="password",
        store_id="123",
        recipient_name="Customer",
        recipient_phone="01837224409",
        recipient_address="House 1, Dhaka",
        cod_amount=90,
        merchant_order_id="1003",
        base_url="https://courier-api-sandbox.pathao.com",
    )

    assert result["success"] is False
    assert "requires explicit recipient_city" in result["error"]


@pytest.mark.asyncio
async def test_cancel_pathao_auth_failure_keeps_provider_active(monkeypatch):
    async def fake_get_pathao_token(*_args, **_kwargs):
        return None

    monkeypatch.setattr(CourierService, "get_pathao_token", fake_get_pathao_token)

    result = await CourierService.cancel_pathao_order(
        client_id="client-id",
        client_secret="client-secret",
        email="merchant@example.com",
        password="password",
        consignment_id="12345",
    )

    assert result["success"] is False
    assert result["local_only"] is False
    assert result["provider_active"] is True


@pytest.mark.asyncio
async def test_cancel_pathao_body_error_keeps_provider_active(monkeypatch):
    class PathaoCancelErrorResponse:
        status_code = 200
        text = '{"error":true,"message":"Unauthorized!"}'

        def json(self):
            return {"error": True, "message": "Unauthorized!"}

    class PathaoCancelHttpClient:
        async def post(self, *_args, **_kwargs):
            return PathaoCancelErrorResponse()

    async def fake_get_http_client():
        return PathaoCancelHttpClient()

    async def fake_get_pathao_token(*_args, **_kwargs):
        return "token"

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(CourierService, "get_pathao_token", fake_get_pathao_token)

    result = await CourierService.cancel_pathao_order(
        client_id="client-id",
        client_secret="client-secret",
        email="merchant@example.com",
        password="password",
        consignment_id="12345",
    )

    assert result["success"] is False
    assert result["local_only"] is False
    assert result["provider_active"] is True
    assert result["error"] == "Unauthorized!"


@pytest.mark.asyncio
async def test_send_to_pathao_rejects_success_response_without_consignment_id(monkeypatch):
    class MissingIdResponse:
        status_code = 200

        def json(self):
            return {"data": {}}

    class MissingIdHttpClient:
        async def post(self, *_args, **_kwargs):
            return MissingIdResponse()

    async def fake_get_http_client():
        return MissingIdHttpClient()

    async def fake_get_pathao_token(*_args, **_kwargs):
        return "live-token"

    monkeypatch.setattr(courier_service, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(CourierService, "get_pathao_token", fake_get_pathao_token)

    result = await CourierService.send_to_pathao(
        client_id="client-id",
        client_secret="client-secret",
        email="merchant@example.com",
        password="password",
        store_id="123",
        recipient_name="Customer",
        recipient_phone="01837224409",
        recipient_address="House 1, Dhaka",
        cod_amount=90,
        merchant_order_id="1003",
        base_url="https://api-hermes.pathao.com",
    )

    assert result["success"] is False
    assert "consignment ID" in result["error"]


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
    assert CourierService.map_status("redx", "pickup-pending") == "pending"
    assert CourierService.map_status("redx", "delivery-in-progress") == "in_transit"
    assert CourierService.map_status("redx", "delivered") == "delivered"
    assert CourierService.map_status("redx", "agent-returning") == "returned"
    assert CourierService.map_status("redx", "rejected") == "cancelled"
    assert CourierService.map_status("redx", "agent-hold") == "in_transit"
    assert CourierService.map_status("redx", "agent-area-change") == "in_transit"


def test_map_status_normalizes_provider_variants():
    assert CourierService.map_status("steadfast", " PICKED_UP ") == "in_transit"
    assert CourierService.map_status("pathao", "IN TRANSIT") == "in_transit"
    assert CourierService.map_status("pathao", "picked_up") == "in_transit"
    assert CourierService.map_status("redx", "delivery_in_progress") == "in_transit"
    assert CourierService.is_known_status("redx", "DELIVERED") is True


def test_map_steadfast_documented_statuses_without_premature_purchase():
    assert CourierService.map_status("steadfast", "delivered_approval_pending") == "in_transit"
    assert CourierService.map_status("steadfast", "partial_delivered_approval_pending") == "in_transit"
    assert CourierService.map_status("steadfast", "cancelled_approval_pending") == "in_transit"
    assert CourierService.map_status("steadfast", "unknown_approval_pending") == "pending"
    assert CourierService.map_status("steadfast", "partial_delivered") == "partial_delivered"
    assert CourierService.map_status("steadfast", "unknown") == "pending"


def test_status_transition_guard_rejects_stale_callbacks():
    assert CourierService.should_apply_status_transition("in_transit", "pending") is False
    assert CourierService.should_apply_status_transition("delivered", "in_transit") is False
    assert CourierService.should_apply_status_transition("delivered", "returned") is True
    assert CourierService.should_apply_status_transition("pending", "in_transit") is True
    assert CourierService.should_apply_status_transition("partial_delivered", "delivered") is False
