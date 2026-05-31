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
import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select, update, and_, func as sql_func, cast, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_client, CachedClient
from app.models.client import Client as ClientModel
from app.models.courier_order import CourierOrder
from app.models.pending_event import PendingEvent
from app.schemas.event import EventData
from app.security import decrypt_token
from app.services.courier_service import CourierService
from app.services.event_quality import boost_event_quality
from app.services.event_worker import enqueue_events
from app.services.usage_service import check_and_reserve_usage

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
        raise HTTPException(status_code=500, detail=f"Event data parse error: {e}")

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
    Book a confirmed COD order with the client's default courier.
    Returns mode:
      booked/already_booked = hold Purchase until courier delivery
      not_configured = use direct Purchase fallback
      failed = keep the order pending and show an error
    """
    client_res = await db.execute(select(ClientModel).where(ClientModel.id == client_id))
    client_obj = client_res.scalar_one_or_none()
    if not client_obj or not client_obj.courier_auto_send:
        return {"mode": "not_configured", "message": "Courier auto-booking is not configured."}
    if not client_obj.default_courier:
        return {"mode": "failed", "message": "Default courier provider is missing."}

    raw = pending.raw_order_data or {}
    missing = [
        key
        for key in ("recipient_name", "recipient_phone", "recipient_address")
        if not str(raw.get(key) or "").strip()
    ]
    if missing:
        return {
            "mode": "failed",
            "message": "Courier booking data missing: " + ", ".join(missing),
        }

    existing_res = await db.execute(
        select(CourierOrder).where(
            and_(
                CourierOrder.client_id == client_id,
                CourierOrder.order_id == pending.order_id,
            )
        )
    )
    if existing_res.scalar_one_or_none():
        return {"mode": "already_booked", "message": "Order is already booked with courier."}

    provider = str(client_obj.default_courier or "").strip().lower()
    cod_amount = float(raw.get("cod_amount") or 0)

    try:
        if provider == "steadfast":
            if not (client_obj.steadfast_api_key and client_obj.steadfast_secret_key):
                return {"mode": "failed", "message": "SteadFast credentials are missing."}
            result = await CourierService.send_to_steadfast(
                api_key=client_obj.steadfast_api_key,
                secret_key=decrypt_token(client_obj.steadfast_secret_key),
                recipient_name=str(raw.get("recipient_name") or "").strip(),
                recipient_phone=str(raw.get("recipient_phone") or "").strip(),
                recipient_address=str(raw.get("recipient_address") or "").strip(),
                cod_amount=cod_amount,
                merchant_order_id=pending.order_id,
            )
        elif provider == "pathao":
            if not (client_obj.pathao_api_key and client_obj.pathao_secret_key and client_obj.pathao_store_id):
                return {"mode": "failed", "message": "Pathao credentials are missing."}
            try:
                pathao_client_id, email = client_obj.pathao_api_key.split("|", 1)
                pathao_client_secret, password = decrypt_token(client_obj.pathao_secret_key).split("|", 1)
            except ValueError:
                return {"mode": "failed", "message": "Pathao credentials format is invalid."}
            # Extract product names from event_data or raw_order_data
            item_description_to_use = None
            event_data_dict = pending.event_data or {}
            custom_data_dict = event_data_dict.get("custom_data") or {}
            contents_list = custom_data_dict.get("contents") or []

            if not contents_list and pending.raw_order_data:
                contents_list = pending.raw_order_data.get("products") or pending.raw_order_data.get("line_items") or []

            if contents_list and isinstance(contents_list, list):
                product_descriptions = []
                for item in contents_list:
                    if isinstance(item, dict):
                        name = item.get("title") or item.get("name") or item.get("content_name")
                        qty = item.get("quantity") or item.get("qty") or 1
                        if name:
                            product_descriptions.append(f"{name} x{qty}")
                if product_descriptions:
                    item_description_to_use = ", ".join(product_descriptions)

            result = await CourierService.send_to_pathao(
                client_id=pathao_client_id,
                client_secret=pathao_client_secret,
                email=email,
                password=password,
                store_id=client_obj.pathao_store_id,
                recipient_name=str(raw.get("recipient_name") or "").strip(),
                recipient_phone=str(raw.get("recipient_phone") or "").strip(),
                recipient_address=str(raw.get("recipient_address") or "").strip(),
                cod_amount=cod_amount,
                merchant_order_id=pending.order_id,
                item_description=item_description_to_use
            )
        elif provider == "redx":
            if not (
                client_obj.redx_access_token
                and client_obj.redx_delivery_area_id
                and client_obj.redx_delivery_area_name
            ):
                return {"mode": "failed", "message": "RedX credentials and default delivery area are missing."}
            result = await CourierService.send_to_redx(
                access_token=decrypt_token(client_obj.redx_access_token),
                recipient_name=str(raw.get("recipient_name") or "").strip(),
                recipient_phone=str(raw.get("recipient_phone") or "").strip(),
                recipient_address=str(raw.get("recipient_address") or "").strip(),
                cod_amount=cod_amount,
                merchant_order_id=pending.order_id,
                delivery_area_id=client_obj.redx_delivery_area_id,
                delivery_area_name=client_obj.redx_delivery_area_name,
                pickup_store_id=client_obj.redx_pickup_store_id,
            )
        else:
            return {"mode": "failed", "message": f"Unsupported courier provider: {provider}"}
    except Exception as exc:
        logger.error("Auto courier booking failed for %s: %s", pending.order_id, exc)
        return {"mode": "failed", "message": f"Courier booking failed: {exc}"}

    if not result.get("success"):
        return {
            "mode": "failed",
            "message": f"Courier booking failed: {result.get('error') or 'Unknown courier error'}",
        }

    courier_order = CourierOrder(
        client_id=client_id,
        pending_event_id=pending.id,
        order_id=pending.order_id,
        courier_provider=provider,
        courier_order_id=result.get("courier_order_id"),
        courier_tracking_id=result.get("tracking_id"),
        courier_status="pending",
        recipient_name=str(raw.get("recipient_name") or "").strip(),
        recipient_phone=str(raw.get("recipient_phone") or "").strip(),
        recipient_address=str(raw.get("recipient_address") or "").strip(),
        cod_amount=cod_amount,
        status_history=[{"status": "pending", "time": datetime.now(timezone.utc).isoformat()}],
    )
    db.add(courier_order)
    return {
        "mode": "booked",
        "message": "Order booked with courier. Purchase event will fire after delivery.",
        "tracking_id": result.get("tracking_id"),
    }


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
    if pending.status == "courier_booked":
        return ConfirmResponse(
            status="success",
            order_id=payload.order_id,
            message="Order is already booked with courier. Purchase event will fire after delivery.",
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

    # The pending row is already locked for the booking attempt.
    booking = await _auto_book_courier_for_pending(client.id, pending, db)

    # Now hold the row lock and verify the status is still 'pending' to prevent split-brain transaction commits
    lock_res = await db.execute(
        select(PendingEvent).where(
            and_(
                PendingEvent.id == pending.id,
                PendingEvent.status == "pending",
            )
        ).with_for_update()
    )
    pending_locked = lock_res.scalar_one_or_none()

    if not pending_locked:
        # Rollback any additions to db session (like the CourierOrder) to ensure transaction protection
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Order status changed during booking.",
        )

    if booking["mode"] in {"booked", "already_booked"}:
        pending_locked.status = "courier_booked"
        pending_locked.portal_state = "processing"
        pending_locked.is_confirmed = True
        message = booking["message"]
        logger.info(f"[{client.name}] Order confirmed and booked with courier: {payload.order_id}")
    elif booking["mode"] == "not_configured":
        try:
            await _queue_confirmed_event(client, pending_locked, db)
        except HTTPException:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            logger.error(f"[{client.name}] Confirm queue failed ({payload.order_id}): {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Purchase event queue করতে সমস্যা: {e}",
            )
        pending_locked.status = "confirmed"
        pending_locked.portal_state = "confirmed"
        pending_locked.is_confirmed = True
        pending_locked.confirmed_at = datetime.now(timezone.utc)
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

    # Lock before external booking so overlapping bulk/single confirms serialize per order.
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

    found_ids = {p.order_id for p in pending_events}
    confirmed = 0
    failed = 0
    details = []

    # Fetch ClientModel details once
    client_res = await db.execute(select(ClientModel).where(ClientModel.id == client.id))
    client_obj = client_res.scalar_one_or_none()

    # Fetch existing courier orders for these order IDs once
    existing_orders_res = await db.execute(
        select(CourierOrder.order_id).where(
            and_(
                CourierOrder.client_id == client.id,
                CourierOrder.order_id.in_(payload.order_ids),
            )
        )
    )
    existing_order_ids = set(existing_orders_res.scalars().all())

    auto_send = client_obj and client_obj.courier_auto_send
    default_provider = str(client_obj.default_courier or "").strip().lower() if client_obj else None

    # Categorize and prepare tasks for external HTTP requests
    tasks_to_run = []
    event_actions = {}

    for pending in pending_events:
        oid = pending.order_id
        if not auto_send:
            event_actions[oid] = {"action": "direct"}
            continue

        if not default_provider:
            event_actions[oid] = {"action": "fail", "error": "Default courier provider is missing."}
            continue

        if oid in existing_order_ids:
            event_actions[oid] = {"action": "already_booked"}
            continue

        raw = pending.raw_order_data or {}
        missing = [
            key
            for key in ("recipient_name", "recipient_phone", "recipient_address")
            if not str(raw.get(key) or "").strip()
        ]
        if missing:
            event_actions[oid] = {
                "action": "fail",
                "error": "Courier booking data missing: " + ", ".join(missing),
            }
            continue

        cod_amount = float(raw.get("cod_amount") or 0)
        recipient_name = str(raw.get("recipient_name") or "").strip()
        recipient_phone = str(raw.get("recipient_phone") or "").strip()
        recipient_address = str(raw.get("recipient_address") or "").strip()

        if default_provider == "steadfast":
            if not (client_obj.steadfast_api_key and client_obj.steadfast_secret_key):
                event_actions[oid] = {"action": "fail", "error": "SteadFast credentials are missing."}
                continue

            task = CourierService.send_to_steadfast(
                api_key=client_obj.steadfast_api_key,
                secret_key=decrypt_token(client_obj.steadfast_secret_key),
                recipient_name=recipient_name,
                recipient_phone=recipient_phone,
                recipient_address=recipient_address,
                cod_amount=cod_amount,
                merchant_order_id=oid,
            )
            tasks_to_run.append((oid, task))

        elif default_provider == "pathao":
            if not (client_obj.pathao_api_key and client_obj.pathao_secret_key and client_obj.pathao_store_id):
                event_actions[oid] = {"action": "fail", "error": "Pathao credentials are missing."}
                continue
            try:
                pathao_client_id, email = client_obj.pathao_api_key.split("|", 1)
                pathao_client_secret, password = decrypt_token(client_obj.pathao_secret_key).split("|", 1)
            except ValueError:
                event_actions[oid] = {"action": "fail", "error": "Pathao credentials format is invalid."}
                continue

            # Extract product names from event_data or raw_order_data
            item_description_to_use = None
            event_data_dict = pending.event_data or {}
            custom_data_dict = event_data_dict.get("custom_data") or {}
            contents_list = custom_data_dict.get("contents") or []

            if not contents_list and pending.raw_order_data:
                contents_list = pending.raw_order_data.get("products") or pending.raw_order_data.get("line_items") or []

            if contents_list and isinstance(contents_list, list):
                product_descriptions = []
                for item in contents_list:
                    if isinstance(item, dict):
                        name = item.get("title") or item.get("name") or item.get("content_name")
                        qty = item.get("quantity") or item.get("qty") or 1
                        if name:
                            product_descriptions.append(f"{name} x{qty}")
                if product_descriptions:
                    item_description_to_use = ", ".join(product_descriptions)

            task = CourierService.send_to_pathao(
                client_id=pathao_client_id,
                client_secret=pathao_client_secret,
                email=email,
                password=password,
                store_id=client_obj.pathao_store_id,
                recipient_name=recipient_name,
                recipient_phone=recipient_phone,
                recipient_address=recipient_address,
                cod_amount=cod_amount,
                merchant_order_id=oid,
                item_description=item_description_to_use
            )
            tasks_to_run.append((oid, task))
        elif default_provider == "redx":
            if not (
                client_obj.redx_access_token
                and client_obj.redx_delivery_area_id
                and client_obj.redx_delivery_area_name
            ):
                event_actions[oid] = {"action": "fail", "error": "RedX credentials and default delivery area are missing."}
                continue
            task = CourierService.send_to_redx(
                access_token=decrypt_token(client_obj.redx_access_token),
                recipient_name=recipient_name,
                recipient_phone=recipient_phone,
                recipient_address=recipient_address,
                cod_amount=cod_amount,
                merchant_order_id=oid,
                delivery_area_id=client_obj.redx_delivery_area_id,
                delivery_area_name=client_obj.redx_delivery_area_name,
                pickup_store_id=client_obj.redx_pickup_store_id,
            )
            tasks_to_run.append((oid, task))
        else:
            event_actions[oid] = {"action": "fail", "error": f"Unsupported courier provider: {default_provider}"}

    # Run provider calls in parallel while the matching pending rows remain locked.
    if tasks_to_run:
        order_ids_for_tasks = [item[0] for item in tasks_to_run]
        tasks = [item[1] for item in tasks_to_run]

        # Gather with return_exceptions=True to avoid one failure crashing the entire batch
        api_results = await asyncio.gather(*tasks, return_exceptions=True)

        for oid, res in zip(order_ids_for_tasks, api_results):
            if isinstance(res, Exception):
                event_actions[oid] = {"action": "fail", "error": f"Network exception: {str(res)}"}
            elif not res.get("success"):
                event_actions[oid] = {
                    "action": "fail",
                    "error": res.get("error") or "Unknown courier error",
                }
            else:
                event_actions[oid] = {
                    "action": "book_success",
                    "courier_order_id": res.get("courier_order_id"),
                    "tracking_id": res.get("tracking_id"),
                }

    # Now persist the changes inside DB transaction blocks
    for pending in pending_events:
        oid = pending.order_id
        action_info = event_actions.get(oid, {"action": "direct"})
        action = action_info["action"]

        try:
            async with db.begin_nested():
                # Re-fetch with row lock to prevent race conditions during updates
                lock_res = await db.execute(
                    select(PendingEvent).where(
                        PendingEvent.id == pending.id
                    ).with_for_update()
                )
                pending_locked = lock_res.scalar_one_or_none()
                if not pending_locked or pending_locked.status != "pending":
                    raise HTTPException(status_code=400, detail="Order status changed during booking.")

                if action == "direct":
                    await _queue_confirmed_event(client, pending_locked, db)
                    pending_locked.status = "confirmed"
                    pending_locked.portal_state = "confirmed"
                    pending_locked.is_confirmed = True
                    pending_locked.confirmed_at = datetime.now(timezone.utc)
                    confirmed += 1
                    details.append({"order_id": oid, "status": "queued", "mode": "direct"})

                elif action == "already_booked":
                    pending_locked.status = "courier_booked"
                    pending_locked.portal_state = "processing"
                    pending_locked.is_confirmed = True
                    confirmed += 1
                    details.append({"order_id": oid, "status": "queued", "mode": "courier"})

                elif action == "book_success":
                    raw = pending_locked.raw_order_data or {}
                    cod_amount = float(raw.get("cod_amount") or 0)

                    courier_order = CourierOrder(
                        client_id=client.id,
                        pending_event_id=pending_locked.id,
                        order_id=oid,
                        courier_provider=default_provider,
                        courier_order_id=action_info.get("courier_order_id"),
                        courier_tracking_id=action_info.get("tracking_id"),
                        courier_status="pending",
                        recipient_name=str(raw.get("recipient_name") or "").strip(),
                        recipient_phone=str(raw.get("recipient_phone") or "").strip(),
                        recipient_address=str(raw.get("recipient_address") or "").strip(),
                        cod_amount=cod_amount,
                        status_history=[{"status": "pending", "time": datetime.now(timezone.utc).isoformat()}],
                    )
                    db.add(courier_order)

                    pending_locked.status = "courier_booked"
                    pending_locked.portal_state = "processing"
                    pending_locked.is_confirmed = True

                    confirmed += 1
                    details.append({"order_id": oid, "status": "queued", "mode": "courier"})

                elif action == "fail":
                    error_msg = action_info.get("error") or "Unknown error"
                    raise HTTPException(status_code=400, detail=error_msg)

        except HTTPException as e:
            failed += 1
            details.append({"order_id": oid, "status": "failed", "error": str(e.detail)})
            logger.error(f"[{client.name}] Bulk confirm queue rejected ({oid}): {e.detail}")
        except Exception as e:
            failed += 1
            details.append({"order_id": oid, "status": "failed", "error": f"Internal error: {str(e)}"})
            logger.exception(f"[{client.name}] Bulk confirm queue failed ({oid})")

    # Not found orders
    for oid in payload.order_ids:
        if oid not in found_ids:
            failed += 1
            details.append({"order_id": oid, "status": "not_found"})

    await db.commit()

    logger.info(f"[{client.name}] Bulk confirm queued: {confirmed} confirmed, {failed} failed")

    return BulkConfirmResponse(
        status="success",
        confirmed=confirmed,
        failed=failed,
        details=details,
    )


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

    details = [
        {"order_id": oid, "status": "cancelled" if oid in found_ids else "not_found"}
        for oid in order_ids
    ]
    cancelled = len(found_ids)
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

    sum_and_min_stmt = select(
        sql_func.sum(cast(PendingEvent.event_data['custom_data']['value'].astext, Numeric)),
        sql_func.min(PendingEvent.created_at)
    ).where(
        and_(
            PendingEvent.client_id == client.id,
            PendingEvent.status == "pending"
        )
    )
    sum_and_min_res = await db.execute(sum_and_min_stmt)
    sum_val, oldest_created = sum_and_min_res.fetchone()
    pending_value = float(sum_val or 0.0)

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
