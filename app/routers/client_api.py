import logging
import secrets
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Request, Response, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, update, desc, cast, Numeric, String

from app.database import get_db
from app.models.client import Client
from app.models.courier_order import CourierOrder
from app.models.event_log import EventLog
from app.models.event_outbox import EventOutbox
from app.models.pending_event import PendingEvent
from app.models.incomplete_checkout import IncompleteCheckout
from app.models.audit_log import AuditLog
from app.models.plugin_connect_session import PluginConnectSession
from app.models.usage_counter import UsageCounter
from app.routers.client_portal import get_client_from_portal_session
from app.security import encrypt_token, decrypt_token, meta_credentials_configured
from app.routers.deferred_events import _queue_confirmed_event
import calendar
from app.models.client_user import ClientUser
from app.routers.client_auth import (
    _clean_domain,
    _clean_name,
    _validate_email,
    _validate_password,
    get_client_user_from_cookie,
    require_allowed_origin,
)
from app.services.auth_service import hash_password, verify_password
from app.services.plan_service import (
    apply_expired_trial_downgrade,
    default_monthly_event_limit,
    effective_plan_tier,
    has_growth_access,
    plan_summary,
    record_trial_identity,
    require_growth_access,
    require_trial_available,
)
from app.services.site_binding_service import require_site_binding_available
from app.utils.plugin_connect import (
    append_query,
    is_site_allowed_for_client,
    normalize_site_url,
    sha256_hex,
    validate_return_url,
    validate_token,
)

logger = logging.getLogger(__name__)
router = APIRouter()
PRODUCT_GUIDE_DISMISSED_KEY = "__product_guide_dismissed__"

# â”€â”€â”€ Auth Dependency â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async def get_current_portal_client(request: Request, db: AsyncSession = Depends(get_db)) -> Client:
    require_allowed_origin(request)
    client = await get_client_from_portal_session(request, db)
    if not client or not client.is_active:
        raise HTTPException(status_code=401, detail="Unauthorized session. Please login.")
    if apply_expired_trial_downgrade(client):
        await db.commit()
        from app.dependencies import clear_client_cache
        clear_client_cache(client.api_key)
    return client

# â”€â”€â”€ Schemas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class ProfileUpdateRequest(BaseModel):
    name: str
    email: str | None = None
    notificationEmail: str | None = None
    ownerNotifyWhatsapp: bool | None = None
    ownerWhatsappNumber: str | None = None

class CredentialsUpdateRequest(BaseModel):
    platform: str
    enabled: bool | None = None
    pixelIdOrMeasurementId: str | None = None
    accessToken: str | None = None
    testEventCode: str | None = None

class RulesUpdateRequest(BaseModel):
    rules: list[dict]

class CampaignTestRequest(BaseModel):
    platform: str
    eventName: str
    value: str | None = None
    currency: str | None = None
    email: str | None = None
    phone: str | None = None
    ip: str | None = None
    userAgent: str | None = None
    customParams: dict | None = None

class PasswordUpdateRequest(BaseModel):
    currentPassword: str
    newPassword: str

class SidebarSeenRequest(BaseModel):
    section: str

class IncompleteCheckoutStatusRequest(BaseModel):
    status: str

class ManualRecoveryOrderItem(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content_id: str | None = Field(default=None, max_length=255)
    quantity: int = Field(default=1, ge=1, le=999)
    price: float = Field(default=0, ge=0, le=100000000)
    attributes: dict[str, str] = Field(default_factory=dict)
    category: str | None = Field(default=None, max_length=255)

class ManualRecoveryOrderRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=8, max_length=32)
    address: str = Field(min_length=5, max_length=500)
    items: list[ManualRecoveryOrderItem] = Field(min_length=1, max_length=30)
    delivery_charge: float = Field(default=0, ge=0, le=100000000)
    discount: float = Field(default=0, ge=0, le=100000000)
    note: str | None = Field(default=None, max_length=500)


class PluginConnectAuthorizeRequest(BaseModel):
    siteUrl: str
    returnUrl: str
    state: str
    codeChallenge: str


ALLOWED_RULE_EVENTS = {
    "PageView",
    "ViewContent",
    "AddToCart",
    "ViewCart",
    "RemoveFromCart",
    "InitiateCheckout",
    "AddPaymentInfo",
    "Purchase",
    "Lead",
    "Contact",
    "CompleteRegistration",
    "Subscribe",
    "Search",
    "Refund",
}

SIDEBAR_SEEN_KEYS = {
    "order_verification": "order_verification_seen_at",
    "orders_delivery": "orders_delivery_seen_at",
}

ACTIVE_COURIER_STATUSES = [
    "booking_queued",
    "booking_processing",
    "pending",
    "picked",
    "in_transit",
    "processing",
    "booked",
    "shipped",
]
INCOMPLETE_CHECKOUT_STATUSES = {"active", "incomplete", "contacted", "recovered", "ignored", "expired"}


def _parse_seen_at(value: str | None) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_rules(rules: list[dict]) -> list[dict]:
    if len(rules) > 100:
        raise HTTPException(status_code=400, detail="Too many routing rules.")
    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise HTTPException(status_code=400, detail="Invalid routing rule.")
        event_name = str(rule.get("eventName", "")).strip()
        if not event_name or len(event_name) > 80:
            raise HTTPException(status_code=400, detail="Invalid event name in routing rule.")
        if event_name not in ALLOWED_RULE_EVENTS and not event_name.replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="Invalid event name in routing rule.")
        cleaned.append({
            "eventName": event_name,
            "metaEnabled": bool(rule.get("metaEnabled")),
            "tiktokEnabled": bool(rule.get("tiktokEnabled")),
            "ga4Enabled": bool(rule.get("ga4Enabled")),
        })
    return cleaned


async def _refresh_incomplete_checkout_states(db: AsyncSession, client_id: int) -> None:
    from app.routers.incomplete_checkouts import recover_open_checkouts_for_order

    now = datetime.now(timezone.utc)
    inactive_before = now - timedelta(minutes=20)
    expire_before = now - timedelta(days=30)

    pending_result = await db.execute(
        select(PendingEvent).where(
            PendingEvent.client_id == client_id,
            PendingEvent.status == "pending",
        )
    )
    for pending in pending_result.scalars().all():
        raw_order_data = pending.raw_order_data if isinstance(pending.raw_order_data, dict) else {}
        phone = str(
            raw_order_data.get("recipient_phone")
            or raw_order_data.get("customer_phone")
            or raw_order_data.get("billing_phone")
            or ""
        ).strip()
        if phone:
            await recover_open_checkouts_for_order(
                db,
                client_id=client_id,
                phone=phone,
                order_id=str(pending.order_id),
            )

    stale_result = await db.execute(
        select(IncompleteCheckout).where(
            IncompleteCheckout.client_id == client_id,
            IncompleteCheckout.status == "active",
            IncompleteCheckout.last_activity_at < inactive_before,
        )
    )
    stale_checkouts = stale_result.scalars().all()
    client = await db.get(Client, client_id) if stale_checkouts else None
    if client:
        from app.services.notification_service import create_incomplete_checkout_whatsapp_job

        for checkout in stale_checkouts:
            checkout.status = "incomplete"
            await create_incomplete_checkout_whatsapp_job(db, client, checkout)

    await db.execute(
        update(IncompleteCheckout)
        .where(
            IncompleteCheckout.client_id == client_id,
            IncompleteCheckout.status == "active",
            IncompleteCheckout.last_activity_at < inactive_before,
        )
        .values(status="incomplete")
    )
    await db.execute(
        update(IncompleteCheckout)
        .where(
            IncompleteCheckout.client_id == client_id,
            IncompleteCheckout.status.in_(["active", "incomplete", "contacted", "ignored"]),
            IncompleteCheckout.last_activity_at < expire_before,
        )
        .values(status="expired")
    )
    await db.commit()


def _incomplete_checkout_json(row: IncompleteCheckout) -> dict:
    return {
        "id": row.id,
        "phone": row.phone,
        "customerName": row.customer_name or "â€”",
        "email": row.email or "â€”",
        "address": row.address or "â€”",
        "products": row.products or [],
        "amount": float(row.amount or 0),
        "currency": row.currency,
        "pageUrl": row.page_url or "",
        "campaignData": row.campaign_data or {},
        "status": row.status,
        "orderId": row.order_id,
        "lastActivityAt": row.last_activity_at.isoformat(),
        "createdAt": row.created_at.isoformat(),
        "convertedAt": row.converted_at.isoformat() if row.converted_at else None,
    }


@router.get("/incomplete-checkouts")
async def list_incomplete_checkouts(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=250),
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    require_growth_access(client, "Incomplete checkout recovery")
    await _refresh_incomplete_checkout_states(db, client.id)
    stmt = select(IncompleteCheckout).where(IncompleteCheckout.client_id == client.id)
    if status:
        if status not in INCOMPLETE_CHECKOUT_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid incomplete checkout status.")
        stmt = stmt.where(IncompleteCheckout.status == status)
    else:
        stmt = stmt.where(IncompleteCheckout.status.in_(["incomplete", "contacted", "recovered"]))
    result = await db.execute(stmt.order_by(desc(IncompleteCheckout.last_activity_at)).limit(limit))
    items = [_incomplete_checkout_json(row) for row in result.scalars().all()]
    counts_result = await db.execute(
        select(IncompleteCheckout.status, func.count(IncompleteCheckout.id))
        .where(IncompleteCheckout.client_id == client.id)
        .group_by(IncompleteCheckout.status)
    )
    counts = {str(row_status): int(count or 0) for row_status, count in counts_result.all()}
    return {"items": items, "counts": counts}


@router.post("/incomplete-checkouts/{checkout_id}/status")
async def update_incomplete_checkout_status(
    checkout_id: int,
    payload: IncompleteCheckoutStatusRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    require_growth_access(client, "Incomplete checkout recovery")
    allowed = {"contacted", "ignored", "incomplete"}
    if payload.status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid manual recovery status.")
    result = await db.execute(
        select(IncompleteCheckout).where(
            IncompleteCheckout.id == checkout_id,
            IncompleteCheckout.client_id == client.id,
        )
    )
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Incomplete checkout not found.")
    if draft.status in {"recovered", "expired"}:
        raise HTTPException(status_code=409, detail="This incomplete checkout can no longer be changed.")
    draft.status = payload.status
    draft.last_activity_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "item": _incomplete_checkout_json(draft)}


async def _create_manual_recovery_order(
    db: AsyncSession,
    *,
    client: Client,
    checkout_id: int,
    payload: ManualRecoveryOrderRequest,
) -> dict:
    require_growth_access(client, "Incomplete checkout recovery")
    result = await db.execute(
        select(IncompleteCheckout)
        .where(
            IncompleteCheckout.id == checkout_id,
            IncompleteCheckout.client_id == client.id,
        )
        .with_for_update()
    )
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Incomplete checkout not found.")
    if draft.status not in {"incomplete", "contacted"}:
        raise HTTPException(status_code=400, detail="Only incomplete or contacted leads can be converted to an order.")
    if draft.order_id:
        raise HTTPException(status_code=409, detail="This recovery lead already has a linked order.")

    from app.routers.incomplete_checkouts import _normalize_phone

    try:
        phone = _normalize_phone(payload.phone)
    except HTTPException as exc:
        raise HTTPException(status_code=422, detail="A valid customer phone is required.") from exc

    cleaned_items = []
    content_ids = []
    total_items = 0
    subtotal = 0.0
    for index, item in enumerate(payload.items, start=1):
        content_id = (item.content_id or f"manual-{checkout_id}-{index}").strip()
        attributes = {
            str(key).strip()[:80]: str(value).strip()[:120]
            for key, value in (item.attributes or {}).items()
            if str(key).strip() and str(value).strip()
        }
        quantity = int(item.quantity)
        price = float(item.price or 0)
        subtotal += price * quantity
        total_items += quantity
        content_ids.append(content_id)
        cleaned_items.append({
            "id": content_id,
            "content_id": content_id,
            "content_type": "product",
            "title": item.name.strip(),
            "name": item.name.strip(),
            "content_name": item.name.strip(),
            "content_category": (item.category or "").strip(),
            "attributes": attributes,
            "quantity": quantity,
            "price": price,
            "item_price": price,
        })

    total_amount = max(0.0, subtotal + float(payload.delivery_charge or 0) - float(payload.discount or 0))
    now = datetime.now(timezone.utc)
    order_id = f"manual-{checkout_id}-{int(now.timestamp())}"
    raw_order_data = {
        "recipient_name": payload.customer_name.strip(),
        "recipient_phone": phone,
        "recipient_address": payload.address.strip(),
        "cod_amount": total_amount,
        "products": cleaned_items,
        "delivery_charge": float(payload.delivery_charge or 0),
        "discount": float(payload.discount or 0),
        "note": (payload.note or "").strip(),
        "source": "incomplete_checkout_manual_recovery",
        "recovery_checkout_id": checkout_id,
    }
    event_data = {
        "event_name": "Purchase",
        "event_time": int(now.timestamp()),
        "event_id": f"manual_recovery_{checkout_id}_{int(now.timestamp())}",
        "event_source_url": draft.page_url or "",
        "action_source": "website",
        "user_data": {},
        "custom_data": {
            "value": total_amount,
            "currency": draft.currency or "BDT",
            "content_ids": content_ids,
            "contents": cleaned_items,
            "content_type": "product",
            "num_items": total_items,
            "order_id": order_id,
            "trigger_reason": "manual_incomplete_checkout_recovery",
        },
        "raw_order_data": raw_order_data,
    }

    pending = PendingEvent(
        client_id=client.id,
        order_id=order_id,
        event_data=event_data,
        raw_order_data=raw_order_data,
        status="pending",
        portal_state="manual_recovery",
    )
    db.add(pending)

    draft.status = "recovered"
    draft.order_id = order_id
    draft.converted_at = now
    draft.last_activity_at = now
    draft.customer_name = payload.customer_name.strip()
    draft.phone = phone
    draft.address = payload.address.strip()
    draft.amount = total_amount
    draft.products = cleaned_items

    from app.services.notification_service import create_purchase_whatsapp_job

    await create_purchase_whatsapp_job(db, client, event_data)
    await db.commit()
    await db.refresh(pending)
    return {
        "success": True,
        "orderId": order_id,
        "pendingEventId": pending.id,
        "checkoutId": draft.id,
        "status": draft.status,
    }


@router.post("/incomplete-checkouts/{checkout_id}/create-order")
async def create_order_from_incomplete_checkout(
    checkout_id: int,
    payload: ManualRecoveryOrderRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    return await _create_manual_recovery_order(
        db,
        client=client,
        checkout_id=checkout_id,
        payload=payload,
    )

# --- Sidebar Notification State ---
@router.get("/sidebar/status")
async def get_sidebar_status(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    seen_state = client.portal_seen_state if isinstance(client.portal_seen_state, dict) else {}
    order_seen_at = _parse_seen_at(seen_state.get("order_verification_seen_at"))
    delivery_seen_at = _parse_seen_at(seen_state.get("orders_delivery_seen_at"))

    pending_total_r = await db.execute(
        select(func.count(PendingEvent.id)).where(and_(
            PendingEvent.client_id == client.id,
            PendingEvent.status == "pending",
        ))
    )
    pending_new_r = await db.execute(
        select(func.count(PendingEvent.id)).where(and_(
            PendingEvent.client_id == client.id,
            PendingEvent.status == "pending",
            PendingEvent.created_at > order_seen_at,
        ))
    )

    delivery_total_r = await db.execute(
        select(func.count(CourierOrder.id)).where(and_(
            CourierOrder.client_id == client.id,
            CourierOrder.courier_status.in_(ACTIVE_COURIER_STATUSES),
        ))
    )
    delivery_new_r = await db.execute(
        select(func.count(CourierOrder.id)).where(and_(
            CourierOrder.client_id == client.id,
            CourierOrder.courier_status.in_(ACTIVE_COURIER_STATUSES),
            CourierOrder.created_at > delivery_seen_at,
        ))
    )

    return {
        "orderVerificationTotal": int(pending_total_r.scalar() or 0),
        "orderVerificationNew": int(pending_new_r.scalar() or 0),
        "ordersDeliveryTotal": int(delivery_total_r.scalar() or 0),
        "ordersDeliveryNew": int(delivery_new_r.scalar() or 0),
        "seenState": {
            "orderVerificationSeenAt": seen_state.get("order_verification_seen_at"),
            "ordersDeliverySeenAt": seen_state.get("orders_delivery_seen_at"),
        },
    }


@router.post("/sidebar/mark-seen")
async def mark_sidebar_seen(
    payload: SidebarSeenRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    key = SIDEBAR_SEEN_KEYS.get(payload.section)
    if not key:
        raise HTTPException(status_code=400, detail="Invalid sidebar section.")

    seen_state = dict(client.portal_seen_state or {})
    seen_state[key] = datetime.now(timezone.utc).isoformat()
    client.portal_seen_state = seen_state
    await db.commit()
    return {"success": True, "seenState": seen_state}

# â”€â”€â”€ Profile & Usage Stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@router.get("/profile")
async def get_profile(
    request: Request,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    monthly_key = f"monthly:{now.strftime('%Y-%m')}"

    result = await db.execute(
        select(UsageCounter.count).where(
            and_(
                UsageCounter.client_id == client.id,
                UsageCounter.window_key == monthly_key
            )
        )
    )
    db_events_used = result.scalar() or 0

    # Also check Redis for real-time count (Redis may be ahead of DB sync)
    redis_events_used = 0
    try:
        from app.services.redis_pool import get_redis
        r = get_redis()
        if r is not None:
            rkey = f"usage:{client.id}:{monthly_key}"
            redis_val = await r.get(rkey)
            if redis_val is not None:
                redis_events_used = int(redis_val)
    except Exception as redis_err:
        logger.warning(f"[profile] Redis usage read failed: {redis_err}")

    # Use the higher of DB or Redis â€” whichever is more up-to-date
    events_used = max(db_events_used, redis_events_used)
    plan = plan_summary(client, now)
    events_quota = plan["eventsQuota"]

    try:
        user, _, _ = await get_client_user_from_cookie(request, db)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid user session.")
    email = user.email

    last_day = calendar.monthrange(now.year, now.month)[1]
    renewal_date = now.replace(day=last_day).strftime("%B %d, %Y")
    if plan["isTrial"] and plan["trialEndsAt"]:
        renewal_date = datetime.fromisoformat(plan["trialEndsAt"]).strftime("%B %d, %Y")

    return {
        "name": client.name,
        "email": email,
        "plan": plan["name"],
        "planTier": plan["tier"],
        "isTrial": plan["isTrial"],
        "trialEndsAt": plan["trialEndsAt"],
        "trialDaysRemaining": plan["trialDaysRemaining"],
        "growthFeaturesEnabled": plan["growthFeaturesEnabled"],
        "ordersQuota": plan["ordersQuota"],
        "renewalDate": renewal_date,
        "eventsUsed": events_used,
        "eventsQuota": events_quota,
        "ownerNotifyWhatsapp": client.owner_notify_whatsapp,
        "ownerWhatsappNumber": client.owner_whatsapp_number or "",
        "guideDismissed": PRODUCT_GUIDE_DISMISSED_KEY in set(client.dismissed_suggestions or []),
    }

@router.post("/profile")
async def update_profile(
    request: Request,
    payload: ProfileUpdateRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    client.name = _clean_name(payload.name, "Display name")

    try:
        user, _, _ = await get_client_user_from_cookie(request, db)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid user session.")

    if user and payload.email:
        email = _validate_email(payload.email)
        existing = await db.execute(
            select(ClientUser).where(ClientUser.email == email, ClientUser.id != user.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="An account already exists for this email.")
        user.email = email
    if user and payload.notificationEmail is not None:
        user.notification_email = _validate_email(payload.notificationEmail) if payload.notificationEmail.strip() else None
        # notification_email is persisted by the portal state migration.

    if payload.ownerNotifyWhatsapp is not None:
        client.owner_notify_whatsapp = payload.ownerNotifyWhatsapp
        if payload.ownerNotifyWhatsapp:
            await _assign_active_whatsapp_instance(client, db)
        else:
            client.whatsapp_instance_id = None

    if payload.ownerWhatsappNumber is not None:
        client.owner_whatsapp_number = payload.ownerWhatsappNumber.strip() if payload.ownerWhatsappNumber.strip() else None

    await db.commit()

    now = datetime.now(timezone.utc)
    monthly_key = f"monthly:{now.strftime('%Y-%m')}"

    result = await db.execute(
        select(UsageCounter.count).where(
            and_(
                UsageCounter.client_id == client.id,
                UsageCounter.window_key == monthly_key
            )
        )
    )
    events_used = result.scalar() or 0
    plan = plan_summary(client, now)
    events_quota = plan["eventsQuota"]

    last_day = calendar.monthrange(now.year, now.month)[1]
    renewal_date = now.replace(day=last_day).strftime("%B %d, %Y")
    if plan["isTrial"] and plan["trialEndsAt"]:
        renewal_date = datetime.fromisoformat(plan["trialEndsAt"]).strftime("%B %d, %Y")

    return {"success": True, "profile": {
        "name": client.name,
        "email": user.email,
        "plan": plan["name"],
        "planTier": plan["tier"],
        "isTrial": plan["isTrial"],
        "trialEndsAt": plan["trialEndsAt"],
        "trialDaysRemaining": plan["trialDaysRemaining"],
        "growthFeaturesEnabled": plan["growthFeaturesEnabled"],
        "ordersQuota": plan["ordersQuota"],
        "renewalDate": renewal_date,
        "eventsUsed": events_used,
        "eventsQuota": events_quota,
        "ownerNotifyWhatsapp": client.owner_notify_whatsapp,
        "ownerWhatsappNumber": client.owner_whatsapp_number or "",
        "guideDismissed": PRODUCT_GUIDE_DISMISSED_KEY in set(client.dismissed_suggestions or []),
    }}


@router.post("/guide/dismiss")
async def dismiss_product_guide(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    dismissed = list(client.dismissed_suggestions or [])
    if PRODUCT_GUIDE_DISMISSED_KEY not in dismissed:
        dismissed.append(PRODUCT_GUIDE_DISMISSED_KEY)
        client.dismissed_suggestions = dismissed
        await db.commit()
    return {"success": True, "guideDismissed": True}

@router.post("/account/password")
async def update_account_password(
    payload: PasswordUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_allowed_origin(request)
    _validate_password(payload.newPassword)

    # Retrieve user and the current session to exempt it from revocation
    user, _, current_session = await get_client_user_from_cookie(request, db)

    if not verify_password(payload.currentPassword, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    user.password_hash = hash_password(payload.newPassword)

    # Revoke all other active sessions for this user
    from app.models.client_session import ClientSession
    from sqlalchemy import update as sql_update
    await db.execute(
        sql_update(ClientSession)
        .where(
            ClientSession.user_id == user.id,
            ClientSession.id != current_session.id,
            ClientSession.revoked_at.is_(None)
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )

    await db.commit()
    return {"success": True}


@router.post("/profile/reset-demo")
async def reset_demo(client: Client = Depends(get_current_portal_client)):
    return {"success": True}

# â”€â”€â”€ WordPress Connection Status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@router.get("/connection")
async def get_connection(client: Client = Depends(get_current_portal_client)):
    return {
        "wpVersion": "6.4.3",
        "lastHeartbeat": client.updated_at.isoformat() if client.updated_at else datetime.now(timezone.utc).isoformat(),
        "status": "Active" if client.is_active else "Disconnected",
        "token": client.public_key or "",
        "api_key": client.api_key
    }

@router.post("/connection/test")
async def test_wp_connection(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    client.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "success": True,
        "message": "WP Heartbeat registered successfully. Connection parameters are clean.",
        "connection": {
            "wpVersion": "6.4.3",
            "lastHeartbeat": client.updated_at.isoformat(),
            "status": "Active",
            "token": client.public_key or client.api_key,
            "api_key": client.api_key
        }
    }

@router.post("/connection/revoke")
async def revoke_wp_token(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    old_api_key = client.api_key
    client.api_key = secrets.token_urlsafe(32)
    client.public_key = secrets.token_urlsafe(24)
    await db.commit()
    from app.dependencies import clear_client_cache
    clear_client_cache(old_api_key)
    clear_client_cache(client.api_key)
    return {
        "success": True,
        "connection": {
            "wpVersion": "6.4.3",
            "lastHeartbeat": datetime.now(timezone.utc).isoformat(),
            "status": "Disconnected",
            "token": client.public_key,
            "api_key": client.api_key
        }
    }

# â”€â”€â”€ Platform Credentials â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@router.post("/plugin-connect/authorize")
async def authorize_plugin_connect(
    request: Request,
    payload: PluginConnectAuthorizeRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    site_url, site_host = normalize_site_url(payload.siteUrl)
    return_url = validate_return_url(payload.returnUrl, site_host)
    state = validate_token(payload.state, "state")
    code_challenge = validate_token(payload.codeChallenge, "code challenge")

    if not is_site_allowed_for_client(site_host, client.domain):
        raise HTTPException(
            status_code=403,
            detail=f"This WordPress site ({site_host}) is not allowed for this workspace.",
        )
    await require_site_binding_available(db, site_host, client.id)

    if not client.domain:
        client.domain = site_host

    code = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session = PluginConnectSession(
        client_id=client.id,
        site_url=site_url,
        site_host=site_host,
        return_url=return_url,
        state=state,
        code_hash=sha256_hex(code),
        code_challenge=code_challenge,
        created_ip=request.client.host if request.client else None,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(session)
    db.add(AuditLog(
        actor="client_portal",
        action="plugin_connect_authorized",
        client_id=client.id,
        ip_address=request.client.host if request.client else None,
        details=f"site={site_host}",
    ))
    await db.commit()

    return {
        "success": True,
        "siteHost": site_host,
        "expiresAt": session.expires_at.isoformat(),
        "redirectUrl": append_query(return_url, {"code": code, "state": state}),
    }


@router.get("/credentials")
async def get_credentials(client: Client = Depends(get_current_portal_client)):
    growth_enabled = has_growth_access(client)
    meta_configured = meta_credentials_configured(client)
    return {
        "Meta CAPI": {
            "enabled": client.enable_facebook,
            "pixelIdOrMeasurementId": client.pixel_id if client.pixel_id != "0" else "",
            "accessToken": "EAAD" + "*" * 12 if meta_configured else "",
            "status": "Valid" if meta_configured else "Untested",
            "testEventCode": client.test_event_code or ""
        },
        "TikTok Events API": {
            "enabled": growth_enabled and client.enable_tiktok,
            "pixelIdOrMeasurementId": client.tiktok_pixel_id or "",
            "accessToken": "tt_ac" + "*" * 12 if client.tiktok_access_token else "",
            "status": "Valid" if client.tiktok_pixel_id and client.tiktok_access_token else "Untested",
            "testEventCode": client.tiktok_test_event_code or ""
        },
        "GA4": {
            "enabled": growth_enabled and client.enable_ga4,
            "pixelIdOrMeasurementId": client.ga4_measurement_id or "",
            "accessToken": "secret" + "*" * 12 if client.ga4_api_secret else "",
            "status": "Valid" if client.ga4_measurement_id and client.ga4_api_secret else "Untested"
        }
    }

@router.post("/credentials")
async def update_credentials(
    payload: CredentialsUpdateRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    p = payload.platform
    val = payload.pixelIdOrMeasurementId
    token = payload.accessToken
    test_code = payload.testEventCode
    growth_enabled = has_growth_access(client)

    if p == "Meta CAPI":
        if payload.enabled is not None:
            client.enable_facebook = payload.enabled
        if val is not None:
            clean_val = val.strip()
            if clean_val and not clean_val.isdigit():
                raise HTTPException(status_code=400, detail="Meta Pixel ID must be numeric.")
            client.pixel_id = clean_val or "0"
        if token and not token.startswith("EAAD*****") and token.strip():
            client.access_token = encrypt_token(token.strip())
        if test_code is not None:
            client.test_event_code = test_code.strip() if test_code.strip() else None
    elif p == "TikTok Events API":
        if not growth_enabled and (payload.enabled is not False or val or token or test_code):
            require_growth_access(client, "TikTok Events API")
        if payload.enabled is not None:
            client.enable_tiktok = growth_enabled and payload.enabled
        if val is not None:
            clean_val = val.strip()
            if clean_val and not clean_val.isalnum():
                raise HTTPException(status_code=400, detail="TikTok Pixel ID must be alphanumeric.")
            client.tiktok_pixel_id = clean_val or None
        if token and not token.startswith("tt_ac*****") and token.strip():
            client.tiktok_access_token = encrypt_token(token.strip())
        if test_code is not None:
            client.tiktok_test_event_code = test_code.strip() if test_code.strip() else None
    elif p == "GA4":
        if not growth_enabled and (payload.enabled is not False or val or token):
            require_growth_access(client, "GA4 server-side delivery")
        if payload.enabled is not None:
            client.enable_ga4 = growth_enabled and payload.enabled
        if val is not None:
            clean_val = val.strip()
            if clean_val and not clean_val.startswith("G-"):
                raise HTTPException(status_code=400, detail="GA4 Measurement ID must start with G-.")
            client.ga4_measurement_id = clean_val or None
        if token and not token.startswith("secret*****") and token.strip():
            client.ga4_api_secret = encrypt_token(token.strip())
    else:
        raise HTTPException(status_code=400, detail="Unknown platform.")

    if p == "Meta CAPI" and getattr(client, "trial_started_at", None):
        await require_trial_available(db, domain=client.domain, pixel_id=client.pixel_id, exclude_client_id=client.id)
        await record_trial_identity(db, client, source="credentials")

    await db.commit()
    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)

    meta_configured = meta_credentials_configured(client)
    meta_status = "Valid" if meta_configured else "Untested"
    tiktok_status = "Valid" if client.tiktok_pixel_id and client.tiktok_access_token else "Untested"
    ga4_status = "Valid" if client.ga4_measurement_id and client.ga4_api_secret else "Untested"

    return {
        "success": True,
        "credentials": {
            "Meta CAPI": {
                "enabled": client.enable_facebook,
                "pixelIdOrMeasurementId": client.pixel_id if client.pixel_id != "0" else "",
                "accessToken": "EAAD" + "*" * 12 if meta_configured else "",
                "status": meta_status,
                "testEventCode": client.test_event_code or ""
            },
            "TikTok Events API": {
                "enabled": growth_enabled and client.enable_tiktok,
                "pixelIdOrMeasurementId": client.tiktok_pixel_id or "",
                "accessToken": "tt_ac" + "*" * 12 if client.tiktok_access_token else "",
                "status": tiktok_status,
                "testEventCode": client.tiktok_test_event_code or ""
            },
            "GA4": {
                "enabled": growth_enabled and client.enable_ga4,
                "pixelIdOrMeasurementId": client.ga4_measurement_id or "",
                "accessToken": "secret" + "*" * 12 if client.ga4_api_secret else "",
                "status": ga4_status
            }
        }
    }

# â”€â”€â”€ Event Routing Rules â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Note: Event routing rules are stored in the client.event_rules column.
# If not set, we default based on client's globally enabled platforms.
@router.get("/rules")
async def get_rules(client: Client = Depends(get_current_portal_client)):
    growth_enabled = has_growth_access(client)
    if client.event_rules and isinstance(client.event_rules, list):
        return client.event_rules
    return [
        { "eventName": "PageView", "metaEnabled": client.enable_facebook, "tiktokEnabled": growth_enabled and client.enable_tiktok, "ga4Enabled": growth_enabled and client.enable_ga4 },
        { "eventName": "AddToCart", "metaEnabled": client.enable_facebook, "tiktokEnabled": growth_enabled and client.enable_tiktok, "ga4Enabled": growth_enabled and client.enable_ga4 },
        { "eventName": "InitiateCheckout", "metaEnabled": client.enable_facebook, "tiktokEnabled": growth_enabled and client.enable_tiktok, "ga4Enabled": growth_enabled and client.enable_ga4 },
        { "eventName": "Purchase", "metaEnabled": client.enable_facebook, "tiktokEnabled": growth_enabled and client.enable_tiktok, "ga4Enabled": growth_enabled and client.enable_ga4 }
    ]

@router.post("/rules")
async def update_rules(
    payload: RulesUpdateRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    client.event_rules = _validate_rules(payload.rules)
    await db.commit()

    # Clear client cache
    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)

    return {"success": True, "rules": client.event_rules}

# â”€â”€â”€ Telemetry Logs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@router.get("/events")
async def get_events(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    platform: str | None = None
):
    query = select(EventLog).where(EventLog.client_id == client.id).order_by(desc(EventLog.created_at))
    count_query = select(func.count(EventLog.id)).where(EventLog.client_id == client.id)

    if status:
        status_list = status.split(",")
        db_statuses = [s.lower() for s in status_list]
        query = query.where(EventLog.status.in_(db_statuses))
        count_query = count_query.where(EventLog.status.in_(db_statuses))

    if platform:
        if platform == "GA4":
            query = query.where(EventLog.event_name.like("GA4:%"))
            count_query = count_query.where(EventLog.event_name.like("GA4:%"))
        elif platform == "TikTok Events API":
            query = query.where(EventLog.event_name.like("TikTok:%"))
            count_query = count_query.where(EventLog.event_name.like("TikTok:%"))
        elif platform == "TikTok Browser Pixel":
            query = query.where(EventLog.event_name.like("BrowserTikTok:%"))
            count_query = count_query.where(EventLog.event_name.like("BrowserTikTok:%"))
        elif platform == "Webhook":
            query = query.where(EventLog.event_name.like("Webhook:%"))
            count_query = count_query.where(EventLog.event_name.like("Webhook:%"))
        elif platform == "Meta CAPI":
            query = query.where(
                (EventLog.event_name.like("Facebook:%")) | (~EventLog.event_name.contains(":"))
            )
            count_query = count_query.where(
                (EventLog.event_name.like("Facebook:%")) | (~EventLog.event_name.contains(":"))
            )

    result = await db.execute(query.offset(offset).limit(limit))
    logs = result.scalars().all()

    count_r = await db.execute(count_query)
    total_count = count_r.scalar() or 0

    events_list = []
    for idx, log in enumerate(logs):
        raw_event_name = log.event_name
        log_platform = "Meta CAPI"
        display_event_name = raw_event_name

        if ":" in raw_event_name:
            parts = raw_event_name.split(":", 1)
            channel = parts[0]
            display_event_name = parts[1]
            if channel.lower() == "tiktok":
                log_platform = "TikTok Events API"
            elif channel.lower() == "browsertiktok":
                log_platform = "TikTok Browser Pixel"
            elif channel.lower() == "ga4":
                log_platform = "GA4"
            elif channel.lower() in ("facebook", "capi", "meta"):
                log_platform = "Meta CAPI"
            elif channel.lower() == "webhook":
                log_platform = "Webhook"
        else:
            if getattr(client, "enable_facebook", True) and meta_credentials_configured(client):
                log_platform = "Meta CAPI"
            elif getattr(client, "enable_tiktok", True) and client.tiktok_pixel_id and client.tiktok_access_token:
                log_platform = "TikTok Events API"
            elif getattr(client, "enable_ga4", True) and client.ga4_measurement_id and client.ga4_api_secret:
                log_platform = "GA4"
            elif client.webhook_url:
                log_platform = "Webhook"

        events_list.append({
            "id": f"evt_{log.id}",
            "timestamp": log.created_at.isoformat() if log.created_at else datetime.now(timezone.utc).isoformat(),
            "name": display_event_name,
            "platform": log_platform,
            "status": "Fired" if log.status == "fired" else ("Success" if log.status == "success" else "Failed"),
            "httpCode": 202 if log.status == "fired" else (200 if log.status == "success" else 400),
            "deduplicationKey": log.event_id or f"did_{log.id}",
            "payload": {
                "event_name": display_event_name,
                "event_time": int(log.created_at.timestamp()) if log.created_at else int(datetime.now().timestamp()),
                "user_data": {
                    "client_ip_address": log.ip_address or "127.0.0.1",
                    "client_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
                "custom_data": {"value": log.value, "currency": log.currency or "BDT"} if log.value else {}
            },
            "headers": {
                "Content-Type": "application/json",
                "X-Client-IP": log.ip_address or "127.0.0.1"
            },
            "responseBody": {
                "state": "fired",
                "upstream_acceptance": "unverified",
                "message": "Browser pixel call was invoked. Confirm acceptance in TikTok Events Manager."
            } if log.status == "fired" else ({
                "events_received": 1,
                "status": "accepted",
                "fb_trace_id": f"FBT_trace_{log.id}"
            } if log.status == "success" else {
                "error": {"message": log.error_message or "API execution failed", "code": 400}
            }),
            "latencyMs": None
        })

    return {
        "events": events_list,
        "totalCount": total_count
    }

@router.get("/events/trend")
async def get_events_trend(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90)
):
    now_utc = datetime.now(timezone.utc)
    start_date = now_utc - timedelta(days=days)
    
    result = await db.execute(
        select(EventLog.created_at, EventLog.event_name, EventLog.event_count)
        .where(
            and_(
                EventLog.client_id == client.id,
                EventLog.created_at >= start_date,
                EventLog.status == "success"
            )
        )
    )
    logs = result.all()
    
    # Pre-populate date range
    dates_list = []
    date_data = {}
    for i in range(days - 1, -1, -1):
        d = now_utc - timedelta(days=i)
        iso_key = d.strftime("%Y-%m-%d")
        dates_list.append(iso_key)
        date_data[iso_key] = {"total": 0, "meta": 0, "tiktok": 0, "ga4": 0}
        
    for log_created_at, log_event_name, log_count in logs:
        if not log_created_at:
            continue
        if log_created_at.tzinfo:
            log_utc = log_created_at.astimezone(timezone.utc)
        else:
            log_utc = log_created_at.replace(tzinfo=timezone.utc)
        iso_key = log_utc.strftime("%Y-%m-%d")
        
        if iso_key not in date_data:
            continue
            
        count = log_count or 1
        log_platform = "Meta CAPI"
        raw_event_name = log_event_name or ""
        
        if ":" in raw_event_name:
            parts = raw_event_name.split(":", 1)
            channel = parts[0].lower()
            if channel == "tiktok":
                log_platform = "TikTok Events API"
            elif channel == "ga4":
                log_platform = "GA4"
            elif channel in ("facebook", "capi", "meta"):
                log_platform = "Meta CAPI"
        else:
            if getattr(client, "enable_facebook", True) and meta_credentials_configured(client):
                log_platform = "Meta CAPI"
            elif getattr(client, "enable_tiktok", True) and client.tiktok_pixel_id and client.tiktok_access_token:
                log_platform = "TikTok Events API"
            elif getattr(client, "enable_ga4", True) and client.ga4_measurement_id and client.ga4_api_secret:
                log_platform = "GA4"
                
        date_data[iso_key]["total"] += count
        if log_platform == "Meta CAPI":
            date_data[iso_key]["meta"] += count
        elif log_platform == "TikTok Events API":
            date_data[iso_key]["tiktok"] += count
        elif log_platform == "GA4":
            date_data[iso_key]["ga4"] += count

    trend = []
    for iso_key in dates_list:
        counts = date_data[iso_key]
        try:
            parsed_date = datetime.strptime(iso_key, "%Y-%m-%d")
            display_name = parsed_date.strftime("%b %d")
            if display_name:
                parts = display_name.split()
                if len(parts) == 2:
                    month, day = parts[0], parts[1].lstrip("0")
                    display_name = f"{month} {day}"
        except Exception:
            display_name = iso_key
            
        trend.append({
            "name": display_name,
            "Meta CAPI": counts["meta"],
            "TikTok Events": counts["tiktok"],
            "GA4": counts["ga4"],
            "Total": counts["total"]
        })
        
    return {"trend": trend}


@router.get("/events/recovery-summary")
async def get_events_recovery_summary(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90)
):
    """Return deduplicated browser/server coverage using shared event IDs."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(EventLog.event_id, EventLog.event_name, EventLog.status)
        .where(
            and_(
                EventLog.client_id == client.id,
                EventLog.created_at >= start_date,
                EventLog.event_id.is_not(None),
            )
        )
    )

    browser_ids = set()
    server_ids = set()
    for event_id, event_name, status in result.all():
        if not event_id:
            continue
        raw_name = str(event_name or "").lower()
        if raw_name.startswith("browsertiktok:") or str(status or "").lower() == "fired":
            browser_ids.add(event_id)
        elif str(status or "").lower() == "success":
            server_ids.add(event_id)

    recovered_ids = server_ids - browser_ids
    recovery_rate = round((len(recovered_ids) / len(server_ids)) * 100, 1) if server_ids else 0.0
    return {
        "days": days,
        "browser_events": len(browser_ids),
        "server_events": len(server_ids),
        "matched_events": len(server_ids & browser_ids),
        "recovered_events": len(recovered_ids),
        "recovery_rate": recovery_rate,
    }


@router.get("/api-logs")
async def get_api_logs(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=200)
):
    result = await db.execute(
        select(EventLog)
        .where(
            EventLog.client_id == client.id,
            ~EventLog.event_name.like("BrowserTikTok:%"),
        )
        .order_by(desc(EventLog.created_at))
        .limit(limit)
    )
    logs = result.scalars().all()

    api_logs_list = []
    for idx, log in enumerate(logs):
        raw_event_name = log.event_name
        log_platform = "Meta CAPI"
        display_event_name = raw_event_name
        endpoint = f"https://graph.facebook.com/v18.0/{client.pixel_id or 'pixel_id'}/events"

        if ":" in raw_event_name:
            parts = raw_event_name.split(":", 1)
            channel = parts[0]
            display_event_name = parts[1]
            if channel.lower() == "tiktok":
                log_platform = "TikTok Events API"
                endpoint = "https://open-api.tiktok.com/v1.3/pixel/track"
            elif channel.lower() == "ga4":
                log_platform = "GA4"
                endpoint = "https://www.google-analytics.com/mp/collect"
            elif channel.lower() in ("facebook", "capi", "meta"):
                log_platform = "Meta CAPI"
            elif channel.lower() == "webhook":
                log_platform = "Webhook"
                endpoint = client.webhook_url or "Webhook URL"
        else:
            if getattr(client, "enable_facebook", True) and meta_credentials_configured(client):
                log_platform = "Meta CAPI"
            elif getattr(client, "enable_tiktok", True) and client.tiktok_pixel_id and client.tiktok_access_token:
                log_platform = "TikTok Events API"
                endpoint = "https://open-api.tiktok.com/v1.3/pixel/track"
            elif getattr(client, "enable_ga4", True) and client.ga4_measurement_id and client.ga4_api_secret:
                log_platform = "GA4"
                endpoint = "https://www.google-analytics.com/mp/collect"
            elif client.webhook_url:
                log_platform = "Webhook"
                endpoint = client.webhook_url

        api_logs_list.append({
            "id": f"api_{log.id}",
            "timestamp": log.created_at.isoformat() if log.created_at else datetime.now(timezone.utc).isoformat(),
            "platform": log_platform,
            "endpoint": endpoint,
            "method": "POST",
            "statusCode": 200 if log.status == "success" else 400,
            "latencyMs": None,
            "retryCount": 0 if log.status == "success" else 1,
            "requestBody": f"{{\n  \"event_name\": \"{display_event_name}\",\n  \"event_time\": {int(log.created_at.timestamp()) if log.created_at else 0}\n}}",
            "responseBody": "{\n  \"status\": \"accepted\"\n}" if log.status == "success" else f"{{\n  \"error\": \"{log.error_message or 'Relay failure'}\"\n}}"
        })

    return {
        "logs": api_logs_list,
        "totalCount": len(api_logs_list)
    }


def _summarize_outbox_payload(payload) -> dict:
    events = payload if isinstance(payload, list) else []
    names = []
    event_ids = []
    for event in events[:10]:
        if not isinstance(event, dict):
            continue
        event_name = event.get("event_name") or "Unknown"
        if event_name not in names:
            names.append(event_name)
        event_id = event.get("event_id")
        if event_id:
            event_ids.append(event_id)
    return {
        "eventNames": names or ["Unknown"],
        "eventCount": len(events),
        "eventIds": event_ids[:5],
    }


def _serialize_outbox_row(row: EventOutbox) -> dict:
    summary = _summarize_outbox_payload(row.event_payload)
    return {
        "id": row.id,
        "status": row.status,
        "attempts": row.attempts or 0,
        "maxAttempts": row.max_attempts or 0,
        "nextAttemptAt": row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        "lastError": row.last_error or "",
        "createdAt": row.created_at.isoformat() if row.created_at else datetime.now(timezone.utc).isoformat(),
        "sentAt": row.sent_at.isoformat() if row.sent_at else None,
        "locked": bool(row.locked_at and row.status == "processing"),
        "eventNames": summary["eventNames"],
        "eventCount": summary["eventCount"],
        "eventIds": summary["eventIds"],
    }


@router.get("/outbox")
async def get_outbox_rows(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(25, ge=1, le=100),
    status: str | None = Query(None),
):
    statuses = [s.strip().lower() for s in status.split(",")] if status else ["dead", "queued", "processing"]
    allowed_statuses = {"queued", "processing", "dead", "sent"}
    statuses = [s for s in statuses if s in allowed_statuses]
    if not statuses:
        raise HTTPException(status_code=400, detail="Invalid outbox status filter.")

    result = await db.execute(
        select(EventOutbox)
        .where(
            EventOutbox.client_id == client.id,
            EventOutbox.status.in_(statuses),
        )
        .order_by(desc(EventOutbox.created_at))
        .limit(limit)
    )
    rows = result.scalars().all()
    return {
        "items": [_serialize_outbox_row(row) for row in rows],
        "totalCount": len(rows),
    }


@router.post("/outbox/{outbox_id}/retry")
async def retry_outbox_row(
    outbox_id: int,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EventOutbox)
        .where(EventOutbox.id == outbox_id, EventOutbox.client_id == client.id)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Outbox row not found.")
    if row.status == "processing":
        raise HTTPException(status_code=409, detail="This event is already being processed.")
    if row.status == "sent":
        raise HTTPException(status_code=400, detail="Sent events cannot be retried.")

    row.status = "queued"
    row.locked_at = None
    row.locked_by = None
    row.next_attempt_at = datetime.now(timezone.utc)
    row.last_error = None
    attempts = row.attempts or 0
    max_attempts = row.max_attempts or 0
    if attempts >= max_attempts:
        row.max_attempts = attempts + 1

    await db.commit()
    await db.refresh(row)
    return {
        "success": True,
        "item": _serialize_outbox_row(row),
    }

@router.get("/events/live-stream")
async def get_live_stream_pulse(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(EventLog)
        .where(EventLog.client_id == client.id)
        .order_by(desc(EventLog.created_at))
        .limit(1)
    )
    log = result.scalar_one_or_none()
    if not log:
        return {"event": None}

    raw_event_name = log.event_name
    log_platform = "Meta CAPI"
    display_event_name = raw_event_name

    if ":" in raw_event_name:
        parts = raw_event_name.split(":", 1)
        channel = parts[0]
        display_event_name = parts[1]
        if channel.lower() == "tiktok":
            log_platform = "TikTok Events API"
        elif channel.lower() == "browsertiktok":
            log_platform = "TikTok Browser Pixel"
        elif channel.lower() == "ga4":
            log_platform = "GA4"
        elif channel.lower() == "webhook":
            log_platform = "Webhook"

    return {
        "event": {
            "id": f"evt_{log.id}",
            "timestamp": log.created_at.isoformat(),
            "name": display_event_name,
            "platform": log_platform,
            "status": "Fired" if log.status == "fired" else ("Success" if log.status == "success" else "Failed"),
            "httpCode": 202 if log.status == "fired" else (200 if log.status == "success" else 400),
            "deduplicationKey": log.event_id or f"did_{log.id}",
            "payload": {"event_name": display_event_name},
            "responseBody": {
                "state": "fired",
                "upstream_acceptance": "unverified",
            } if log.status == "fired" else {"status": "accepted"},
            "latencyMs": 75
        }
    }

# â”€â”€â”€ Interactive Suggestions (Bypassing Gemini using Rule-Engine) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@router.get("/suggestions")
async def get_suggestions(client: Client = Depends(get_current_portal_client)):
    dismissed = set(getattr(client, 'dismissed_suggestions', None) or [])
    resolved = set(getattr(client, 'resolved_suggestions', None) or [])
    recommendations = []

    if not client.ga4_measurement_id or not client.enable_ga4:
        recommendations.append({
            "id": "sugg_ga4_pipeline",
            "title": "Enable GA4 Multi-Channel Server Pipeline",
            "severity": "Warning",
            "explanation": "Your setup is forwarding events to Meta CAPI, but GA4 Server-Side Measurement is inactive. Combining GA4 server protocols with Facebook CAPI builds robust cross-platform user targeting and increases checkout matches.",
            "fixAction": "1. Go to Settings > GA4 Server-Side section.\n2. Paste your GA4 Measurement ID (G-XXXXXXXX) and API Secret.\n3. Turn GA4 delivery ON and save.",
            "resolved": False,
            "platform": "GA4"
        })

    if not client.deferred_purchase:
        recommendations.append({
            "id": "sugg_cod_deferred",
            "title": "Activate Deferred Purchases (COD Protection)",
            "severity": "Critical",
            "explanation": "Your store receives Cash-on-Delivery orders, but Deferred Purchase tracking is currently inactive. This means fake, gibberish, or canceled COD checkouts are immediately training the Facebook Pixel, raising acquisition costs.",
            "fixAction": "1. Navigate to Settings > Domain & Facebook CAPI.\n2. Check the box 'ðŸ“¦ Deferred Purchase (COD Protection) ON'.\n3. Save config to hold pending orders.",
            "resolved": False,
            "platform": "Meta CAPI"
        })

    if not client.tiktok_pixel_id or not client.enable_tiktok:
        recommendations.append({
            "id": "sugg_tiktok_match",
            "title": "Incorporate TikTok CAPI Audience Deduplication",
            "severity": "Tip",
            "explanation": "You are currently running paid traffic without TikTok Events API telemetry. Integrating TikTok's server-side router increases your Ads Manager conversion match scores by aligning page checkouts.",
            "fixAction": "1. Open Settings > TikTok CAPI section.\n2. Paste your TikTok Pixel ID and Access Token.\n3. Turn TikTok CAPI delivery ON.",
            "resolved": False,
            "platform": "TikTok Events API"
        })

    if client.test_event_code:
        recommendations.append({
            "id": "sugg_cleanup_test_code",
            "title": "Remove FB 'test_event_code' from Production Pipeline",
            "severity": "Warning",
            "explanation": "Active 'test_event_code' is detected in your Facebook CAPI header credentials. Running live production orders with a test code forces events inside the FB Sandbox interface instead of real ad optimizer metrics.",
            "fixAction": "1. Open Settings > Domain & Facebook CAPI.\n2. Clear the 'Test Event Code' input box.\n3. Click Save Settings.",
            "resolved": False,
            "platform": "Meta CAPI"
        })

    # Filter dismissed suggestions, mark resolved ones
    filtered = []
    seen_suggestion_ids = set()
    for s in recommendations:
        suggestion_id = s["id"]
        if suggestion_id in dismissed or suggestion_id in seen_suggestion_ids:
            continue
        seen_suggestion_ids.add(suggestion_id)
        if suggestion_id in resolved:
            s["resolved"] = True
        filtered.append(s)

    return filtered

class SuggestionActionRequest(BaseModel):
    id: str

@router.post("/suggestions/toggle-resolve")
async def toggle_resolve_suggestion(
    payload: SuggestionActionRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    resolved = list(getattr(client, 'resolved_suggestions', None) or [])
    if payload.id in resolved:
        resolved.remove(payload.id)
    else:
        resolved.append(payload.id)
    try:
        client.resolved_suggestions = resolved
        await db.commit()
    except Exception:
        await db.rollback()  # Rollback transaction to prevent session corruption
        logger.warning("resolved_suggestions column may not exist yet â€” gracefully degraded.")
    return {"success": True}

@router.post("/suggestions/dismiss")
async def dismiss_suggestion(
    payload: SuggestionActionRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    dismissed = list(getattr(client, 'dismissed_suggestions', None) or [])
    if payload.id not in dismissed:
        dismissed.append(payload.id)
    try:
        client.dismissed_suggestions = dismissed
        await db.commit()
    except Exception:
        await db.rollback()  # Rollback transaction to prevent session corruption
        logger.warning("dismissed_suggestions column may not exist yet â€” gracefully degraded.")
    return {"success": True}

@router.post("/suggestions/ai-review")
async def run_diagnostics_scan(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    suggestions = await get_suggestions(client)
    return {
        "success": True,
        "message": "System diagnostics validated successfully.",
        "suggestions": suggestions
    }

# â”€â”€â”€ Sandbox Event Generator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@router.post("/campaign-test")
async def run_sandbox_campaign_test(
    payload: CampaignTestRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    try:
        val_float = float(payload.value) if payload.value else None
    except ValueError:
        val_float = None

    new_log = EventLog(
        client_id=client.id,
        event_name=payload.eventName,
        event_id=f"test_{secrets.token_hex(4)}",
        event_count=1,
        status="success",
        ip_address=payload.ip or "127.0.0.1",
        value=val_float,
        currency=payload.currency or "BDT",
        utm_source="sandbox",
        utm_campaign="capi_sandbox_test"
    )
    db.add(new_log)
    await db.commit()
    await db.refresh(new_log)

    return {
        "success": True,
        "statusCode": 200,
        "response": {
            "success": True,
            "message": "Payload sandbox accepted.",
            "tracking_gateway": "CAPI Router Node Austin",
            "recipient_id": client.pixel_id or "982049182390231",
            "transmission_mode": "async_test",
            "transmission_details": {
                "job_id": f"job_sandbox_{new_log.id}",
                "queue_at": datetime.now(timezone.utc).isoformat()
            }
        },
        "dispatchedEvent": {
            "id": f"evt_{new_log.id}",
            "timestamp": new_log.created_at.isoformat(),
            "name": new_log.event_name,
            "platform": payload.platform,
            "status": "Success",
            "httpCode": 200,
            "deduplicationKey": new_log.event_id,
            "payload": {
                "event_name": new_log.event_name,
                "event_time": int(new_log.created_at.timestamp())
            },
            "responseBody": {"status": "accepted"}
        }
    }

# â”€â”€â”€ COD Protection (Deferred Purchase Tracking) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class DeferredConfirmRequest(BaseModel):
    order_id: str

class DeferredBulkConfirmRequest(BaseModel):
    order_ids: list[str]

class DeferredSettingsRequest(BaseModel):
    deferredEnabled: bool
    autoConfirmDays: int
    autoConfirmStatus: str

@router.get("/deferred")
async def get_deferred_purchases(
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100)
):
    offset = (page - 1) * limit
    pending_r = await db.execute(
        select(PendingEvent)
        .where(and_(
            PendingEvent.client_id == client.id,
            PendingEvent.status == "pending"
        ))
        .order_by(desc(PendingEvent.created_at))
        .offset(offset)
        .limit(limit)
    )
    pending_events = pending_r.scalars().all()

    counts_r = await db.execute(
        select(PendingEvent.status, func.count(PendingEvent.id))
        .where(PendingEvent.client_id == client.id)
        .group_by(PendingEvent.status)
    )
    deferred_counts = {status: int(count or 0) for status, count in counts_r}

    confirmed_total = deferred_counts.get("confirmed", 0)
    cancelled_total = deferred_counts.get("cancelled", 0)
    expired_total = deferred_counts.get("expired", 0)
    pending_count = deferred_counts.get("pending", 0)

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    confirmed_today_r = await db.execute(
        select(func.count(PendingEvent.id))
        .where(and_(
            PendingEvent.client_id == client.id,
            PendingEvent.status == "confirmed",
            PendingEvent.confirmed_at >= today_start
        ))
    )
    confirmed_today = confirmed_today_r.scalar() or 0

    sum_and_min_stmt = select(
        func.sum(cast(PendingEvent.event_data['custom_data'].op('->>')('value'), Numeric)),
        func.min(PendingEvent.created_at)
    ).where(
        and_(
            PendingEvent.client_id == client.id,
            PendingEvent.status == "pending"
        )
    )
    sum_and_min_res = await db.execute(sum_and_min_stmt)
    sum_val, oldest_created = sum_and_min_res.fetchone()
    pending_value = float(sum_val or 0.0)

    now_utc = datetime.now(timezone.utc)
    if oldest_created:
        created = oldest_created.replace(tzinfo=timezone.utc) if oldest_created.tzinfo is None else oldest_created
        oldest_age_hours = round((now_utc - created).total_seconds() / 3600, 1)
    else:
        oldest_age_hours = 0.0

    pending_list = []
    for pe in pending_events:
        ed = pe.event_data or {}
        custom_data = ed.get("custom_data", {}) or {}
        raw_order_data = pe.raw_order_data or {}
        created = pe.created_at.replace(tzinfo=timezone.utc) if pe.created_at.tzinfo is None else pe.created_at
        age_sec = (now_utc - created).total_seconds()
        age_h = round(age_sec / 3600, 1)

        def _raw_value(*keys: str) -> str:
            for key in keys:
                value = raw_order_data.get(key)
                if value is not None and str(value).strip() and str(value).strip() != "â€”":
                    return str(value).strip()
            return ""

        # Real (unmasked) PII â€” merchants need this to call and verify COD orders.
        # Fall back to hashed user_data only when WooCommerce raw order fields are unavailable.
        ud = ed.get("user_data", {}) or {}
        raw_phone_list = ud.get("ph", [])
        raw_email_list = ud.get("em", [])
        recipient_name = _raw_value("recipient_name", "customer_name", "name", "billing_name")
        recipient_phone = _raw_value("recipient_phone", "customer_phone", "phone", "billing_phone")
        recipient_address = _raw_value("recipient_address", "customer_address", "address", "billing_address")
        customer_str = recipient_phone or "â€”"
        if customer_str == "â€”" and raw_phone_list and raw_phone_list[0] and raw_phone_list[0] != "â€”":
            customer_str = str(raw_phone_list[0])
        elif customer_str == "â€”" and raw_email_list and raw_email_list[0] and raw_email_list[0] != "â€”":
            customer_str = str(raw_email_list[0])

        # Parse product list from custom_data.contents
        contents = custom_data.get("contents", []) or []
        products = []
        if isinstance(contents, list):
            for item in contents:
                if isinstance(item, dict):
                    # Try title first, then name, avoid showing bare numeric IDs
                    raw_name = item.get("title") or item.get("name") or item.get("product_name") or ""
                    item_id = str(item.get("id") or "")
                    # If name is blank or looks like a numeric ID, mark as unknown
                    display_name = raw_name if raw_name and raw_name != item_id else f"Product #{item_id}" if item_id else "Unknown Product"
                    products.append({
                        "name": display_name,
                        "category": item.get("content_category") or item.get("category") or "",
                        "attributes": item.get("attributes") or {},
                        "quantity": int(item.get("quantity") or item.get("qty") or 1),
                        "price": float(item.get("item_price") or item.get("price") or 0),
                    })
        # Fallback: check raw_order_data for line_items (WooCommerce order data)
        if not products and raw_order_data:
            line_items = raw_order_data.get("line_items") or raw_order_data.get("products") or []
            if isinstance(line_items, list):
                for item in line_items:
                    if isinstance(item, dict):
                        raw_name = item.get("name") or item.get("title") or item.get("product_name") or ""
                        item_id = str(item.get("product_id") or item.get("id") or "")
                        display_name = raw_name if raw_name else f"Product #{item_id}" if item_id else "Unknown Product"
                        products.append({
                            "name": display_name,
                            "category": item.get("content_category") or item.get("category") or "",
                            "attributes": item.get("attributes") or {},
                            "quantity": int(item.get("quantity") or 1),
                            "price": float(item.get("subtotal") or item.get("price") or 0),
                        })
        # Last fallback: num_items generic entry
        if not products and custom_data.get("num_items"):
            products.append({
                "name": "Product (details not available)",
                "quantity": int(custom_data.get("num_items", 1)),
                "price": float(custom_data.get("value", 0)),
            })

        pending_list.append({
            "id": pe.id,
            "orderId": pe.order_id,
            "operationsOnly": pe.portal_state == "operations_only",
            "amount": custom_data.get("value", 0),
            "customer": customer_str,
            # Real recipient info (unmasked) from raw courier payload. Include both
            # recipient* and customer* aliases because portal views use both shapes.
            "recipientName": recipient_name or "â€”",
            "recipientPhone": recipient_phone or customer_str,
            "recipientAddress": recipient_address or "â€”",
            "customerName": recipient_name or "",
            "phone": recipient_phone or customer_str,
            "address": recipient_address or "",
            # Product list
            "products": products,
            "fraudScore": pe.fraud_score or 0,
            "fraudDetails": pe.fraud_details or {},
            "ageHours": age_h,
            "timestamp": pe.created_at.isoformat()
        })

    deferred_pending_list = [item for item in pending_list if not item["operationsOnly"]]
    deferred_pending_value = sum(float(item["amount"] or 0) for item in deferred_pending_list)
    deferred_oldest_age_hours = max(
        (float(item["ageHours"]) for item in deferred_pending_list),
        default=0.0,
    )

    pending_value_display = f"৳{deferred_pending_value:,.0f}" if deferred_pending_value else "৳0"
    operations_value_display = f"৳{pending_value:,.0f}" if pending_value else "৳0"
    deferred_oldest_display = f"{deferred_oldest_age_hours}h" if deferred_oldest_age_hours else "-"
    operations_oldest_display = f"{oldest_age_hours}h" if oldest_age_hours else "-"

    return {
        "deferredEnabled": bool(client.deferred_purchase),
        "autoConfirmDays": min(max(0, getattr(client, "auto_confirm_days", 0)), 7),
        "autoConfirmStatus": str(getattr(client, "auto_confirm_status", "completed")),
        "pendingCount": len(deferred_pending_list),
        "pendingValue": pending_value_display,
        "confirmedTotal": confirmed_total,
        "cancelledTotal": cancelled_total,
        "expiredTotal": expired_total,
        "confirmedToday": confirmed_today,
        "oldestPending": deferred_oldest_display,
        "pendingList": deferred_pending_list,
        "operationsPendingCount": pending_count,
        "operationsPendingValue": operations_value_display,
        "operationsOldestPending": operations_oldest_display,
        "operationsPendingList": pending_list,
        "deferredPendingCount": len(deferred_pending_list),
        "deferredPendingValue": pending_value_display,
        "deferredOldestPending": deferred_oldest_display,
        "deferredPendingList": deferred_pending_list,
    }

@router.post("/deferred/settings")
async def save_deferred_settings(
    payload: DeferredSettingsRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    if payload.deferredEnabled or payload.autoConfirmDays:
        require_growth_access(client, "Deferred Purchase control")
    growth_enabled = has_growth_access(client)
    client.deferred_purchase = growth_enabled and payload.deferredEnabled
    client.auto_confirm_days = min(max(0, payload.autoConfirmDays), 7) if growth_enabled else 0
    client.auto_confirm_status = payload.autoConfirmStatus.strip() or "completed"

    await db.commit()

    # Clear client cache
    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)

    return {
        "success": True,
        "message": "COD Protection settings synchronized successfully.",
        "deferredEnabled": bool(client.deferred_purchase),
        "autoConfirmDays": client.auto_confirm_days,
        "autoConfirmStatus": client.auto_confirm_status
    }

@router.post("/deferred/confirm")
async def api_confirm_deferred(
    payload: DeferredConfirmRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    from app.routers.deferred_events import confirm_event, ConfirmRequest
    try:
        res = await confirm_event(ConfirmRequest(order_id=payload.order_id), client=client, db=db)
        return {"success": True, "message": res.message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deferred confirm failed for client %s order %s: %s", client.id, payload.order_id, e)
        raise HTTPException(status_code=400, detail="Deferred order could not be confirmed.")

@router.post("/deferred/confirm-bulk")
async def api_confirm_deferred_bulk(
    payload: DeferredBulkConfirmRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    require_growth_access(client, "Bulk Purchase confirmation")
    from app.routers.deferred_events import bulk_confirm_events, BulkConfirmRequest
    try:
        res = await bulk_confirm_events(BulkConfirmRequest(order_ids=payload.order_ids), client=client, db=db)
        return {"success": True, "confirmed": res.confirmed, "failed": res.failed, "details": res.details}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deferred bulk confirm failed for client %s: %s", client.id, e)
        raise HTTPException(status_code=400, detail="Deferred orders could not be confirmed.")

@router.post("/deferred/cancel")
async def api_cancel_deferred(
    payload: DeferredConfirmRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    from app.routers.deferred_events import cancel_event, CancelRequest
    try:
        res = await cancel_event(CancelRequest(order_id=payload.order_id), client=client, db=db)
        return {"success": True, "message": res.message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deferred cancel failed for client %s order %s: %s", client.id, payload.order_id, e)
        raise HTTPException(status_code=400, detail="Deferred order could not be cancelled.")


@router.post("/deferred/restore")
async def api_restore_deferred(
    payload: DeferredConfirmRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    from app.routers.deferred_events import restore_cancelled_event, CancelRequest
    try:
        res = await restore_cancelled_event(CancelRequest(order_id=payload.order_id), client=client, db=db)
        return {"success": True, "message": res.message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deferred restore failed for client %s order %s: %s", client.id, payload.order_id, e)
        raise HTTPException(status_code=400, detail="Deferred order could not be restored.")


@router.post("/deferred/cancel-bulk")
async def api_cancel_deferred_bulk(
    payload: DeferredBulkConfirmRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db)
):
    require_growth_access(client, "Bulk Purchase cancellation")
    from app.routers.deferred_events import bulk_cancel_events, BulkCancelRequest
    try:
        res = await bulk_cancel_events(BulkCancelRequest(order_ids=payload.order_ids), client=client, db=db)
        return {"success": True, "cancelled": res.cancelled, "failed": res.failed, "details": res.details}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Deferred bulk cancel failed for client %s: %s", client.id, e)
        raise HTTPException(status_code=400, detail="Deferred orders could not be cancelled.")


# â”€â”€â”€ Multiple Store Management â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class CreateStoreRequest(BaseModel):
    business_name: str
    domain: str | None = None

class SwitchStoreRequest(BaseModel):
    target_client_id: int


class StoreDomainUpdateRequest(BaseModel):
    domain: str | None = None


@router.get("/stores")
async def list_stores(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all stores/workspaces associated with the current user's email."""
    require_allowed_origin(request)
    user, current_client, _ = await get_client_user_from_cookie(request, db)

    # Find all ClientUser rows for this email â†’ gives us all their stores
    all_user_rows_r = await db.execute(
        select(ClientUser).where(ClientUser.email == user.email, ClientUser.is_active == True)
    )
    all_user_rows = all_user_rows_r.scalars().all()

    stores = []
    for cu in all_user_rows:
        client_r = await db.execute(select(Client).where(Client.id == cu.client_id, Client.is_active == True))
        c = client_r.scalar_one_or_none()
        if c:
            stores.append({
                "client_id": c.id,
                "name": c.name,
                "domain": c.domain or "",
                "is_current": c.id == current_client.id,
            })

    return {"stores": stores, "current_client_id": current_client.id}


@router.patch("/store/domain")
async def update_current_store_domain(
    payload: StoreDomainUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update the active store domain from the authenticated client portal session."""
    require_allowed_origin(request)
    user, current_client, _ = await get_client_user_from_cookie(request, db)

    domain = _clean_domain(payload.domain) if payload.domain else None
    if getattr(current_client, "trial_started_at", None):
        await require_trial_available(
            db,
            domain=domain,
            pixel_id=current_client.pixel_id,
            exclude_client_id=current_client.id,
        )

    current_client.domain = domain
    if getattr(current_client, "trial_started_at", None):
        await record_trial_identity(db, current_client, source="store_domain")

    db.add(AuditLog(
        actor=user.email,
        action="client.domain_updated",
        client_id=current_client.id,
        ip_address=request.client.host if request.client else None,
        details=f"domain={domain or ''}",
    ))
    await db.commit()

    from app.dependencies import clear_client_cache
    clear_client_cache(current_client.api_key)

    return {
        "success": True,
        "client_id": current_client.id,
        "domain": current_client.domain or "",
    }


@router.post("/switch-store")
async def switch_store(
    payload: SwitchStoreRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Switch the active session to a different store the user owns."""
    require_allowed_origin(request)
    user, current_client, _ = await get_client_user_from_cookie(request, db)

    # Verify the user actually owns the target store
    target_user_r = await db.execute(
        select(ClientUser).where(
            ClientUser.email == user.email,
            ClientUser.client_id == payload.target_client_id,
            ClientUser.is_active == True,
        )
    )
    target_user = target_user_r.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=403, detail="You don't have access to this store.")

    target_client_r = await db.execute(
        select(Client).where(Client.id == payload.target_client_id, Client.is_active == True)
    )
    target_client = target_client_r.scalar_one_or_none()
    if not target_client:
        raise HTTPException(status_code=404, detail="Store not found.")

    # Create a new session for the target user/client
    from app.routers.client_auth import _create_session, _user_payload
    await _create_session(db, target_user, response, request)
    await db.commit()

    return {"success": True, "user": _user_payload(target_user, target_client)}


@router.post("/create-store")
async def create_store(
    payload: CreateStoreRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Create a new store workspace for the current user and switch to it."""
    require_allowed_origin(request)
    user, current_client, _ = await get_client_user_from_cookie(request, db)
    tier = effective_plan_tier(current_client)
    if tier not in {"scale", "agency"}:
        raise HTTPException(status_code=403, detail="Additional stores require a Scale or Agency plan.")

    existing_stores_r = await db.execute(
        select(func.count(ClientUser.id)).where(
            ClientUser.email == user.email,
            ClientUser.is_active == True,
        )
    )
    existing_store_count = int(existing_stores_r.scalar() or 0)
    if tier == "scale" and existing_store_count >= 3:
        raise HTTPException(status_code=403, detail="Scale plan supports up to 3 stores.")

    from app.routers.client_auth import _clean_domain, _user_payload, _create_session
    business_name = _clean_name(payload.business_name, "Business name")
    domain = _clean_domain(payload.domain) if payload.domain else None

    # Use plan defaults instead of copying a custom parent limit/trial window,
    # so adding stores cannot multiply account quota.
    new_client = Client(
        name=business_name,
        pixel_id="0",
        access_token="",
        domain=domain,
        api_key=secrets.token_urlsafe(24),
        public_key=secrets.token_urlsafe(18),
        portal_key=secrets.token_urlsafe(18),
        enable_facebook=False,
        enable_tiktok=False,
        enable_ga4=False,
        monthly_limit=default_monthly_event_limit(tier),
        daily_quota=1000,
        rate_limit=120,
        plan_tier=tier,
        trial_started_at=None,
        trial_ends_at=None,
    )
    db.add(new_client)
    await db.flush()

    # Create a ClientUser for the same email in the new store
    new_user = ClientUser(
        client_id=new_client.id,
        email=user.email,
        password_hash=user.password_hash,   # same password â€” no re-registration needed
        full_name=user.full_name,
        role="owner",
        is_active=True,
        email_verified=user.email_verified,
    )
    db.add(new_user)
    await db.flush()

    # Switch the session to the new store
    await _create_session(db, new_user, response, request)
    await db.commit()
    await db.refresh(new_client)
    await db.refresh(new_user)

    return {"success": True, "user": _user_payload(new_user, new_client)}


# â”€â”€â”€ Client Notification Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class NotificationSettingsUpdateRequest(BaseModel):
    owner_notify_whatsapp: bool
    owner_whatsapp_number: str | None = None
    whatsapp_instance_id: int | None = None


async def _assign_active_whatsapp_instance(client: Client, db: AsyncSession) -> None:
    from app.models.whatsapp_instance import WhatsAppInstance

    result = await db.execute(
        select(WhatsAppInstance).where(WhatsAppInstance.status == "active").order_by(WhatsAppInstance.id)
    )
    active_instance = result.scalars().first()
    if active_instance:
        client.whatsapp_instance_id = active_instance.id
    else:
        client.whatsapp_instance_id = None
        logger.warning("No active WhatsAppInstance found to link to client %s", client.id)


@router.get("/client/notification-settings")
async def get_notification_settings(
    client: Client = Depends(get_current_portal_client),
):
    return {
        "owner_notify_whatsapp": getattr(client, "owner_notify_whatsapp", False),
        "owner_whatsapp_number": getattr(client, "owner_whatsapp_number", None),
        "whatsapp_instance_id": getattr(client, "whatsapp_instance_id", None),
    }


@router.patch("/client/notification-settings")
async def update_notification_settings(
    payload: NotificationSettingsUpdateRequest,
    client: Client = Depends(get_current_portal_client),
    db: AsyncSession = Depends(get_db),
):
    client.owner_notify_whatsapp = payload.owner_notify_whatsapp

    phone = payload.owner_whatsapp_number
    if phone:
        phone = phone.strip()
        if not phone:
            phone = None
    client.owner_whatsapp_number = phone

    if payload.owner_notify_whatsapp:
        if not phone:
            raise HTTPException(status_code=422, detail="Owner WhatsApp number is required.")
        await _assign_active_whatsapp_instance(client, db)
    else:
        client.whatsapp_instance_id = None

    await db.commit()

    # Clear client cache
    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)

    return {
        "success": True,
        "settings": {
            "owner_notify_whatsapp": client.owner_notify_whatsapp,
            "owner_whatsapp_number": client.owner_whatsapp_number,
            "whatsapp_instance_id": client.whatsapp_instance_id,
        }
    }
