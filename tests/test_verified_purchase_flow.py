import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("ADMIN_PASSWORD", "pbkdf2_sha256$210000$dGVzdC1hZG1pbi1zYWx0LTE=$9gwSQUsI_uzxaNpdvx_cOcpF4opgO7Ma_Hcmq3z4kSU=")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-api-key")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("ENCRYPTION_KEY", "ZFhnf1szwemka8kBbH9jPTC7oKBRTEv0EqWt1J8AD0M=")

from app.routers.client_health import ClientSetupRequest, client_update_setup
from app.routers.deferred_events import BulkConfirmRequest, ConfirmRequest, _pending_event_value, _safe_numeric_value
from app.routers.events import _event_order_id
from app.routers.webhook import _woocommerce_status_meets_threshold
from app.schemas.event import CustomData, EventData
from app.security import decrypt_token


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, client):
        self.client = client
        self.committed = False

    async def execute(self, _stmt):
        return _Result(self.client)

    async def commit(self):
        self.committed = True


def test_deferred_confirm_requests_normalize_order_ids():
    assert ConfirmRequest(order_id="  1001  ").order_id == "1001"
    assert BulkConfirmRequest(order_ids=[" 1001 ", "1001", "", "1002"]).order_ids == ["1001", "1002"]


def test_deferred_summary_value_parsing_ignores_non_numeric_values():
    assert _safe_numeric_value("1,250.50") == 1250.5
    assert _safe_numeric_value("not-a-number") == 0.0
    assert _safe_numeric_value(True) == 0.0
    assert _pending_event_value({"custom_data": {"value": "not-a-number"}}) == 0.0
    assert _pending_event_value({"custom_data": {"value": "950"}}) == 950.0


def test_event_order_id_fallback_is_stable_without_order_or_event_id():
    payload = {
        "event_name": "Purchase",
        "event_time": 1710000000,
        "event_source_url": "https://example.com/checkout/order-received",
        "custom_data": CustomData(value=1250, currency="BDT", content_ids=["sku-1"]),
        "raw_order_data": {"recipient_phone": "01816101745", "items": [{"id": "sku-1", "quantity": 1}]},
    }

    first = _event_order_id(EventData(**payload))
    second = _event_order_id(EventData(**payload))

    assert first == second
    assert first.startswith("auto-1710000000-")
    assert str(id(EventData(**payload))) not in first


def test_woocommerce_confirmation_status_respects_configured_threshold():
    assert _woocommerce_status_meets_threshold("completed", "completed") is True
    assert _woocommerce_status_meets_threshold("processing", "completed") is False
    assert _woocommerce_status_meets_threshold("processing", "processing") is True
    assert _woocommerce_status_meets_threshold("completed", "processing") is True
    assert _woocommerce_status_meets_threshold("courier-booked", "completed") is True
    assert _woocommerce_status_meets_threshold("processing", "invalid") is False


@pytest.mark.anyio
async def test_client_setup_encrypts_ga4_api_secret():
    row = SimpleNamespace(
        id=1,
        api_key="client-api-key",
        domain=None,
        pixel_id="0",
        access_token="pending_setup",
        enable_facebook=False,
        tiktok_pixel_id=None,
        tiktok_access_token=None,
        enable_tiktok=False,
        ga4_measurement_id=None,
        ga4_api_secret=None,
        enable_ga4=False,
    )
    db = _FakeDb(row)

    response = await client_update_setup(
        ClientSetupRequest(ga4_measurement_id="G-TEST", ga4_api_secret="ga-secret"),
        client=SimpleNamespace(id=1),
        db=db,
    )

    assert db.committed
    assert response["enable_ga4"] is True
    assert row.ga4_api_secret != "ga-secret"
    assert decrypt_token(row.ga4_api_secret) == "ga-secret"


@pytest.mark.anyio
async def test_client_setup_updates_deferred_purchase():
    row = SimpleNamespace(
        id=1,
        api_key="client-api-key",
        domain=None,
        pixel_id="0",
        access_token="pending_setup",
        enable_facebook=False,
        tiktok_pixel_id=None,
        tiktok_access_token=None,
        enable_tiktok=False,
        ga4_measurement_id=None,
        ga4_api_secret=None,
        enable_ga4=False,
        deferred_purchase=False,
    )
    db = _FakeDb(row)

    response = await client_update_setup(
        ClientSetupRequest(deferred_purchase=True),
        client=SimpleNamespace(id=1),
        db=db,
    )

    assert db.committed
    assert response["deferred_purchase"] is True
    assert row.deferred_purchase is True
