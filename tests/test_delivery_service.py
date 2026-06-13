import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from app.schemas.event import EventData
from app.services.delivery_service import deliver_events_to_platforms, is_event_enabled_for_platform
from app.security import encrypt_token


def test_pending_meta_credentials_do_not_enable_delivery():
    client = SimpleNamespace(
        pixel_id="0",
        access_token=encrypt_token("pending_setup"),
        enable_facebook=True,
        event_rules=None,
    )

    assert not is_event_enabled_for_platform(client, "PageView", "meta")

@pytest.mark.anyio
@patch("app.services.delivery_service.send_to_facebook", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_tiktok", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_ga4", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_webhook", new_callable=AsyncMock)
async def test_delivery_facebook_primary(
    mock_send_webhook, mock_send_ga4, mock_send_tiktok, mock_send_facebook
):
    client = SimpleNamespace(
        id=1,
        name="Test Client",
        pixel_id="123456",
        access_token=encrypt_token("fb-token"),
        enable_facebook=True,
        tiktok_pixel_id="tiktok-pixel",
        tiktok_access_token="tiktok-token",
        enable_tiktok=True,
        ga4_measurement_id="ga4-id",
        ga4_api_secret="ga4-secret",
        enable_ga4=True,
        webhook_url="https://example.com/webhook",
    )
    events = [
        EventData(
            event_name="PageView",
            event_time=1710000000,
            event_id="evt-123",
            user_data={"client_ip_address": "127.0.0.1"},
        )
    ]
    context = {"cookies": {"_fbp": "fbp-val"}, "ip_address": "127.0.0.1"}

    # Setup mocks
    mock_send_facebook.return_value = {"fb_ok": True}
    mock_send_tiktok.return_value = {"code": 0, "sent_count": 1}
    mock_send_ga4.return_value = {"ok": True}
    mock_send_webhook.return_value = True

    # Patch database logging methods to avoid hitting real database
    with patch("app.services.delivery_service._log_secondary_success", new_callable=AsyncMock), \
         patch("app.services.delivery_service._log_secondary_failure", new_callable=AsyncMock):
        res = await deliver_events_to_platforms(client, events, context)
        if "_tasks" in res:
            import asyncio
            await asyncio.gather(*res["_tasks"], return_exceptions=True)

    assert res["primary_platform"] == "Facebook"
    assert res["result"] == {"fb_ok": True}

    mock_send_facebook.assert_called_once_with(client, events)
    mock_send_tiktok.assert_called_once_with(client, events)
    mock_send_ga4.assert_called_once()
    mock_send_webhook.assert_called_once()


@pytest.mark.anyio
@patch("app.services.delivery_service.send_to_facebook", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_tiktok", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_ga4", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_webhook", new_callable=AsyncMock)
async def test_delivery_tiktok_primary(
    mock_send_webhook, mock_send_ga4, mock_send_tiktok, mock_send_facebook
):
    client = SimpleNamespace(
        id=1,
        name="Test Client",
        pixel_id=None,
        access_token=None,
        enable_facebook=False,
        tiktok_pixel_id="tiktok-pixel",
        tiktok_access_token="tiktok-token",
        enable_tiktok=True,
        ga4_measurement_id="ga4-id",
        ga4_api_secret="ga4-secret",
        enable_ga4=True,
        webhook_url=None,
    )
    events = [EventData(event_name="AddToCart", event_time=1710000000, event_id="evt-456")]
    context = {}

    mock_send_tiktok.return_value = {"code": 0, "sent_count": 1}
    mock_send_ga4.return_value = {"ok": True}

    with patch("app.services.delivery_service._log_secondary_success", new_callable=AsyncMock), \
         patch("app.services.delivery_service._log_secondary_failure", new_callable=AsyncMock):
        res = await deliver_events_to_platforms(client, events, context)
        if "_tasks" in res:
            import asyncio
            await asyncio.gather(*res["_tasks"], return_exceptions=True)

    assert res["primary_platform"] == "TikTok"
    mock_send_facebook.assert_not_called()
    mock_send_tiktok.assert_called_once_with(client, events)
    mock_send_ga4.assert_called_once()
    mock_send_webhook.assert_not_called()


@pytest.mark.anyio
@patch("app.services.delivery_service.send_to_facebook", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_tiktok", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_ga4", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_webhook", new_callable=AsyncMock)
async def test_delivery_webhook_only_primary_success(
    mock_send_webhook, mock_send_ga4, mock_send_tiktok, mock_send_facebook
):
    client = SimpleNamespace(
        id=1,
        name="Webhook Client",
        pixel_id=None,
        access_token=None,
        enable_facebook=False,
        tiktok_pixel_id=None,
        tiktok_access_token=None,
        enable_tiktok=False,
        ga4_measurement_id=None,
        ga4_api_secret=None,
        enable_ga4=False,
        webhook_url="https://example.com/webhook",
    )
    events = [EventData(event_name="Purchase", event_time=1710000000, event_id="evt-789")]
    mock_send_webhook.return_value = True

    res = await deliver_events_to_platforms(client, events, {})

    assert res["primary_platform"] == "Webhook"
    assert res["result"] == {"ok": True, "sent_count": 1}
    mock_send_facebook.assert_not_called()
    mock_send_tiktok.assert_not_called()
    mock_send_ga4.assert_not_called()
    mock_send_webhook.assert_called_once()


@pytest.mark.anyio
@patch("app.services.delivery_service.send_webhook", new_callable=AsyncMock)
async def test_delivery_webhook_only_primary_failure_raises(mock_send_webhook):
    client = SimpleNamespace(
        id=1,
        name="Webhook Client",
        pixel_id=None,
        access_token=None,
        enable_facebook=False,
        tiktok_pixel_id=None,
        tiktok_access_token=None,
        enable_tiktok=False,
        ga4_measurement_id=None,
        ga4_api_secret=None,
        enable_ga4=False,
        webhook_url="https://example.com/webhook",
    )
    events = [EventData(event_name="Purchase", event_time=1710000000, event_id="evt-789")]
    mock_send_webhook.return_value = False

    with pytest.raises(RuntimeError, match="Webhook send failed"):
        await deliver_events_to_platforms(client, events, {})


@pytest.mark.anyio
@patch("app.services.delivery_service.send_to_facebook", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_tiktok", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_ga4", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_webhook", new_callable=AsyncMock)
async def test_delivery_with_routing_rules(
    mock_send_webhook, mock_send_ga4, mock_send_tiktok, mock_send_facebook
):
    client = SimpleNamespace(
        id=1,
        name="Test Rules Client",
        pixel_id="123456",
        access_token=encrypt_token("fb-token"),
        enable_facebook=True,
        tiktok_pixel_id="tiktok-pixel",
        tiktok_access_token="tiktok-token",
        enable_tiktok=True,
        ga4_measurement_id="ga4-id",
        ga4_api_secret="ga4-secret",
        enable_ga4=True,
        webhook_url=None,
        event_rules=[
            {"eventName": "PageView", "metaEnabled": True, "tiktokEnabled": True, "ga4Enabled": True},
            {"eventName": "InitiateCheckout", "metaEnabled": True, "tiktokEnabled": False, "ga4Enabled": True}
        ]
    )
    events = [
        EventData(event_name="PageView", event_time=1710000000, event_id="evt-1"),
        EventData(event_name="InitiateCheckout", event_time=1710000000, event_id="evt-2"),
    ]
    context = {"ip_address": "127.0.0.1"}

    mock_send_facebook.return_value = {"fb_ok": True}
    mock_send_tiktok.return_value = {"code": 0, "sent_count": 1}
    mock_send_ga4.return_value = {"ok": True}

    with patch("app.services.delivery_service._log_secondary_success", new_callable=AsyncMock), \
         patch("app.services.delivery_service._log_secondary_failure", new_callable=AsyncMock):
        res = await deliver_events_to_platforms(client, events, context)
        if "_tasks" in res:
            import asyncio
            await asyncio.gather(*res["_tasks"], return_exceptions=True)

    assert res["primary_platform"] == "Facebook"
    # Facebook should get both events
    mock_send_facebook.assert_called_once_with(client, events)
    # TikTok should only get the PageView event since InitiateCheckout is disabled in rules
    mock_send_tiktok.assert_called_once()
    called_tiktok_events = mock_send_tiktok.call_args[0][1]
    assert len(called_tiktok_events) == 1
    assert called_tiktok_events[0].event_name == "PageView"

    # GA4 should get both events
    mock_send_ga4.assert_called_once()
    called_ga4_events = mock_send_ga4.call_args_list[0][1]["events"]
    assert len(called_ga4_events) == 2


@pytest.mark.anyio
@patch("app.services.delivery_service.send_to_facebook", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_tiktok", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_ga4", new_callable=AsyncMock)
async def test_delivery_reports_only_events_sent_to_primary_platform(
    mock_send_ga4, mock_send_tiktok, mock_send_facebook
):
    client = SimpleNamespace(
        id=1,
        name="Test Rules Client",
        pixel_id="123456",
        access_token=encrypt_token("fb-token"),
        enable_facebook=True,
        tiktok_pixel_id="tiktok-pixel",
        tiktok_access_token="tiktok-token",
        enable_tiktok=True,
        ga4_measurement_id="ga4-id",
        ga4_api_secret="ga4-secret",
        enable_ga4=True,
        webhook_url=None,
        event_rules=[
            {"eventName": "PageView", "metaEnabled": True, "tiktokEnabled": True, "ga4Enabled": True},
            {"eventName": "InitiateCheckout", "metaEnabled": False, "tiktokEnabled": False, "ga4Enabled": True},
        ],
    )
    page_view = EventData(event_name="PageView", event_time=1710000000, event_id="evt-1")
    checkout = EventData(event_name="InitiateCheckout", event_time=1710000000, event_id="evt-2")

    mock_send_facebook.return_value = {"fb_ok": True}
    mock_send_tiktok.return_value = {"code": 0, "sent_count": 1}
    mock_send_ga4.return_value = {"ok": True}

    with patch("app.services.delivery_service._log_secondary_success", new_callable=AsyncMock), \
         patch("app.services.delivery_service._log_secondary_failure", new_callable=AsyncMock):
        res = await deliver_events_to_platforms(client, [page_view, checkout], {})
        if "_tasks" in res:
            import asyncio
            await asyncio.gather(*res["_tasks"], return_exceptions=True)

    assert res["primary_platform"] == "Facebook"
    assert res["primary_events"] == [page_view]
    assert res["filtered_events"] == [checkout]
    mock_send_facebook.assert_called_once_with(client, [page_view])


@pytest.mark.anyio
@patch("app.services.delivery_service.send_to_facebook", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_tiktok", new_callable=AsyncMock)
@patch("app.services.delivery_service.send_to_ga4", new_callable=AsyncMock)
async def test_delivery_with_all_events_filtered(mock_send_ga4, mock_send_tiktok, mock_send_facebook):
    client = SimpleNamespace(
        id=1,
        name="Test Rules Client",
        pixel_id="123456",
        access_token=encrypt_token("fb-token"),
        enable_facebook=True,
        tiktok_pixel_id="tiktok-pixel",
        tiktok_access_token="tiktok-token",
        enable_tiktok=True,
        ga4_measurement_id="ga4-id",
        ga4_api_secret="ga4-secret",
        enable_ga4=True,
        webhook_url=None,
        event_rules=[
            {"eventName": "InitiateCheckout", "metaEnabled": False, "tiktokEnabled": False, "ga4Enabled": False}
        ]
    )
    events = [
        EventData(event_name="InitiateCheckout", event_time=1710000000, event_id="evt-2"),
    ]
    context = {"ip_address": "127.0.0.1"}

    res = await deliver_events_to_platforms(client, events, context)
    assert res["primary_platform"] == "None"
    assert res["result"]["ok"] is True
    assert "filtered" in res["result"]["message"].lower()

    mock_send_facebook.assert_not_called()
    mock_send_tiktok.assert_not_called()
    mock_send_ga4.assert_not_called()
