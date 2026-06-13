import asyncio
import json
import logging
from app.database import AsyncSessionLocal
from app.models.event_log import EventLog
from app.schemas.event import EventData
from app.services.capi_service import send_to_facebook
from app.security import meta_credentials_configured
from app.services.ga4_service import send_to_ga4
from app.services.tiktok_service import send_to_tiktok
from app.services.webhook_service import send_webhook
from app.utils.event_log_helpers import build_event_log_kwargs

logger = logging.getLogger(__name__)


async def wait_for_secondary_tasks(delivery_result: dict) -> None:
    """Keep worker-owned secondary sends alive until their logging completes."""
    tasks = delivery_result.get("_tasks") or []
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _log_secondary_failure(
    client_id: int,
    channel: str,
    event_names: str,
    event_count: int,
    error_message: str,
    ip_address: str | None,
    user_agent: str | None = None,
    device_metadata: dict | None = None,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            kwargs = build_event_log_kwargs(
                client_id,
                {"event_name": event_names, "custom_data": {}},
                "failed",
                ip_address,
                user_agent,
                device_metadata,
                error_message=str(error_message)[:500],
            )
            kwargs["event_name"] = f"{channel}:{event_names}"[:255]
            kwargs["event_count"] = event_count
            db.add(EventLog(**kwargs))
            await db.commit()
    except Exception as log_error:
        logger.warning(f"Secondary failure logging failed: {log_error}")

async def _log_secondary_success(
    client_id: int,
    channel: str,
    events: list[EventData],
    response_payload: object,
    ip_address: str | None,
    user_agent: str | None = None,
    device_metadata: dict | None = None,
) -> None:
    """Record non-primary platform delivery while preserving per-event dedup keys."""
    try:
        async with AsyncSessionLocal() as db:
            for event in events:
                event_data = event.model_dump(exclude_none=True)
                kwargs = build_event_log_kwargs(
                    client_id,
                    event_data,
                    "success",
                    ip_address,
                    user_agent,
                    device_metadata,
                    fb_response=json.dumps({
                        "channel": channel,
                        "response": response_payload,
                    }, default=str)[:5000],
                )
                kwargs["event_name"] = f"{channel}:{event.event_name}"[:255]
                kwargs["event_count"] = 0
                db.add(EventLog(**kwargs))
            await db.commit()
    except Exception as log_error:
        logger.warning(f"Secondary success logging failed: {log_error}")

async def _send_tiktok_secondary(client, events: list[EventData], event_names: str, context: dict) -> None:
    ip_address = context.get("ip_address")
    user_agent = context.get("user_agent")
    device_metadata = context.get("device")
    try:
        tiktok_result = await send_to_tiktok(client, events)
        if not tiktok_result or tiktok_result.get("code") not in (0, None):
            await _log_secondary_failure(
                client.id,
                "TikTok",
                event_names,
                len(events),
                tiktok_result or "TikTok send failed",
                ip_address,
                user_agent,
                device_metadata,
            )
            return

        if tiktok_result.get("sent_count") == 0:
            logger.debug(
                f"[{client.name}] TikTok secondary skipped unsupported event(s): {event_names}"
            )
            return

        await _log_secondary_success(
            client.id,
            "TikTok",
            events,
            tiktok_result,
            ip_address,
            user_agent,
            device_metadata,
        )
    except Exception as secondary_error:
        logger.warning(f"[{client.name}] TikTok secondary send failed: {secondary_error}")
        await _log_secondary_failure(
            client.id,
            "TikTok",
            event_names,
            len(events),
            str(secondary_error),
            ip_address,
            user_agent,
            device_metadata,
        )

async def _send_ga4_secondary(
    client,
    events_data: list[dict],
    event_names: str,
    context: dict,
) -> None:
    try:
        ga4_result = await send_to_ga4(
            events=events_data,
            measurement_id=client.ga4_measurement_id,
            api_secret=client.ga4_api_secret,
            cookies=context.get("cookies") or {},
            ip_address=context.get("ip_address"),
            user_agent=context.get("user_agent") or "",
        )
        if ga4_result and not ga4_result.get("ok", True):
            await _log_secondary_failure(
                client.id,
                "GA4",
                event_names,
                len(events_data),
                ga4_result.get("error") or ga4_result,
                context.get("ip_address"),
                context.get("user_agent"),
                context.get("device"),
            )
    except Exception as secondary_error:
        logger.warning(f"[{client.name}] GA4 secondary send failed: {secondary_error}")
        await _log_secondary_failure(
            client.id,
            "GA4",
            event_names,
            len(events_data),
            str(secondary_error),
            context.get("ip_address"),
            context.get("user_agent"),
            context.get("device"),
        )

async def _send_webhook_secondary(client, events_data: list[dict], context: dict) -> None:
    for event_data in events_data:
        try:
            sent = await send_webhook(
                client.webhook_url,
                "event.sent",
                {
                    "client_name": client.name,
                    "event_name": event_data.get("event_name"),
                    "event_id": event_data.get("event_id"),
                    "custom_data": event_data.get("custom_data", {}),
                },
            )
            if not sent:
                await _log_secondary_failure(
                    client.id,
                    "Webhook",
                    event_data.get("event_name") or "unknown",
                    1,
                    "Webhook send failed",
                    context.get("ip_address"),
                    context.get("user_agent"),
                    context.get("device"),
                )
        except Exception as secondary_error:
            logger.warning(f"[{client.name}] Outbound webhook failed: {secondary_error}")
            await _log_secondary_failure(
                client.id,
                "Webhook",
                event_data.get("event_name") or "unknown",
                1,
                str(secondary_error),
                context.get("ip_address"),
                context.get("user_agent"),
                context.get("device"),
            )

def is_event_enabled_for_platform(client, event_name: str, platform: str) -> bool:
    """Check if an event is enabled for a platform under client's event routing rules."""
    # First check global settings
    if platform == "meta":
        global_enabled = bool(
            getattr(client, "enable_facebook", True)
            and meta_credentials_configured(client)
        )
    elif platform == "tiktok":
        global_enabled = bool(getattr(client, "enable_tiktok", True) and client.tiktok_pixel_id and client.tiktok_access_token)
    elif platform == "ga4":
        global_enabled = bool(getattr(client, "enable_ga4", True) and client.ga4_measurement_id and client.ga4_api_secret)
    else:
        global_enabled = False

    if not global_enabled:
        return False

    # If the client has custom rules, check them
    rules = getattr(client, "event_rules", None)
    if rules and isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict) and rule.get("eventName") == event_name:
                rule_key = f"{platform}Enabled"
                if rule_key in rule:
                    return bool(rule.get(rule_key))
                break

    return global_enabled


async def deliver_events_to_platforms(
    client,
    events: list[EventData],
    context: dict,
) -> dict:
    """
    Deliver events to enabled ad/analytics platforms.
    Determines primary and secondary platforms based on active settings.
    Raises exceptions if the primary delivery platform fails.
    Fires secondary deliveries as parallel background tasks.
    """
    # Filter events for each platform based on custom event routing rules
    facebook_events = [e for e in events if is_event_enabled_for_platform(client, e.event_name, "meta")]
    tiktok_events = [e for e in events if is_event_enabled_for_platform(client, e.event_name, "tiktok")]
    ga4_events = [e for e in events if is_event_enabled_for_platform(client, e.event_name, "ga4")]
    
    facebook_enabled = len(facebook_events) > 0
    tiktok_enabled = len(tiktok_events) > 0
    ga4_enabled = len(ga4_events) > 0
    webhook_enabled = bool(client.webhook_url)

    if not any([facebook_enabled, tiktok_enabled, ga4_enabled, webhook_enabled]):
        logger.debug(f"[{client.name}] All events in batch filtered by rules or no platforms enabled.")
        return {
            "primary_platform": "None",
            "result": {"ok": True, "message": "Filtered by custom event routing rules"},
            "primary_events": [],
            "filtered_events": events,
            "_tasks": [],
        }

    event_names = ", ".join(sorted({event.event_name for event in events}))
    events_data = [event.model_dump(exclude_none=True) for event in events]
    # Filter the dict representations for ga4/webhook
    ga4_events_data = [event.model_dump(exclude_none=True) for event in ga4_events]
    webhook_events_data = events_data  # webhook gets everything

    result = None
    primary_platform = None
    primary_tiktok_sent = False
    primary_ga4_sent = False

    if facebook_enabled:
        result = await send_to_facebook(client, facebook_events)
        primary_platform = "Facebook"
        primary_events = facebook_events
    elif tiktok_enabled:
        tiktok_result = await send_to_tiktok(client, tiktok_events)
        if not tiktok_result or tiktok_result.get("code") not in (0, None):
            raise RuntimeError(f"TikTok send failed: {tiktok_result}")
        result = tiktok_result
        primary_tiktok_sent = True
        primary_platform = "TikTok"
        primary_events = tiktok_events
    elif ga4_enabled:
        ga4_result = await send_to_ga4(
            events=ga4_events_data,
            measurement_id=client.ga4_measurement_id,
            api_secret=client.ga4_api_secret,
            cookies=context.get("cookies") or {},
            ip_address=context.get("ip_address"),
            user_agent=context.get("user_agent") or "",
        )
        if ga4_result and not ga4_result.get("ok", True):
            raise RuntimeError(f"GA4 send failed: {ga4_result.get('error') or ga4_result}")
        result = ga4_result
        primary_ga4_sent = True
        primary_platform = "GA4"
        primary_events = ga4_events
    elif webhook_enabled:
        webhook_errors = []
        for event_data in webhook_events_data:
            sent = await send_webhook(
                client.webhook_url,
                "event.sent",
                {
                    "client_name": client.name,
                    "event_name": event_data.get("event_name"),
                    "event_id": event_data.get("event_id"),
                    "custom_data": event_data.get("custom_data", {}),
                },
            )
            if not sent:
                webhook_errors.append(event_data.get("event_name") or "unknown")
        if webhook_errors:
            raise RuntimeError(f"Webhook send failed for: {', '.join(webhook_errors)}")
        result = {"ok": True, "sent_count": len(webhook_events_data)}
        primary_platform = "Webhook"
        primary_events = events

    # Fire secondary sends in parallel as background tasks
    secondary_tasks = []
    if tiktok_enabled and not primary_tiktok_sent:
        secondary_tasks.append(
            _send_tiktok_secondary(
                client,
                tiktok_events,
                ", ".join(sorted({e.event_name for e in tiktok_events})),
                context,
            )
        )

    if ga4_enabled and not primary_ga4_sent:
        secondary_tasks.append(
            _send_ga4_secondary(
                client,
                ga4_events_data,
                ", ".join(sorted({e.event_name for e in ga4_events})),
                context
            )
        )

    if webhook_enabled and primary_platform != "Webhook":
        secondary_tasks.append(_send_webhook_secondary(client, webhook_events_data, context))

    # Fire secondary sends as true background tasks — don't block the outbox worker
    tasks = []
    for coro in secondary_tasks:
        task = asyncio.create_task(coro)

        def _on_secondary_done(t: asyncio.Task) -> None:
            exc = t.exception()
            if exc:
                logger.error(f"Secondary send failed: {exc}")

        task.add_done_callback(_on_secondary_done)
        tasks.append(task)

    return {
        "primary_platform": primary_platform,
        "result": result,
        "primary_events": primary_events,
        "filtered_events": [event for event in events if event not in primary_events],
        "_tasks": tasks,
    }
