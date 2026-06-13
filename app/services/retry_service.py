"""
Retry Service — ব্যর্থ ইভেন্ট পুনরায় Facebook-এ পাঠানোর সার্ভিস।
Background task হিসেবে চলে, exponential backoff সহ।
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.dependencies import _snapshot
from app.models.client import Client
from app.models.event_log import EventLog
from app.models.failed_event import FailedEvent
from app.schemas.event import EventData
from app.services.delivery_service import deliver_events_to_platforms, wait_for_secondary_tasks

logger = logging.getLogger(__name__)

# Retry intervals (seconds): 30s, 2min, 10min, 30min, 1hr
RETRY_DELAYS = [30, 120, 600, 1800, 3600]
# If a row stays in "retrying" for longer than this, reclaim it (crash recovery)
STALE_RETRYING_SECONDS = 600  # 10 minutes



def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _retry_is_due(failed: FailedEvent, now: datetime) -> bool:
    delay_index = min(failed.retry_count, len(RETRY_DELAYS) - 1)
    if not failed.last_retry_at:
        return True
    elapsed = (now - _as_utc(failed.last_retry_at)).total_seconds()
    return elapsed >= RETRY_DELAYS[delay_index]


async def claim_due_failed_events(db: AsyncSession, limit: int = 20) -> list[FailedEvent]:
    """
    Retry করার আগে row lock করে claim করা হয়।
    একাধিক worker থাকলেও একই failed event একসাথে পাঠানো হবে না।
    SQL-এ due-time ফিল্টার করা হয় যেন LIMIT শুধু due ইভেন্টে প্রযোজ্য হয়।
    Stale "retrying" rows (crash recovery) also get reclaimed after STALE_RETRYING_SECONDS.
    """
    from sqlalchemy import or_

    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=STALE_RETRYING_SECONDS)
    # SQL-level: শুধুমাত্র due হতে পারে এমন row fetch করো
    # (last_retry_at NULL = pending, অথবা minimum 30s পার হয়ে গেছে)
    # Stale "retrying" rows older than STALE_RETRYING_SECONDS are also reclaimed
    min_delay = RETRY_DELAYS[0]  # 30 seconds
    result = await db.execute(
        select(FailedEvent)
        .where(
            and_(
                FailedEvent.retry_count < FailedEvent.max_retries,
                or_(
                    # Pending rows that are due for retry
                    and_(
                        FailedEvent.status == "pending",
                        or_(
                            FailedEvent.last_retry_at.is_(None),
                            FailedEvent.last_retry_at <= now - timedelta(seconds=min_delay),
                        ),
                    ),
                    # Retryable rows that are due by backoff, plus stale claimed rows
                    # that may have crashed before the retry completed.
                    and_(
                        FailedEvent.status == "retrying",
                        FailedEvent.last_retry_at <= stale_before,
                    ),
                ),
            )
        )
        .order_by(FailedEvent.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    failed_events = result.scalars().all()

    # Python-level: exact RETRY_DELAYS দিয়ে precise check
    due_events: list[FailedEvent] = []
    for failed in failed_events:
        is_stale_retrying = (
            failed.status == "retrying"
            and failed.last_retry_at is not None
            and _as_utc(failed.last_retry_at) <= stale_before
        )
        if not is_stale_retrying and not _retry_is_due(failed, now):
            continue
        failed.status = "retrying"
        failed.last_retry_at = now
        due_events.append(failed)

    if due_events:
        await db.commit()
    else:
        await db.rollback()

    return due_events


async def retry_failed_events():
    """
    Background task — pending ব্যর্থ ইভেন্ট retry করে।
    প্রতি ৬০ সেকেন্ডে চলে।
    """
    while True:
        try:
            async with AsyncSessionLocal() as db:
                failed_events = await claim_due_failed_events(db)

                for failed in failed_events:
                    # Client তথ্য আনো
                    client_result = await db.execute(
                        select(Client).where(Client.id == failed.client_id)
                    )
                    client_row = client_result.scalar_one_or_none()
                    if not client_row or not client_row.is_active:
                        # Client inactive — dead letter queue-তে পাঠাও
                        failed.status = "dead"
                        await db.commit()
                        continue

                    # ORM object থেকে session-independent snapshot তৈরি করো
                    # DetachedInstanceError প্রতিরোধ করতে — event_worker.py-এর সাথে consistent
                    client = _snapshot(client_row)

                    try:
                        # ইভেন্ট ডাটা থেকে EventData অবজেক্ট তৈরি করো
                        events = [EventData(**e) for e in failed.event_payload]

                        # Prepare request context from the first event's user data since retry rows don't have it saved
                        first_user_data = events[0].user_data if (events and events[0].user_data) else None
                        cookies = {}
                        if first_user_data:
                            if first_user_data.fbp: cookies["_fbp"] = first_user_data.fbp
                            if first_user_data.fbc: cookies["_fbc"] = first_user_data.fbc
                            if first_user_data.ttp: cookies["_ttp"] = first_user_data.ttp
                        context = {
                            "cookies": cookies,
                            "ip_address": first_user_data.client_ip_address if first_user_data else None,
                            "user_agent": first_user_data.client_user_agent if first_user_data else "",
                        }

                        delivery_res = await deliver_events_to_platforms(client, events, context)
                        primary_platform = delivery_res["primary_platform"]
                        result = delivery_res["result"]
                        await wait_for_secondary_tasks(delivery_res)

                        # সফল! স্ট্যাটাস আপডেট করো
                        failed.status = "success"
                        failed.last_retry_at = datetime.now(timezone.utc)
                        event_names = ", ".join(sorted({event.event_name for event in events}))
                        db.add(EventLog(
                            client_id=client.id,
                            event_name=event_names,
                            event_count=len(events),
                            status="success",
                            fb_response=json.dumps(result) if result else None,
                        ))
                        await db.commit()

                        logger.info(
                            f"[{client.name}] Retry #{failed.retry_count + 1} সফল! "
                            f"{len(events)} ইভেন্ট পাঠানো হয়েছে।"
                        )

                    except Exception as e:
                        failed.retry_count += 1
                        failed.last_retry_at = datetime.now(timezone.utc)
                        is_dead = failed.retry_count >= failed.max_retries
                        failed.status = "retrying" if not is_dead else "dead"
                        failed.error_message = str(e)[:500]
                        await db.commit()

                        logger.warning(
                            f"[{client.name}] Retry #{failed.retry_count} ব্যর্থ: {str(e)[:100]}"
                        )

                        if is_dead and client.webhook_url:
                            try:
                                from app.services.webhook_service import send_webhook
                                payloads = failed.event_payload or []
                                if isinstance(payloads, dict):
                                    payloads = [payloads]
                                event_names = ", ".join(list(set(str(evt.get("event_name", "Unknown")) for evt in payloads if isinstance(evt, dict))))
                                event_ids = ", ".join(list(set(str(evt.get("event_id", "Unknown")) for evt in payloads if isinstance(evt, dict))))

                                alert_data = {
                                    "client_id": client.id,
                                    "client_name": client.name,
                                    "event_ids": event_ids,
                                    "event_names": event_names,
                                    "error_message": failed.error_message,
                                    "status": "dead",
                                }
                                asyncio.create_task(send_webhook(client.webhook_url, "dlq_alert", alert_data))
                            except Exception as alert_err:
                                logger.warning(f"[{client.name}] Failed to dispatch DLQ webhook alert from retry: {alert_err}")

        except Exception as e:
            logger.error(f"Retry service error: {e}")

        # ৬০ সেকেন্ড অপেক্ষা করো
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(retry_failed_events())
