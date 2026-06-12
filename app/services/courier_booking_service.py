"""Durable courier booking queue processed outside API request transactions."""

import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.database import AsyncSessionLocal
from app.models.client import Client
from app.models.courier_booking_job import CourierBookingJob
from app.models.courier_order import CourierOrder
from app.models.pending_event import PendingEvent
from app.security import decrypt_token
from app.services.courier_service import CourierService

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:courier-booking"
WORKER_BATCH_SIZE = int(os.getenv("COURIER_BOOKING_WORKER_BATCH_SIZE", "10"))
WORKER_POLL_SECONDS = float(os.getenv("COURIER_BOOKING_WORKER_POLL_SECONDS", "1.0"))
WORKER_STALE_LOCK_SECONDS = int(os.getenv("COURIER_BOOKING_STALE_LOCK_SECONDS", "600"))
MAX_ATTEMPTS = int(os.getenv("COURIER_BOOKING_MAX_ATTEMPTS", "8"))
RETRY_DELAYS = [30, 120, 600, 1800, 3600, 7200, 14400, 28800]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_attempt_after(attempts: int) -> datetime:
    delay = RETRY_DELAYS[min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)]
    return _now() + timedelta(seconds=delay)


def _append_history(order: CourierOrder, status: str, **extra) -> None:
    history = list(order.status_history or [])
    history.append({"status": status, "time": _now().isoformat(), **extra})
    order.status_history = history[-100:]


def _product_description(pending: PendingEvent) -> str | None:
    custom_data = (pending.event_data or {}).get("custom_data") or {}
    contents = custom_data.get("contents") or []
    if not contents and pending.raw_order_data:
        contents = pending.raw_order_data.get("products") or pending.raw_order_data.get("line_items") or []
    descriptions = []
    for item in contents if isinstance(contents, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("title") or item.get("name") or item.get("content_name")
        if name:
            descriptions.append(f"{name} x{item.get('quantity') or item.get('qty') or 1}")
    return ", ".join(descriptions) or None


def _normalized_payload(pending: PendingEvent, overrides: dict | None = None) -> dict:
    raw = dict(pending.raw_order_data or {})
    raw.update({key: value for key, value in (overrides or {}).items() if value is not None})
    payload = {
        "recipient_name": str(raw.get("recipient_name") or "").strip(),
        "recipient_phone": str(raw.get("recipient_phone") or "").strip(),
        "recipient_address": str(raw.get("recipient_address") or "").strip(),
        "cod_amount": float(raw.get("cod_amount") or 0),
        "store_id": str(raw.get("store_id") or "").strip() or None,
        "delivery_area_id": str(raw.get("delivery_area_id") or "").strip() or None,
        "delivery_area_name": str(raw.get("delivery_area_name") or "").strip() or None,
        "pickup_store_id": str(raw.get("pickup_store_id") or "").strip() or None,
        "recipient_city": int(raw["recipient_city"]) if raw.get("recipient_city") else None,
        "recipient_zone": int(raw["recipient_zone"]) if raw.get("recipient_zone") else None,
        "recipient_area": int(raw["recipient_area"]) if raw.get("recipient_area") else None,
        "item_weight": float(raw.get("item_weight") or 0.5),
        "item_quantity": int(raw.get("item_quantity") or 1),
        "item_description": raw.get("item_description") or _product_description(pending),
    }
    missing = [
        key
        for key in ("recipient_name", "recipient_phone", "recipient_address")
        if not payload[key]
    ]
    if missing:
        raise ValueError("Courier booking data missing: " + ", ".join(missing))
    return payload


def _validate_provider_config(client: Client, provider: str, payload: dict) -> None:
    if provider == "steadfast":
        if not client.steadfast_api_key or not client.steadfast_secret_key:
            raise ValueError("SteadFast credentials are missing.")
    elif provider == "pathao":
        if not client.pathao_api_key or not client.pathao_secret_key:
            raise ValueError("Pathao credentials are missing.")
        try:
            pathao_client_id, email = client.pathao_api_key.split("|", 1)
            pathao_client_secret, password = decrypt_token(client.pathao_secret_key).split("|", 1)
            if not all(value.strip() for value in (pathao_client_id, email, pathao_client_secret, password)):
                raise ValueError
        except Exception as exc:
            raise ValueError("Pathao credentials format is invalid.") from exc
        payload["store_id"] = payload.get("store_id") or client.pathao_store_id
        if not payload["store_id"]:
            raise ValueError("Pathao Store ID is missing.")
    elif provider == "redx":
        if not client.redx_access_token:
            raise ValueError("RedX access token is missing.")
        payload["delivery_area_id"] = payload.get("delivery_area_id") or client.redx_delivery_area_id
        payload["delivery_area_name"] = payload.get("delivery_area_name") or client.redx_delivery_area_name
        payload["pickup_store_id"] = payload.get("pickup_store_id") or client.redx_pickup_store_id
        if not payload["delivery_area_id"] or not payload["delivery_area_name"]:
            raise ValueError("RedX delivery area ID and name are missing.")
    else:
        raise ValueError(f"Unsupported courier provider: {provider}")


async def enqueue_courier_booking(
    db,
    *,
    client: Client,
    pending: PendingEvent,
    provider: str,
    overrides: dict | None = None,
    purchase_event_sent: bool = False,
) -> dict:
    """Persist a booking intent and placeholder courier order in the caller transaction."""
    provider = str(provider or "").strip().lower()
    payload = _normalized_payload(pending, overrides)
    _validate_provider_config(client, provider, payload)

    existing_result = await db.execute(
        select(CourierOrder).where(
            and_(
                CourierOrder.client_id == client.id,
                CourierOrder.order_id == pending.order_id,
            )
        ).with_for_update()
    )
    courier_order = existing_result.scalar_one_or_none()
    if courier_order and courier_order.courier_status != "booking_failed":
        return {
            "mode": "already_booked",
            "message": "Courier booking is already queued or completed.",
            "courier_order": courier_order,
        }

    if courier_order is None:
        courier_order = CourierOrder(
            client_id=client.id,
            pending_event_id=pending.id,
            order_id=pending.order_id,
            courier_provider=provider,
            courier_status="booking_queued",
            recipient_name=payload["recipient_name"],
            recipient_phone=payload["recipient_phone"],
            recipient_address=payload["recipient_address"],
            cod_amount=payload["cod_amount"],
            purchase_event_sent=purchase_event_sent,
            status_history=[],
        )
        db.add(courier_order)
        await db.flush()
    else:
        courier_order.courier_provider = provider
        courier_order.courier_status = "booking_queued"
        courier_order.recipient_name = payload["recipient_name"]
        courier_order.recipient_phone = payload["recipient_phone"]
        courier_order.recipient_address = payload["recipient_address"]
        courier_order.cod_amount = payload["cod_amount"]
        courier_order.purchase_event_sent = purchase_event_sent

    _append_history(courier_order, "booking_queued")
    job_result = await db.execute(
        select(CourierBookingJob)
        .where(CourierBookingJob.courier_order_id == courier_order.id)
        .with_for_update()
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        job = CourierBookingJob(
            client_id=client.id,
            pending_event_id=pending.id,
            courier_order_id=courier_order.id,
            provider=provider,
            request_payload=payload,
            status="queued",
            max_attempts=MAX_ATTEMPTS,
            next_attempt_at=_now(),
        )
    else:
        job.provider = provider
        job.request_payload = payload
        job.status = "queued"
        job.attempts = 0
        job.max_attempts = MAX_ATTEMPTS
        job.next_attempt_at = _now()
        job.last_error = None
        job.locked_at = None
        job.locked_by = None
    db.add(job)
    db.add(courier_order)

    pending.status = "courier_booking_queued"
    pending.portal_state = "processing"
    pending.is_confirmed = True
    db.add(pending)
    return {
        "mode": "queued",
        "message": "Courier booking queued. Purchase event will fire after delivery.",
        "courier_order": courier_order,
    }


async def claim_due_booking_jobs(db, limit: int = WORKER_BATCH_SIZE) -> list[int]:
    now = _now()
    stale_before = now - timedelta(seconds=WORKER_STALE_LOCK_SECONDS)
    result = await db.execute(
        select(CourierBookingJob)
        .where(
            and_(
                CourierBookingJob.attempts < CourierBookingJob.max_attempts,
                CourierBookingJob.next_attempt_at <= now,
                or_(
                    CourierBookingJob.status == "queued",
                    and_(
                        CourierBookingJob.status == "processing",
                        or_(
                            CourierBookingJob.locked_at.is_(None),
                            CourierBookingJob.locked_at <= stale_before,
                        ),
                    ),
                ),
            )
        )
        .order_by(CourierBookingJob.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = result.scalars().all()
    ids = []
    for job in jobs:
        job.status = "processing"
        job.locked_at = now
        job.locked_by = WORKER_ID
        ids.append(job.id)
        order = await db.get(CourierOrder, job.courier_order_id)
        if order and order.courier_status == "booking_queued":
            order.courier_status = "booking_processing"
    if ids:
        await db.commit()
    else:
        await db.rollback()
    return ids


async def cancel_queued_booking(db, courier_order: CourierOrder) -> bool:
    """Cancel an intent that has not been claimed by a provider worker yet."""
    if courier_order.courier_status != "booking_queued":
        return False
    result = await db.execute(
        select(CourierBookingJob).where(
            CourierBookingJob.courier_order_id == courier_order.id,
            CourierBookingJob.status == "queued",
        ).with_for_update()
    )
    job = result.scalar_one_or_none()
    if not job:
        return False
    job.status = "cancelled"
    job.locked_at = None
    job.locked_by = None
    courier_order.courier_status = "cancelled"
    _append_history(courier_order, "cancelled", raw_status="cancelled_before_provider_booking")
    if courier_order.pending_event_id:
        pending = await db.get(PendingEvent, courier_order.pending_event_id)
        if pending:
            pending.status = "cancelled"
            pending.portal_state = "cancelled"
    db.add(job)
    db.add(courier_order)
    return True


async def requeue_failed_booking_job(db, job_id: int) -> CourierBookingJob | None:
    """Reset a dead-letter booking after an operator explicitly requests a retry."""
    result = await db.execute(
        select(CourierBookingJob)
        .where(CourierBookingJob.id == job_id)
        .with_for_update()
    )
    job = result.scalar_one_or_none()
    if not job:
        return None
    if job.status != "dead":
        raise ValueError(f"Only dead courier booking jobs can be retried. Current status: {job.status}")

    order = await db.get(CourierOrder, job.courier_order_id)
    if not order:
        raise ValueError("Courier order is missing.")
    pending = await db.get(PendingEvent, job.pending_event_id) if job.pending_event_id else None

    job.status = "queued"
    job.attempts = 0
    job.max_attempts = MAX_ATTEMPTS
    job.next_attempt_at = _now()
    job.locked_at = None
    job.locked_by = None
    job.sent_at = None
    job.last_error = None

    order.courier_status = "booking_queued"
    _append_history(order, "booking_queued", raw_status="operator_retry")
    if pending:
        pending.status = "courier_booking_queued"
        pending.portal_state = "processing"
        pending.is_confirmed = True
    db.add(job)
    db.add(order)
    return job


async def _send_to_provider(client: Client, provider: str, payload: dict, order_id: str) -> dict:
    if provider == "steadfast":
        return await CourierService.send_to_steadfast(
            api_key=client.steadfast_api_key,
            secret_key=decrypt_token(client.steadfast_secret_key),
            recipient_name=payload["recipient_name"],
            recipient_phone=payload["recipient_phone"],
            recipient_address=payload["recipient_address"],
            cod_amount=payload["cod_amount"],
            merchant_order_id=order_id,
        )
    if provider == "pathao":
        pathao_client_id, email = client.pathao_api_key.split("|", 1)
        pathao_client_secret, password = decrypt_token(client.pathao_secret_key).split("|", 1)
        return await CourierService.send_to_pathao(
            client_id=pathao_client_id,
            client_secret=pathao_client_secret,
            email=email,
            password=password,
            store_id=payload["store_id"],
            recipient_name=payload["recipient_name"],
            recipient_phone=payload["recipient_phone"],
            recipient_address=payload["recipient_address"],
            cod_amount=payload["cod_amount"],
            merchant_order_id=order_id,
            item_quantity=payload["item_quantity"],
            item_weight=payload["item_weight"],
            item_description=payload.get("item_description"),
            recipient_city=payload.get("recipient_city"),
            recipient_zone=payload.get("recipient_zone"),
            recipient_area=payload.get("recipient_area"),
            base_url=CourierService.pathao_base_url(client.pathao_environment),
        )
    if provider == "redx":
        return await CourierService.send_to_redx(
            access_token=decrypt_token(client.redx_access_token),
            recipient_name=payload["recipient_name"],
            recipient_phone=payload["recipient_phone"],
            recipient_address=payload["recipient_address"],
            cod_amount=payload["cod_amount"],
            merchant_order_id=order_id,
            delivery_area_id=payload["delivery_area_id"],
            delivery_area_name=payload["delivery_area_name"],
            pickup_store_id=payload.get("pickup_store_id"),
            item_weight=payload["item_weight"],
        )
    raise ValueError(f"Unsupported courier provider: {provider}")


async def process_booking_job(job_id: int) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(CourierBookingJob, job_id)
        if not job or job.status != "processing":
            return
        client = await db.get(Client, job.client_id)
        order = await db.get(CourierOrder, job.courier_order_id)
        if not client or not order:
            job.status = "dead"
            job.last_error = "Client or courier order is missing"
            await db.commit()
            return
        provider = job.provider
        payload = dict(job.request_payload or {})
        order_id = order.order_id

    try:
        result = await _send_to_provider(client, provider, payload, order_id)
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Unknown courier provider error")
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            job = await db.get(CourierBookingJob, job_id)
            if not job or job.status != "processing":
                return
            order = await db.get(CourierOrder, job.courier_order_id)
            pending = await db.get(PendingEvent, job.pending_event_id) if job.pending_event_id else None
            job.attempts += 1
            job.last_error = str(exc)[:500]
            job.locked_at = None
            job.locked_by = None
            if job.attempts >= job.max_attempts:
                job.status = "dead"
                if order:
                    order.courier_status = "booking_failed"
                    _append_history(order, "booking_failed", error=job.last_error)
                if pending:
                    pending.status = "pending"
                    pending.portal_state = "pending"
                    pending.is_confirmed = False
            else:
                job.status = "queued"
                job.next_attempt_at = _next_attempt_after(job.attempts)
                if order:
                    order.courier_status = "booking_queued"
            await db.commit()
            logger.warning("Courier booking job %s failed attempt %s: %s", job_id, job.attempts, exc)
        return

    async with AsyncSessionLocal() as db:
        job = await db.get(CourierBookingJob, job_id)
        if not job or job.status != "processing":
            return
        order = await db.get(CourierOrder, job.courier_order_id)
        pending = await db.get(PendingEvent, job.pending_event_id) if job.pending_event_id else None
        if not order:
            job.status = "dead"
            job.last_error = "Courier order is missing after provider booking"
            await db.commit()
            return
        order.courier_order_id = result.get("courier_order_id")
        order.courier_tracking_id = result.get("tracking_id")
        order.courier_status = "pending"
        _append_history(order, "pending", raw_status="provider_booking_accepted")
        job.status = "sent"
        job.sent_at = _now()
        job.locked_at = None
        job.locked_by = None
        job.last_error = None
        if pending:
            pending.status = "courier_booked"
            pending.portal_state = "processing"
            pending.is_confirmed = True
        await db.commit()
        logger.info("Courier booking job %s completed for order %s", job_id, order.order_id)


async def process_courier_booking_jobs_forever() -> None:
    logger.info("Courier booking worker started: %s", WORKER_ID)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                job_ids = await claim_due_booking_jobs(db)
            if job_ids:
                await asyncio.gather(*(process_booking_job(job_id) for job_id in job_ids))
            else:
                await asyncio.sleep(WORKER_POLL_SECONDS)
        except Exception as exc:
            logger.error("Courier booking worker error: %s", exc)
            await asyncio.sleep(WORKER_POLL_SECONDS)
