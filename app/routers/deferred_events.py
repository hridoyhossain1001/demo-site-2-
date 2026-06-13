"""
Deferred Purchase Events Router
─────────────────────────────────
অর্ডার কনফার্ম/ক্যান্সেল হলে pending Purchase events ম্যানেজ করে।
কনফার্ম হলে event durable outbox queue-তে যায়; worker Facebook delivery করে।

Endpoints:
  POST /api/v1/events/confirm       — একটি অর্ডার কনফার্ম
  POST /api/v1/events/confirm/bulk  — একাধিক অর্ডার কনফার্ম
  POST /api/v1/events/cancel        — একটি অর্ডার ক্যান্সেল
  GET  /api/v1/events/pending       — pending events-এর লিস্ট
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select, update, and_, func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_client, CachedClient
from app.models.client import Client as ClientModel
from app.models.courier_order import CourierOrder
from app.models.pending_event import PendingEvent
from app.schemas.event import EventData
from app.services.courier_booking_service import cancel_queued_booking, enqueue_courier_booking
from app.services.event_quality import boost_event_quality
from app.services.event_worker import enqueue_events
from app.services.usage_service import check_and_reserve_usage
from app.services.plan_service import has_growth_access

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Request/Response Schemas ────────────────────────────────────────────────

class ConfirmRequest(BaseModel):
    order_id: str

    @field_validator("order_id")
    @classmethod
    def normalize_order_id(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("order_id is required")
        return value


class BulkConfirmRequest(BaseModel):
    order_ids: List[str]

    @field_validator("order_ids")
    @classmethod
    def normalize_order_ids(cls, values: List[str]) -> List[str]:
        normalized = []
        seen = set()
        for value in values or []:
            order_id = str(value or "").strip()
            if not order_id or order_id in seen:
                continue
            seen.add(order_id)
            normalized.append(order_id)
        if not normalized:
            raise ValueError("order_ids is required")
        return normalized


class CancelRequest(BaseModel):
    order_id: str

    @field_validator("order_id")
    @classmethod
    def normalize_order_id(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("order_id is required")
        return value


class BulkCancelRequest(BaseModel):
    order_ids: List[str]

    @field_validator("order_ids")
    @classmethod
    def normalize_order_ids(cls, values: List[str]) -> List[str]:
        normalized = []
        seen = set()
        for value in values or []:
            order_id = str(value or "").strip()
            if not order_id or order_id in seen:
                continue
            seen.add(order_id)
            normalized.append(order_id)
        if not normalized:
            raise ValueError("order_ids is required")
        return normalized


class PendingEventResponse(BaseModel):
    order_id: str
    event_name: str
    value: Optional[float] = None
    currency: Optional[str] = None
    status: str
    created_at: str
    age_hours: float
    customer: Optional[str] = None
    raw_order_data: Optional[dict] = None
    fraud_score: Optional[int] = None
    fraud_details: Optional[dict] = None


class ConfirmResponse(BaseModel):
    status: str
    order_id: str
    message: str


class BulkConfirmResponse(BaseModel):
    status: str
    confirmed: int
    failed: int
    details: list


class BulkCancelResponse(BaseModel):
    status: str
    cancelled: int
    failed: int
    details: list


class PendingListResponse(BaseModel):
    status: str
    total: int
    events: List[PendingEventResponse]


class DeferredSummaryResponse(BaseModel):
    status: str
    pending: int
    confirmed: int
    cancelled: int
    expired: int
    pending_value: float
    pending_oldest_age_hours: Optional[float] = None


def _safe_numeric_value(value) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _pending_event_value(event_data: dict | None) -> float:
    if not isinstance(event_data, dict):
        return 0.0
    custom_data = event_data.get("custom_data")
    if not isinstance(custom_data, dict):
        return 0.0
    return _safe_numeric_value(custom_data.get("value"))


# ─── Helper: Queue confirmed event for worker delivery ───────────────────────

async def _queue_confirmed_event(
    client: CachedClient,
    pending: PendingEvent,
    db: AsyncSession,
) -> dict:
    """
    pending_events থেকে event data নিয়ে durable outbox-এ queue করে।
    event_time আপডেট করে (current time) — Facebook ৭ দিনের মধ্যের event চায়।
    """
    event_dict = pending.event_data.copy()
    event_dict.pop("raw_order_data", None)

    # event_time আপডেট করো — current time
    event_dict["event_time"] = int(datetime.now(timezone.utc).timestamp())

    # EventData model-এ parse করো
    try:
        event = EventData(**event_dict)
    except Exception as e:
        logger.error(f"[{client.name}] Pending event parse error (order: {pending.order_id}): {e}")
        raise HTTPException(status_code=500, detail="Stored event data could not be parsed.")

    user_data = event_dict.get("user_data", {}) or {}
    boost_event_quality(
        event,
        ip_address=user_data.get("client_ip_address"),
        user_agent=user_data.get("client_user_agent") or "",
    )
    events_data = [event.model_dump(exclude_none=True)]
    reserved_keys = await check_and_reserve_usage(db, client, 1)
    await enqueue_events(
        db,
        client_id=client.id,
        events_data=events_data,
        request_context={
            "ip_address": user_data.get("client_ip_address"),
            "user_agent": user_data.get("client_user_agent") or "",
            "cookies": {},
        },
        usage_reserved=reserved_keys,
    )
    return event_dict


async def _auto_book_courier_for_pending(
    client_id: int,
    pending: PendingEvent,
    db: AsyncSession,
) -> dict:
    """
    Queue a confirmed COD order for the client's default courier.
    Returns mode:
      queued/already_booked = hold Purchase until courier delivery
      not_configured = use direct Purchase fallback
      failed = keep the order pending and show an error
    """
    client_res = await db.execute(select(ClientModel).where(ClientModel.id == client_id))
    client_obj = client_res.scalar_one_or_none()
    if not client_obj or not has_growth_access(client_obj) or not client_obj.courier_auto_send:
        return {"mode": "not_configured", "message": "Courier auto-booking is not configured."}
    if not client_obj.default_courier:
        return {"mode": "failed", "message": "Default courier provider is missing."}

    provider = str(client_obj.default_courier or "").strip().lower()
    try:
        return await enqueue_courier_booking(
            db,
            client=client_obj,
            pending=pending,
            provider=provider,
        )
    except ValueError as exc:
        return {"mode": "failed", "message": str(exc)}


# ─── POST /events/confirm — Single Order Confirm ─────────────────────────────

@router.post(
    "/events/confirm",
    response_model=ConfirmResponse,
    summary="Confirm a pending Purchase event",
)
async def confirm_event(
    payload: ConfirmRequest,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    একটি pending Purchase event কনফার্ম করে delivery queue-তে রাখে।
    Original user data (IP, UA, fbp, fbc) সহ পাঠানো হয়।
    """
    if not client.deferred_purchase:
        raise HTTPException(
            status_code=403,
            detail="Deferred purchase feature is not enabled for this client.",
        )

    # Lock before external booking so concurrent confirms cannot create duplicate consignments.
    result = await db.execute(
        select(PendingEvent).where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.order_id == payload.order_id,
            )
        ).with_for_update()
    )
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(
            status_code=404,
            detail=f"Pending event not found: {payload.order_id}",
        )

    # Resolve status-based misleading error codes
    if pending.portal_state == "operations_only":
        return ConfirmResponse(
            status="success",
            order_id=payload.order_id,
            message="Purchase event was already sent. This order is available for manual courier booking.",
        )
    if pending.status in {"courier_booking_queued", "courier_booked"}:
        return ConfirmResponse(
            status="success",
            order_id=payload.order_id,
            message="Courier booking is already queued or completed. Purchase event will fire after delivery.",
        )
    elif pending.status == "confirmed":
        return ConfirmResponse(
            status="success",
            order_id=payload.order_id,
            message="Purchase event was already confirmed.",
        )
    elif pending.status == "cancelled":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm order because it is already cancelled: {payload.order_id}",
        )
    elif pending.status == "expired":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm order because it is expired: {payload.order_id}",
        )
    elif pending.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm order due to invalid status: {pending.status}",
        )

    # The pending row is locked only while the durable booking intent is saved.
    booking = await _auto_book_courier_for_pending(client.id, pending, db)
    if booking["mode"] in {"queued", "already_booked"}:
        message = booking["message"]
        logger.info(f"[{client.name}] Order confirmed and queued for courier booking: {payload.order_id}")
    elif booking["mode"] == "not_configured":
        try:
            await _queue_confirmed_event(client, pending, db)
        except HTTPException:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            logger.error(f"[{client.name}] Confirm queue failed ({payload.order_id}): {e}")
            raise HTTPException(
                status_code=500,
                detail="Purchase event could not be queued.",
            )
        pending.status = "confirmed"
        pending.portal_state = "confirmed"
        pending.is_confirmed = True
        pending.confirmed_at = datetime.now(timezone.utc)
        message = "Purchase event delivery queue-তে রাখা হয়েছে."
        logger.info(f"[{client.name}] Order confirmed & queued directly: {payload.order_id}")
    else:
        await db.rollback()
        raise HTTPException(status_code=400, detail=booking["message"])

    await db.commit()

    return ConfirmResponse(
        status="success",
        order_id=payload.order_id,
        message=message,
    )


# ─── POST /events/confirm/bulk — Bulk Confirm ────────────────────────────────

async def _queue_bulk_confirm_events(
    payload: BulkConfirmRequest,
    client: CachedClient,
    db: AsyncSession,
) -> BulkConfirmResponse:
    result = await db.execute(
        select(PendingEvent).where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.order_id.in_(payload.order_ids),
                PendingEvent.status == "pending",
            )
        ).with_for_update()
    )
    pending_events = result.scalars().all()
    found_ids = {pending.order_id for pending in pending_events}
    confirmed = 0
    failed = 0
    details = []

    for pending in pending_events:
        order_id = pending.order_id
        try:
            async with db.begin_nested():
                if pending.portal_state == "operations_only":
                    confirmed += 1
                    details.append({"order_id": order_id, "status": "already_sent", "mode": "operations_only"})
                    continue

                booking = await _auto_book_courier_for_pending(client.id, pending, db)
                if booking["mode"] in {"queued", "already_booked"}:
                    confirmed += 1
                    details.append({"order_id": order_id, "status": "queued", "mode": "courier"})
                    continue
                if booking["mode"] != "not_configured":
                    raise HTTPException(status_code=400, detail=booking["message"])

                await _queue_confirmed_event(client, pending, db)
                pending.status = "confirmed"
                pending.portal_state = "confirmed"
                pending.is_confirmed = True
                pending.confirmed_at = datetime.now(timezone.utc)
                confirmed += 1
                details.append({"order_id": order_id, "status": "queued", "mode": "direct"})
        except HTTPException as exc:
            failed += 1
            details.append({"order_id": order_id, "status": "failed", "error": str(exc.detail)})
            logger.error("[%s] Bulk confirm queue rejected (%s): %s", client.name, order_id, exc.detail)
        except Exception as exc:
            failed += 1
            details.append({"order_id": order_id, "status": "failed", "error": f"Internal error: {exc}"})
            logger.exception("[%s] Bulk confirm queue failed (%s)", client.name, order_id)

    for order_id in payload.order_ids:
        if order_id not in found_ids:
            failed += 1
            details.append({"order_id": order_id, "status": "not_found"})

    await db.commit()
    logger.info("[%s] Bulk confirm queued: %s confirmed, %s failed", client.name, confirmed, failed)
    return BulkConfirmResponse(status="success", confirmed=confirmed, failed=failed, details=details)


@router.post(
    "/events/confirm/bulk",
    response_model=BulkConfirmResponse,
    summary="Confirm multiple pending Purchase events",
)
async def bulk_confirm_events(
    payload: BulkConfirmRequest,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    একাধিক pending Purchase event একসাথে কনফার্ম করে delivery queue-তে রাখে।
    """
    if not client.deferred_purchase:
        raise HTTPException(
            status_code=403,
            detail="Deferred purchase feature is not enabled for this client.",
        )

    if not payload.order_ids:
        raise HTTPException(status_code=400, detail="order_ids খালি!")

    if len(payload.order_ids) > 100:
        raise HTTPException(status_code=400, detail="একবারে সর্বোচ্চ ১০০টি অর্ডার কনফার্ম করা যায়।")

    return await _queue_bulk_confirm_events(payload, client, db)

# ─── POST /events/cancel — Cancel ────────────────────────────────────────────

@router.post(
    "/events/cancel",
    response_model=ConfirmResponse,
    summary="Cancel a pending Purchase event",
)
async def cancel_event(
    payload: CancelRequest,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Pending Purchase event ক্যান্সেল করে।
    Facebook-এ কোনো ডেটা পাঠানো হয় না।
    """
    if not client.deferred_purchase:
        raise HTTPException(
            status_code=403,
            detail="Deferred purchase feature is not enabled for this client.",
        )

    result = await db.execute(
        select(PendingEvent).where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.order_id == payload.order_id,
            )
        ).with_for_update()
    )
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(
            status_code=404,
            detail=f"Pending event not found: {payload.order_id}",
        )

    if pending.status == "cancelled":
        return ConfirmResponse(
            status="success",
            order_id=payload.order_id,
            message="❌ Purchase event already cancelled.",
        )
    elif pending.status == "confirmed":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order because it is already confirmed: {payload.order_id}",
        )
    elif pending.status == "courier_booking_queued":
        order_result = await db.execute(
            select(CourierOrder).where(
                and_(
                    CourierOrder.client_id == client.id,
                    CourierOrder.order_id == payload.order_id,
                )
            ).with_for_update()
        )
        courier_order = order_result.scalar_one_or_none()
        if not courier_order or not await cancel_queued_booking(db, courier_order):
            raise HTTPException(
                status_code=409,
                detail="Courier booking is being processed. Refresh shortly before cancelling.",
            )
        await db.commit()
        return ConfirmResponse(
            status="success",
            order_id=payload.order_id,
            message="Courier booking cancelled before provider dispatch.",
        )
    elif pending.status == "courier_booked":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order because it is already booked with courier: {payload.order_id}",
        )
    elif pending.status == "expired":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order because it is expired: {payload.order_id}",
        )
    elif pending.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order due to invalid status: {pending.status}",
        )

    pending.status = "cancelled"
    await db.commit()

    logger.info(f"[{client.name}] ❌ Order cancelled: {payload.order_id}")

    return ConfirmResponse(
        status="success",
        order_id=payload.order_id,
        message="❌ Purchase event ক্যান্সেল হয়েছে। Facebook-এ কিছু পাঠানো হয়নি।",
    )


# ─── POST /events/cancel/bulk — Bulk Cancel ─────────────────────────────────
@router.post(
    "/events/cancel/restore",
    response_model=ConfirmResponse,
    summary="Restore a skipped pending Purchase event",
)
async def restore_cancelled_event(
    payload: CancelRequest,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Move a skipped Purchase event back to the pending verification queue."""
    if not client.deferred_purchase:
        raise HTTPException(
            status_code=403,
            detail="Deferred purchase feature is not enabled for this client.",
        )

    result = await db.execute(
        select(PendingEvent).where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.order_id == payload.order_id,
            )
        ).with_for_update()
    )
    pending = result.scalar_one_or_none()
    if not pending:
        raise HTTPException(status_code=404, detail=f"Order not found: {payload.order_id}")
    if pending.status != "cancelled":
        raise HTTPException(
            status_code=400,
            detail=f"Only skipped events can be restored. Current status: {pending.status}",
        )

    pending.status = "pending"
    await db.commit()

    logger.info("[%s] Restored skipped order to pending: %s", client.name, payload.order_id)
    return ConfirmResponse(
        status="success",
        order_id=payload.order_id,
        message="Purchase event restored to the verification queue.",
    )


@router.post(
    "/events/cancel/bulk",
    response_model=BulkCancelResponse,
    summary="Cancel multiple pending Purchase events",
)
async def bulk_cancel_events(
    payload: BulkCancelRequest,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Cancel multiple pending Purchase events without sending anything to ad platforms."""
    if not client.deferred_purchase:
        raise HTTPException(
            status_code=403,
            detail="Deferred purchase feature is not enabled for this client.",
        )

    order_ids = list(dict.fromkeys(payload.order_ids))
    if not order_ids:
        raise HTTPException(status_code=400, detail="order_ids খালি!")

    if len(order_ids) > 5000:
        raise HTTPException(status_code=400, detail="একবারে সর্বোচ্চ 5000টি অর্ডার cancel করা যাবে।")

    result = await db.execute(
        select(PendingEvent.order_id).where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.order_id.in_(order_ids),
                PendingEvent.status == "pending",
            )
        ).with_for_update()
    )
    found_ids = set(result.scalars().all())
    queued_result = await db.execute(
        select(PendingEvent).where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.order_id.in_(order_ids),
                PendingEvent.status == "courier_booking_queued",
            )
        ).with_for_update()
    )
    queued_cancelled_ids = set()
    for pending in queued_result.scalars().all():
        order_result = await db.execute(
            select(CourierOrder).where(
                and_(
                    CourierOrder.client_id == client.id,
                    CourierOrder.order_id == pending.order_id,
                )
            ).with_for_update()
        )
        courier_order = order_result.scalar_one_or_none()
        if courier_order and await cancel_queued_booking(db, courier_order):
            queued_cancelled_ids.add(pending.order_id)

    if found_ids:
        await db.execute(
            update(PendingEvent)
            .where(
                and_(
                    PendingEvent.client_id == client.id,
                    PendingEvent.order_id.in_(found_ids),
                    PendingEvent.status == "pending",
                )
            )
            .values(status="cancelled")
        )

    await db.commit()

    cancelled_ids = found_ids | queued_cancelled_ids
    details = [
        {"order_id": oid, "status": "cancelled" if oid in cancelled_ids else "not_found"}
        for oid in order_ids
    ]
    cancelled = len(cancelled_ids)
    failed = len(order_ids) - cancelled

    logger.info(f"[{client.name}] Bulk cancel completed: {cancelled} cancelled, {failed} failed")

    return BulkCancelResponse(
        status="success",
        cancelled=cancelled,
        failed=failed,
        details=details,
    )


@router.get(
    "/events/deferred/summary",
    response_model=DeferredSummaryResponse,
    summary="Deferred Purchase summary",
)
async def deferred_purchase_summary(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Compact COD/verified purchase status for dashboards and plugin widgets."""
    if not client.deferred_purchase:
        raise HTTPException(
            status_code=403,
            detail="Deferred purchase feature is not enabled for this client.",
        )
    status_result = await db.execute(
        select(PendingEvent.status, sql_func.count(PendingEvent.id))
        .where(PendingEvent.client_id == client.id)
        .group_by(PendingEvent.status)
    )
    counts = {status: int(count or 0) for status, count in status_result}

    pending_summary_stmt = select(
        PendingEvent.event_data,
        PendingEvent.created_at,
    ).where(
        and_(
            PendingEvent.client_id == client.id,
            PendingEvent.status == "pending"
        )
    )
    pending_summary_res = await db.execute(pending_summary_stmt)
    pending_rows = pending_summary_res.all()
    pending_value = sum(_pending_event_value(event_data) for event_data, _created_at in pending_rows)
    oldest_created = min((created_at for _event_data, created_at in pending_rows if created_at), default=None)

    oldest_age_hours = None
    if oldest_created:
        created = oldest_created.replace(tzinfo=timezone.utc) if oldest_created.tzinfo is None else oldest_created
        oldest_age_hours = round((datetime.now(timezone.utc) - created).total_seconds() / 3600, 1)

    return DeferredSummaryResponse(
        status="success",
        pending=counts.get("pending", 0),
        confirmed=counts.get("confirmed", 0),
        cancelled=counts.get("cancelled", 0),
        expired=counts.get("expired", 0),
        pending_value=round(pending_value, 2),
        pending_oldest_age_hours=oldest_age_hours,
    )


# ─── GET /events/pending — Pending List ──────────────────────────────────────

@router.get(
    "/events/pending",
    response_model=PendingListResponse,
    summary="List pending Purchase events",
)
async def list_pending_events(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Pending Purchase events-এর paginated list"""
    if not client.deferred_purchase:
        raise HTTPException(
            status_code=403,
            detail="Deferred purchase feature is not enabled for this client.",
        )
    offset = (page - 1) * limit

    # Total count
    count_r = await db.execute(
        select(sql_func.count(PendingEvent.id)).where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.status == "pending",
            )
        )
    )
    total = count_r.scalar() or 0

    # Paginated results
    result = await db.execute(
        select(PendingEvent)
        .where(
            and_(
                PendingEvent.client_id == client.id,
                PendingEvent.status == "pending",
            )
        )
        .order_by(PendingEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    events = result.scalars().all()

    now = datetime.now(timezone.utc)
    event_list = []
    for e in events:
        event_data = e.event_data or {}
        custom_data = event_data.get("custom_data", {})
        created = e.created_at.replace(tzinfo=timezone.utc) if e.created_at.tzinfo is None else e.created_at
        age_hours = round((now - created).total_seconds() / 3600, 1)

        # Try to find a human readable customer representation
        user_data = event_data.get("user_data", {}) or {}
        ph_list = user_data.get("ph") or []
        em_list = user_data.get("em") or []
        cust_val = "—"
        if ph_list:
            cust_val = ph_list[0] if isinstance(ph_list, list) else str(ph_list)
        elif em_list:
            cust_val = em_list[0] if isinstance(em_list, list) else str(em_list)

        event_list.append(PendingEventResponse(
            order_id=e.order_id,
            event_name=event_data.get("event_name", "Purchase"),
            value=custom_data.get("value"),
            currency=custom_data.get("currency"),
            status=e.status,
            created_at=e.created_at.isoformat() if e.created_at else "",
            age_hours=age_hours,
            customer=cust_val,
            raw_order_data=e.raw_order_data,
            fraud_score=e.fraud_score,
            fraud_details=e.fraud_details,
        ))

    return PendingListResponse(
        status="success",
        total=total,
        events=event_list,
    )
