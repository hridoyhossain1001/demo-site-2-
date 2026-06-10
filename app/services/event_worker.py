"""
Durable event outbox worker.

The /events endpoint persists accepted events into event_outbox and returns quickly.
This worker claims queued rows and sends them to downstream services.
"""
import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.database import AsyncSessionLocal
from app.dependencies import _snapshot
from app.models.client import Client
from app.models.event_log import EventLog
from app.models.event_outbox import EventOutbox
from app.models.pending_event import PendingEvent
from app.schemas.event import EventData, UserData, _clean_and_hash
from app.services.delivery_service import deliver_events_to_platforms, wait_for_secondary_tasks
from app.services.event_quality import boost_event_quality
from app.services.geoip_service import get_location_data
from app.services.redis_pool import get_redis, record_redis_fallback
from app.services.usage_service import increment_usage_counters_db, rollback_usage_reservation
from app.utils.event_log_helpers import build_event_log_kwargs as _event_log_kwargs

logger = logging.getLogger(__name__)

WORKER_ID = os.getenv("EVENT_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
WORKER_BATCH_SIZE = int(os.getenv("EVENT_WORKER_BATCH_SIZE", "5"))
WORKER_POLL_SECONDS = float(os.getenv("EVENT_WORKER_POLL_SECONDS", "3.0"))
WORKER_STALE_LOCK_SECONDS = int(os.getenv("EVENT_WORKER_STALE_LOCK_SECONDS", "600"))
OUTBOX_MAX_ATTEMPTS = int(os.getenv("EVENT_OUTBOX_MAX_ATTEMPTS", "8"))
EVENT_INGEST_MODE = os.getenv("EVENT_INGEST_MODE", "db").strip().lower()
USAGE_RESERVATION_MODE = os.getenv("USAGE_RESERVATION_MODE", "request").strip().lower()
REDIS_STREAM_KEY = os.getenv("EVENT_REDIS_STREAM_KEY", "capi:events")
REDIS_STREAM_GROUP = os.getenv("EVENT_REDIS_STREAM_GROUP", "event-outbox")
REDIS_STREAM_CONSUMER_ID = os.getenv("EVENT_REDIS_STREAM_CONSUMER_ID") or WORKER_ID
REDIS_STREAM_BLOCK_MS = int(os.getenv("EVENT_REDIS_STREAM_BLOCK_MS", "500"))
REDIS_STREAM_BATCH_SIZE = int(os.getenv("EVENT_REDIS_STREAM_BATCH_SIZE", str(WORKER_BATCH_SIZE)))
REDIS_STREAM_MAXLEN = int(os.getenv("EVENT_REDIS_STREAM_MAXLEN", "200000"))
RETRY_DELAYS = [30, 120, 600, 1800, 3600, 7200, 14400, 28800]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_attempt_after(attempts: int) -> datetime:
    delay = RETRY_DELAYS[min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)]
    return _now() + timedelta(seconds=delay)


def _event_names(events: list[EventData]) -> str:
    return ", ".join(sorted({event.event_name for event in events}))


def _get_stream_redis():
    return get_redis()


def _event_order_id(event: EventData) -> str:
    if event.custom_data and getattr(event.custom_data, "order_id", None):
        return str(event.custom_data.order_id)
    if event.event_id:
        return event.event_id
    return f"auto-{event.event_time}-{uuid.uuid4()}"


def _enrich_event(event: EventData, context: dict) -> EventData:
    custom_extra = getattr(getattr(event, "custom_data", None), "model_extra", None) or {}
    if custom_extra.get("_enriched") or getattr(getattr(event, "custom_data", None), "_enriched", False):
        return event

    if not event.user_data:
        event.user_data = UserData()

    ip_address = context.get("ip_address")
    user_agent = context.get("user_agent")
    cookies = context.get("cookies") or {}
    if ip_address and not event.user_data.client_ip_address:
        event.user_data.client_ip_address = ip_address
    if user_agent and not event.user_data.client_user_agent:
        event.user_data.client_user_agent = user_agent

    if event.user_data.client_ip_address:
        loc_data = get_location_data(event.user_data.client_ip_address)
        if loc_data:
            if loc_data.get("ct") and not event.user_data.ct:
                event.user_data.ct = [_clean_and_hash(loc_data["ct"], "ct")]
            if loc_data.get("st") and not event.user_data.st:
                event.user_data.st = [_clean_and_hash(loc_data["st"], "st")]
            if loc_data.get("country") and not event.user_data.country:
                event.user_data.country = [_clean_and_hash(loc_data["country"], "country")]
            if loc_data.get("zp") and not event.user_data.zp:
                event.user_data.zp = [_clean_and_hash(loc_data["zp"], "zp")]

    return boost_event_quality(
        event,
        cookies=cookies,
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def _enqueue_events_redis_stream(
    client_id: int,
    events_data: list[dict],
    request_context: dict,
    usage_reserved: dict[str, int],
) -> str | None:
    r = _get_stream_redis()
    if r is None:
        return None

    try:
        return await r.xadd(
            REDIS_STREAM_KEY,
            {
                "client_id": str(client_id),
                "events_data": json.dumps(events_data, separators=(",", ":"), default=str),
                "request_context": json.dumps(request_context or {}, separators=(",", ":"), default=str),
                "usage_reserved": json.dumps(usage_reserved or {}, separators=(",", ":"), default=str),
                "queued_at": _now().isoformat(),
            },
            maxlen=REDIS_STREAM_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        logger.warning(f"Redis Stream enqueue failed; falling back to DB outbox: {exc}")
        record_redis_fallback("stream_enqueue")
        return None


async def _ensure_stream_group(r) -> None:
    try:
        await r.xgroup_create(REDIS_STREAM_KEY, REDIS_STREAM_GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _stream_messages(raw_response) -> list[tuple[str, dict]]:
    messages: list[tuple[str, dict]] = []
    for _stream_name, stream_messages in raw_response or []:
        for message_id, fields in stream_messages:
            messages.append((message_id, fields))
    return messages


async def _read_stream_messages(r) -> list[tuple[str, dict]]:
    await _ensure_stream_group(r)
    pending = await r.xreadgroup(
        REDIS_STREAM_GROUP,
        REDIS_STREAM_CONSUMER_ID,
        {REDIS_STREAM_KEY: "0"},
        count=REDIS_STREAM_BATCH_SIZE,
        block=1,
    )
    messages = _stream_messages(pending)
    if messages:
        return messages

    new_messages = await r.xreadgroup(
        REDIS_STREAM_GROUP,
        REDIS_STREAM_CONSUMER_ID,
        {REDIS_STREAM_KEY: ">"},
        count=REDIS_STREAM_BATCH_SIZE,
        block=REDIS_STREAM_BLOCK_MS,
    )
    return _stream_messages(new_messages)


async def bridge_redis_stream_once() -> int:
    r = _get_stream_redis()
    if r is None:
        return 0

    messages = await _read_stream_messages(r)
    if not messages:
        return 0

    ack_ids: list[str] = []
    bridged_count = 0
    for message_id, fields in messages:
        try:
            client_id = int(fields["client_id"])
            events_payload = json.loads(fields["events_data"])
            request_context = json.loads(fields.get("request_context") or "{}")
            usage_reserved = json.loads(fields.get("usage_reserved") or "{}")
            hold_purchase = bool(request_context.get("hold_purchase")) and not bool(
                request_context.get("force_send")
            )
            async with AsyncSessionLocal() as db:
                queue_payload: list[dict] = []
                purchase_events = []
                purchase_order_ids = []
                for raw_event in events_payload:
                    event = EventData(**raw_event)
                    if hold_purchase and event.event_name == "Purchase":
                        order_id = _event_order_id(event)
                        purchase_events.append((order_id, raw_event))
                        purchase_order_ids.append(order_id)
                    else:
                        queue_payload.append(raw_event)

                existing_order_ids = set()
                if purchase_order_ids:
                    result = await db.execute(
                        select(PendingEvent.order_id)
                        .where(
                            and_(
                                PendingEvent.client_id == client_id,
                                PendingEvent.order_id.in_(purchase_order_ids)
                            )
                        )
                    )
                    existing_order_ids = set(result.scalars().all())

                for order_id, raw_event in purchase_events:
                    if order_id in existing_order_ids:
                        logger.warning(
                            f"Duplicate pending purchase skipped while bridging stream event {message_id}"
                        )
                    else:
                        db.add(
                            PendingEvent(
                                client_id=client_id,
                                order_id=order_id,
                                event_data=raw_event,
                                status="pending",
                            )
                        )
                        existing_order_ids.add(order_id)

                if queue_payload:
                    db.add(
                        EventOutbox(
                            client_id=client_id,
                            event_payload=queue_payload,
                            request_context=request_context,
                            usage_reserved=usage_reserved,
                            status="queued",
                            max_attempts=OUTBOX_MAX_ATTEMPTS,
                            next_attempt_at=_now(),
                        )
                    )
                await db.commit()
            bridged_count += 1
            ack_ids.append(message_id)
        except Exception as exc:
            logger.warning(f"Redis Stream event {message_id} bridge failed: {exc}")
            record_redis_fallback("stream_bridge")

    if ack_ids:
        await r.xack(REDIS_STREAM_KEY, REDIS_STREAM_GROUP, *ack_ids)
    return bridged_count


async def bridge_redis_stream_forever() -> None:
    if EVENT_INGEST_MODE != "redis_stream":
        logger.info("Redis Stream ingest bridge disabled (EVENT_INGEST_MODE != redis_stream).")
        return

    logger.info(
        f"Redis Stream ingest bridge started: stream={REDIS_STREAM_KEY}, "
        f"group={REDIS_STREAM_GROUP}, consumer={REDIS_STREAM_CONSUMER_ID}"
    )
    while True:
        try:
            bridged = await bridge_redis_stream_once()
            if bridged:
                logger.debug(f"Bridged {bridged} Redis Stream event batch(es) to DB outbox")
        except Exception as exc:
            logger.error(f"Redis Stream ingest bridge error: {exc}")
            await asyncio.sleep(WORKER_POLL_SECONDS)




# Secondary logging and delivery methods relocated to delivery_service.py


async def enqueue_events(
    db,
    client_id: int,
    events_data: list[dict],
    request_context: dict,
    usage_reserved: dict[str, int],
) -> EventOutbox | None:
    if EVENT_INGEST_MODE == "redis_stream":
        stream_id = await _enqueue_events_redis_stream(
            client_id,
            events_data,
            request_context,
            usage_reserved,
        )
        if stream_id:
            return None

    outbox = EventOutbox(
        client_id=client_id,
        event_payload=events_data,
        request_context=request_context,
        usage_reserved=usage_reserved,
        status="queued",
        max_attempts=OUTBOX_MAX_ATTEMPTS,
        next_attempt_at=_now(),
    )
    db.add(outbox)
    await db.flush()
    return outbox


async def claim_due_events(db, limit: int = WORKER_BATCH_SIZE) -> list[EventOutbox]:
    now = _now()
    stale_before = now - timedelta(seconds=WORKER_STALE_LOCK_SECONDS)
    result = await db.execute(
        select(EventOutbox)
        .where(
            and_(
                EventOutbox.status.in_(["queued", "processing"]),
                EventOutbox.attempts < EventOutbox.max_attempts,
                EventOutbox.next_attempt_at <= now,
                or_(
                    EventOutbox.status == "queued",
                    EventOutbox.locked_at.is_(None),
                    EventOutbox.locked_at <= stale_before,
                ),
            )
        )
        .order_by(EventOutbox.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = result.scalars().all()
    for row in rows:
        row.status = "processing"
        row.locked_at = now
        row.locked_by = WORKER_ID
    if rows:
        await db.commit()
    else:
        await db.rollback()
    return rows


async def _mark_dead(db, row: EventOutbox, client, error_message: str) -> None:
    """Mark an outbox row dead without committing the caller's transaction."""
    row.status = "dead"
    row.last_error = error_message[:500]
    row.locked_at = None
    row.locked_by = None
    if row.usage_reserved:
        try:
            # Using savepoint (nested transaction) ensures that a failure in usage rollback
            # does not abort marking the outbox item as dead or saving its failure EventLog.
            async with db.begin_nested():
                await rollback_usage_reservation(db, client, row.usage_reserved)
        except Exception as usage_error:
            logger.warning(f"[{client.name}] Outbox usage rollback failed: {usage_error}")

    # Trigger DLQ Webhook alert if configured
    if client.webhook_url:
        try:
            from app.services.webhook_service import send_webhook
            payloads = row.event_payload or []
            if isinstance(payloads, dict):
                payloads = [payloads]
            event_names = ", ".join(list(set(str(evt.get("event_name", "Unknown")) for evt in payloads if isinstance(evt, dict))))
            event_ids = ", ".join(list(set(str(evt.get("event_id", "Unknown")) for evt in payloads if isinstance(evt, dict))))

            alert_data = {
                "client_id": client.id,
                "client_name": client.name,
                "event_ids": event_ids,
                "event_names": event_names,
                "error_message": error_message,
                "status": "dead",
            }
            asyncio.create_task(send_webhook(client.webhook_url, "dlq_alert", alert_data))
        except Exception as alert_err:
            logger.warning(f"[{client.name}] Failed to dispatch DLQ webhook alert: {alert_err}")


async def process_outbox_row(row_id: int) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(EventOutbox, row_id)
        if not row or row.status != "processing":
            return

        client_result = await db.execute(select(Client).where(Client.id == row.client_id))
        client_row = client_result.scalar_one_or_none()
        if not client_row or not client_row.is_active:
            # Client inactive or missing â€” mark dead without usage rollback
            row.status = "dead"
            row.last_error = "Client inactive or missing"
            row.locked_at = None
            row.locked_by = None
            await db.commit()
            logger.warning(f"Outbox row {row.id} marked dead: client {row.client_id} inactive/missing")
            return

        client = _snapshot(client_row)
        event_payload = row.event_payload
        context = row.request_context or {}
        usage_reserved = row.usage_reserved
        attempts = row.attempts
        max_attempts = row.max_attempts

    events = []
    event_names = "Unknown"

    try:
        events = [_enrich_event(EventData(**event), context) for event in event_payload]
        event_names = _event_names(events)
        delivery_res = await deliver_events_to_platforms(client, events, context)
        primary_platform = delivery_res["primary_platform"]
        result = delivery_res["result"]
        await wait_for_secondary_tasks(delivery_res)

        async with AsyncSessionLocal() as db:
            row = await db.get(EventOutbox, row_id)
            if not row:
                return

            # If all events were filtered by routing rules or no platforms were enabled,
            # log as "filtered" instead of "success" and rollback usage reservation
            if primary_platform == "None":
                row.status = "sent"
                row.sent_at = _now()
                row.locked_at = None
                row.locked_by = None
                row.last_error = None

                events_data = [event.model_dump(exclude_none=True) for event in events]
                for event_data in events_data:
                    db.add(EventLog(**_event_log_kwargs(
                        client.id,
                        event_data,
                        "filtered",
                        context.get("ip_address"),
                        context.get("user_agent"),
                        context.get("device"),
                        fb_response=json.dumps(result) if result else None,
                    )))

                # Rollback usage since nothing was actually sent
                if usage_reserved:
                    try:
                        async with db.begin_nested():
                            await rollback_usage_reservation(db, client, usage_reserved)
                    except Exception as usage_err:
                        logger.warning(f"[{client.name}] Usage rollback failed for filtered events: {usage_err}")

                await db.commit()
                logger.debug(f"[{client.name}] Outbox row {row.id} filtered ({len(events)} events) â€” no platform enabled for these events.")
                return

            row.status = "sent"
            row.sent_at = _now()
            row.locked_at = None
            row.locked_by = None
            row.last_error = None

            events_data = [event.model_dump(exclude_none=True) for event in events]
            if USAGE_RESERVATION_MODE == "worker" and not usage_reserved:
                await increment_usage_counters_db(db, client, len(events))
            for event_data in events_data:
                db.add(EventLog(**_event_log_kwargs(
                    client.id,
                    event_data,
                    "success",
                    context.get("ip_address"),
                    context.get("user_agent"),
                    context.get("device"),
                    fb_response=json.dumps(result) if result else None,
                )))
            await db.commit()

            logger.debug(f"[{client.name}] Outbox row {row.id} sent ({len(events)} events) via {primary_platform}.")

    except Exception as exc:
        async with AsyncSessionLocal() as db:
            row = await db.get(EventOutbox, row_id)
            if not row:
                return

            new_attempts = attempts + 1
            row.attempts = new_attempts
            row.last_error = str(exc)[:500]
            row.locked_at = None
            row.locked_by = None

            if new_attempts >= max_attempts:
                db.add(EventLog(
                    client_id=client.id,
                    event_name=event_names,
                    event_count=len(events),
                    status="failed",
                    error_message=row.last_error,
                    ip_address=context.get("ip_address"),
                ))
                await _mark_dead(db, row, client, row.last_error or "Outbox send failed")
                await db.commit()
                logger.error(f"[{client.name}] Outbox row {row.id} dead after {new_attempts} attempts.")
                return

            row.status = "queued"
            row.next_attempt_at = _next_attempt_after(new_attempts)
            await db.commit()
            logger.warning(
                f"[{client.name}] Outbox row {row.id} attempt {new_attempts} failed; "
                f"next retry at {row.next_attempt_at}: {str(exc)[:120]}"
            )


async def process_event_outbox_forever() -> None:
    logger.info(f"Event outbox worker started: {WORKER_ID}")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                rows = await claim_due_events(db)
            if rows:
                results = await asyncio.gather(
                    *(process_outbox_row(row.id) for row in rows),
                    return_exceptions=True,
                )
                for res in results:
                    if isinstance(res, Exception):
                        logger.error(f"Exception occurred in process_outbox_row: {res}", exc_info=res)
            else:
                await asyncio.sleep(WORKER_POLL_SECONDS)
        except Exception as exc:
            logger.error(f"Event outbox worker error: {exc}")
            await asyncio.sleep(WORKER_POLL_SECONDS)


if __name__ == "__main__":
    from app.services.cleanup_service import auto_cleanup_database
    from app.services.expiry_service import expire_old_pending_events
    from app.services.retry_service import retry_failed_events
    from app.services.courier_status_worker import poll_courier_statuses_forever
    from app.services.courier_booking_service import process_courier_booking_jobs_forever

    async def main() -> None:
        await asyncio.gather(
            bridge_redis_stream_forever(),
            process_event_outbox_forever(),
            retry_failed_events(),
            auto_cleanup_database(),
            expire_old_pending_events(),
            poll_courier_statuses_forever(),
            process_courier_booking_jobs_forever(),
        )

    asyncio.run(main())
