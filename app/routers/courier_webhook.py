import hmac
import html
import logging
import os
import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from urllib.parse import quote
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select

from app.security import decrypt_token

from app.database import get_db
from app.models.client import Client
from app.models.pending_event import PendingEvent
from app.models.courier_order import CourierOrder
from app.dependencies import _snapshot
from app.routers.deferred_events import _queue_confirmed_event
from app.services.courier_service import CourierService
from app.schemas.event import EventData
from app.dependencies import CachedClient
from app.services.event_worker import enqueue_events
from app.services.event_quality import boost_event_quality
from app.services.usage_service import check_and_reserve_usage
from app.services.plan_service import has_growth_access

logger = logging.getLogger(__name__)
router = APIRouter()

REQUIRE_COURIER_WEBHOOK_SECRET = os.getenv("REQUIRE_COURIER_WEBHOOK_SECRET", "true").lower() in ("1", "true", "yes")
COURIER_WEBHOOK_SECRET = os.getenv("COURIER_WEBHOOK_SECRET", "")
STEADFAST_BEARER_TOKEN = os.getenv("STEADFAST_BEARER_TOKEN", "")
PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET = os.getenv("PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET", "")


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    return ""


def _webhook_secret_from_request(request: Request, *, provider: str) -> str:
    provided = (
        request.headers.get("x-courier-webhook-secret")
        or request.headers.get("x-webhook-secret")
        or request.headers.get("x-buykori-webhook-secret")
        or request.headers.get("x-redx-webhook-secret")
        or _bearer_token(request)
        or ""
    ).strip()
    if provided:
        return provided

    legacy = (request.query_params.get("secret") or request.query_params.get("token") or "").strip()
    if legacy:
        logger.warning(
            "%s webhook authenticated with legacy query token. Move the secret to a header or Authorization bearer token.",
            provider,
        )
    return legacy


def _official_tracking_url(provider: str, tracking_code: str) -> Optional[str]:
    safe_tracking_code = quote(tracking_code, safe="")
    if provider == "steadfast":
        return f"https://portal.packzy.com/tracking/{safe_tracking_code}"
    if provider == "pathao":
        return "https://pathao.com/courier/tracking"
    if provider == "redx":
        return f"https://redx.com.bd/track-parcel/?trackingId={safe_tracking_code}"
    return None


@router.get("/track/{provider}/{tracking_code}", response_class=HTMLResponse, include_in_schema=False)
@router.get("/track/{tracking_code}", response_class=HTMLResponse, include_in_schema=False)
async def public_courier_tracking(
    tracking_code: str,
    provider: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Privacy-safe landing page for invoice QR scans.

    The QR contains the courier-issued unique ID. Public scans intentionally do
    not expose recipient contact details or the delivery address.
    """
    filters = [
        or_(
            CourierOrder.courier_tracking_id == tracking_code,
            CourierOrder.courier_order_id == tracking_code,
        )
    ]
    if provider:
        normalized_provider = provider.strip().lower()
        if normalized_provider not in {"steadfast", "pathao", "redx"}:
            raise HTTPException(status_code=404, detail="Courier provider not found")
        filters.append(CourierOrder.courier_provider == normalized_provider)
    result = await db.execute(
        select(CourierOrder)
        .where(*filters)
        .limit(2)
    )
    matches = result.scalars().all()
    courier_order = matches[0] if len(matches) == 1 else None

    if not courier_order:
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="en">
              <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Courier tracking</title></head>
              <body style="font-family:Arial,sans-serif;background:#f8fafc;color:#0f172a;padding:32px">
                <main style="max-width:560px;margin:auto;background:white;border:1px solid #e2e8f0;border-radius:16px;padding:24px">
                  <h1 style="margin-top:0">Tracking reference not found</h1>
                  <p>Please scan the QR code again or check the consignment ID printed on the invoice.</p>
                </main>
              </body>
            </html>
            """,
            status_code=404,
        )

    provider = html.escape((courier_order.courier_provider or "courier").title())
    raw_reference = (
        courier_order.courier_tracking_id
        or courier_order.courier_order_id
        or tracking_code
    )
    reference = html.escape(raw_reference)
    order_id = html.escape(courier_order.order_id or "N/A")
    status = html.escape((courier_order.courier_status or "pending").replace("_", " ").title())
    official_url = _official_tracking_url(courier_order.courier_provider, raw_reference)
    official_link = ""
    if official_url:
        safe_url = html.escape(official_url, quote=True)
        official_link = f"""
          <a href="{safe_url}" target="_blank" rel="noopener noreferrer"
             style="display:inline-block;margin-top:18px;padding:12px 18px;border-radius:10px;background:#4f46e5;color:white;text-decoration:none;font-weight:700">
            Open {provider} tracking
          </a>
        """

    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="en">
          <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Courier tracking #{reference}</title></head>
          <body style="font-family:Arial,sans-serif;background:#f8fafc;color:#0f172a;padding:24px">
            <main style="max-width:560px;margin:auto;background:white;border:1px solid #e2e8f0;border-radius:16px;padding:24px;box-shadow:0 12px 32px rgba(15,23,42,.08)">
              <p style="margin:0 0 8px;color:#4f46e5;font-weight:700">Buykori AdSync Courier Tracking</p>
              <h1 style="margin:0 0 20px">Consignment #{reference}</h1>
              <dl style="display:grid;grid-template-columns:140px 1fr;gap:12px;margin:0">
                <dt style="color:#64748b">Courier</dt><dd style="margin:0;font-weight:700">{provider}</dd>
                <dt style="color:#64748b">Order reference</dt><dd style="margin:0;font-weight:700">#{order_id}</dd>
                <dt style="color:#64748b">Current status</dt><dd style="margin:0;font-weight:700">{status}</dd>
              </dl>
              {official_link}
            </main>
          </body>
        </html>
        """
    )


def _verify_courier_webhook_secret(request: Request) -> None:
    if not REQUIRE_COURIER_WEBHOOK_SECRET:
        return
    provided = _webhook_secret_from_request(request, provider="Courier")
    if not COURIER_WEBHOOK_SECRET or not hmac.compare_digest(provided, COURIER_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid courier webhook secret")


def _verify_steadfast_bearer_token(request: Request, expected_token: str | None = None) -> None:
    authorization = request.headers.get("authorization") or ""
    scheme, _, provided = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not (expected_token or STEADFAST_BEARER_TOKEN)
        or not hmac.compare_digest(provided, expected_token or STEADFAST_BEARER_TOKEN)
    ):
        raise HTTPException(status_code=401, detail="Invalid SteadFast webhook bearer token")


# ─── Helper: Queue Refund event for worker delivery ──────────────────────────
async def _queue_refund_event(
    client: CachedClient,
    pending: PendingEvent,
    db: AsyncSession,
) -> dict:
    """
    Purchase event data ক্লোন করে event_name 'Refund'-এ পরিবর্তন করে outbox-এ পাঠায়।
    """
    event_dict = pending.event_data.copy()
    event_dict.pop("raw_order_data", None)
    event_dict["event_name"] = "Refund"
    event_dict["event_time"] = int(datetime.now(timezone.utc).timestamp())

    try:
        event = EventData(**event_dict)
    except Exception as e:
        logger.error(f"[{client.name}] Refund event parse error (order: {pending.order_id}): {e}")
        return {}

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
    logger.info(f"[{client.name}] Refund event queued for order {pending.order_id}")
    return event_dict

# ─── Common Status Processing Logic ──────────────────────────────────────────
def _append_status_history(
    courier_order: CourierOrder,
    *,
    mapped_status: str,
    raw_status: str,
    outcome: str = "applied",
    reason: str | None = None,
) -> None:
    history = list(courier_order.status_history or [])
    if not isinstance(history, list):
        history = []
    entry = {
        "status": mapped_status,
        "raw_status": str(raw_status),
        "outcome": outcome,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    if reason:
        entry["reason"] = reason
    history.append(entry)
    courier_order.status_history = history[-100:]


async def process_courier_status_change(
    db: AsyncSession,
    courier_order: CourierOrder,
    new_raw_status: str
) -> dict:
    provider = courier_order.courier_provider
    mapped_status = CourierService.map_status(provider, new_raw_status)
    old_status = courier_order.courier_status

    if not CourierService.is_known_status(provider, new_raw_status):
        logger.warning(
            "Ignoring unknown courier status provider=%s order=%s raw_status=%r",
            provider,
            courier_order.order_id,
            new_raw_status,
        )
        _append_status_history(
            courier_order,
            mapped_status=old_status,
            raw_status=new_raw_status,
            outcome="ignored",
            reason="unknown_status",
        )
        db.add(courier_order)
        return {"status": "ignored", "reason": "unknown_status", "current_status": old_status}

    if old_status == mapped_status:
        logger.info(
            "Ignoring duplicate courier status provider=%s order=%s status=%s",
            provider,
            courier_order.order_id,
            mapped_status,
        )
        return {"status": "duplicate", "current_status": old_status}

    if not CourierService.should_apply_status_transition(old_status, mapped_status):
        logger.warning(
            "Ignoring stale courier status provider=%s order=%s old=%s new=%s raw_status=%r",
            provider,
            courier_order.order_id,
            old_status,
            mapped_status,
            new_raw_status,
        )
        _append_status_history(
            courier_order,
            mapped_status=old_status,
            raw_status=new_raw_status,
            outcome="ignored",
            reason=f"stale_transition:{old_status}->{mapped_status}",
        )
        db.add(courier_order)
        return {"status": "ignored", "reason": "stale_transition", "current_status": old_status}

    # Update order details
    courier_order.courier_status = mapped_status

    # Append to history
    _append_status_history(
        courier_order,
        mapped_status=mapped_status,
        raw_status=new_raw_status,
    )

    client_result = await db.execute(select(Client).where(Client.id == courier_order.client_id))
    client = client_result.scalar_one_or_none()

    pending_event = None
    if courier_order.pending_event_id:
        pending_result = await db.execute(
            select(PendingEvent)
            .where(PendingEvent.id == courier_order.pending_event_id)
            .with_for_update()  # Race condition প্রতিরোধ — concurrent webhook retry-তে double event এড়াতে
        )
        pending_event = pending_result.scalar_one_or_none()

    if client and pending_event:
        client_snapshot = _snapshot(client)

        # 1. Handle DELIVERED (trigger auto Purchase event)
        if mapped_status == "delivered" and not courier_order.purchase_event_sent and has_growth_access(client):
            logger.info(f"Order {courier_order.order_id} delivered! Queueing auto Purchase event.")
            try:
                await _queue_confirmed_event(client_snapshot, pending_event, db)
                courier_order.purchase_event_sent = True
                courier_order.delivered_at = datetime.now(timezone.utc)
                pending_event.status = "confirmed"
                pending_event.confirmed_at = datetime.now(timezone.utc)  # analytics-এর জন্য জরুরি
                pending_event.portal_state = "confirmed"
                pending_event.is_confirmed = True
            except Exception as e:
                logger.error(f"Failed to queue auto Purchase event for delivered order {courier_order.order_id}: {e}")

        # 2. Handle RETURNED or CANCELLED (trigger Refund event to Facebook)
        elif mapped_status in ("returned", "cancelled"):
            # Refund event goes if it was already delivered/purchased, or if we want to log the cancel event.
            # Usually, Facebook expects Refund for already sent Purchases.
            if has_growth_access(client) and courier_order.purchase_event_sent and not courier_order.refund_event_sent:
                logger.info(f"Order {courier_order.order_id} was returned/cancelled after delivery. Queueing Refund event.")
                try:
                    await _queue_refund_event(client_snapshot, pending_event, db)
                    courier_order.refund_event_sent = True
                except Exception as e:
                    logger.error(f"Failed to queue Refund event for order {courier_order.order_id}: {e}")

            pending_event.status = "cancelled"
            pending_event.portal_state = "cancelled"

    db.add(courier_order)
    if pending_event:
        db.add(pending_event)
    return {"status": "applied", "previous_status": old_status, "current_status": mapped_status}

# ─── Webhooks ────────────────────────────────────────────────────────────────

@router.post("/v1/webhook/steadfast")
async def steadfast_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    SteadFast Courier Webhook Endpoint.
    """
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    tracking_code = payload.get("tracking_code")
    consignment_id = payload.get("consignment_id")
    status = payload.get("status")
    invoice = payload.get("invoice")

    if not (tracking_code or consignment_id) or not status:
        logger.warning(f"SteadFast webhook received invalid data: {payload}")
        return {"status": "ignored", "reason": "missing consignment_id/tracking_code or status"}

    logger.info(
        "SteadFast webhook received for consignment=%s tracking=%s: status=%s, invoice=%s",
        consignment_id,
        tracking_code,
        status,
        invoice,
    )

    # Find matching courier order
    identifier_filters = []
    if tracking_code:
        identifier_filters.append(CourierOrder.courier_tracking_id == str(tracking_code))
    if consignment_id:
        identifier_filters.append(CourierOrder.courier_order_id == str(consignment_id))

    result = await db.execute(
        select(CourierOrder)
        .where(
            CourierOrder.courier_provider == "steadfast",
            or_(*identifier_filters),
        )
        .with_for_update()
    )
    courier_order = result.scalar_one_or_none()

    if not courier_order:
        logger.warning(
            "SteadFast courier order not found for consignment=%s tracking=%s",
            consignment_id,
            tracking_code,
        )
        return {"status": "ignored", "reason": "order not found"}

    client_result = await db.execute(select(Client).where(Client.id == courier_order.client_id))
    client = client_result.scalar_one_or_none()
    client_token = decrypt_token(client.steadfast_webhook_token) if client and client.steadfast_webhook_token else None
    _verify_steadfast_bearer_token(request, client_token)

    processing = await process_courier_status_change(db, courier_order, status)
    await db.commit()

    return processing

@router.post("/v1/webhook/pathao")
async def pathao_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Pathao Courier Webhook Endpoint.

    Pathao Webhook ভেরিফিকেশন প্রক্রিয়া:
    1. Pathao Merchant Panel-এ Callback URL সেভ করলে Pathao একটি ভেরিফিকেশন POST পাঠায়:
       payload: {"event": "webhook_integration"}
       header: X-PATHAO-Signature: <configured_webhook_secret_verbatim>
    2. আমাদের সার্ভারকে 202 status এবং response header দিতে হয়:
       X-Pathao-Merchant-Webhook-Integration-Secret: <configured_webhook_secret_verbatim>
    3. এরপর থেকে Pathao HMAC-SHA256 signature দিয়ে real status update পাঠায়।

    Note: Pathao X-PATHAO-Signature header-এ configured secret verbatim পাঠায়
    (hashed HMAC নয়)। তাই আমরা প্রথমে verbatim check করি, তারপর HMAC fallback।
    """
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    # ─── ধাপ ১: Pathao Webhook Integration ভেরিফিকেশন হ্যান্ডশেক ───────────────
    # Pathao প্রথমবার Callback URL সেভ করলে এই event পাঠায়।
    # সঠিকভাবে respond না করলে webhook save হয় না।
    event_type = payload.get("event") or payload.get("type") or ""
    received_sig = request.headers.get("x-pathao-signature") or request.headers.get("x-signature") or ""
    integration_response_secret = PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET

    def pathao_response(content: dict, *, status_code: int = 200, secret: str | None = None) -> JSONResponse:
        response = JSONResponse(status_code=status_code, content=content)
        if secret:
            response.headers["X-Pathao-Merchant-Webhook-Integration-Secret"] = secret
        return response

    if event_type == "webhook_integration":
        if not PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET:
            raise HTTPException(status_code=500, detail="Pathao integration secret not configured")
        if not received_sig or not hmac.compare_digest(received_sig, PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET):
            raise HTTPException(status_code=401, detail="Invalid Pathao integration signature")
        logger.info("Pathao webhook_integration handshake received — sending verification response")
        # Pathao expects us to echo back the secret in the response header
        return pathao_response(
            secret=PATHAO_MERCHANT_WEBHOOK_INTEGRATION_SECRET,
            status_code=202,
            content={"status": "verified", "message": "Webhook integration verified"}
        )

    # ─── ধাপ ২: Normal status update ────────────────────────────────────────────
    consignment_id = payload.get("consignment_id")
    status = payload.get("status") or {
        "order.created": "pending",
        "order.updated": "pending",
        "pickup.requested": "pickup_requested",
        "assigned.for.pickup": "assigned_for_pickup",
        "pickup": "picked_up",
        "in.transit": "in_transit",
        "order.delivered": "delivered",
        "return": "returned",
        "paid.return": "returned",
        "pickup.cancelled": "cancelled",
        "delivery.failed": "delivery_failed",
    }.get(str(event_type).strip().lower())
    merchant_order_id = payload.get("merchant_order_id")

    if not consignment_id or not status:
        logger.warning(f"Pathao webhook received invalid data: {payload}")
        return pathao_response(
            {"status": "ignored", "reason": "missing consignment_id or status"},
            secret=integration_response_secret,
        )

    logger.info(f"Pathao webhook received for {consignment_id}: status={status}")

    # Find matching courier order
    result = await db.execute(
        select(CourierOrder)
        .where(
            CourierOrder.courier_provider == "pathao",
            CourierOrder.courier_order_id == str(consignment_id),
        )
        .with_for_update()
    )
    courier_order = result.scalar_one_or_none()

    if not courier_order:
        logger.warning(f"Pathao courier order not found for consignment: {consignment_id}")
        return pathao_response(
            {"status": "ignored", "reason": "order not found"},
            secret=integration_response_secret,
        )

    # Get associated client and decrypt secret key
    client_result = await db.execute(select(Client).where(Client.id == courier_order.client_id))
    client = client_result.scalar_one_or_none()
    if not client or not client.pathao_webhook_secret:
        raise HTTPException(status_code=401, detail="Client Pathao webhook secret not configured")

    try:
        raw_secret = decrypt_token(client.pathao_webhook_secret, allow_legacy_plaintext=True)
        # Webhook integration secret is separate from the Pathao API credentials.
        webhook_secret = raw_secret
    except Exception:
        raise HTTPException(status_code=401, detail="Failed to decrypt client Pathao webhook secret")

    if not received_sig:
        raise HTTPException(status_code=401, detail="Missing signature header")

    # ─── Signature ভেরিফিকেশন ──────────────────────────────────────────────────
    # Pathao-র actual mechanism: X-PATHAO-Signature header-এ configured secret verbatim থাকে।
    # তাই আমরা প্রথমে verbatim compare করি।
    verbatim_match = hmac.compare_digest(
        webhook_secret.encode("utf-8"),
        received_sig.encode("utf-8")
    )

    if not verbatim_match:
        # Fallback: কিছু implementation HMAC-SHA256 ব্যবহার করে — সেটিও চেক করি
        computed_hmac = hmac.new(
            webhook_secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(computed_hmac, received_sig):
            logger.warning(f"Pathao webhook signature mismatch for consignment {consignment_id}")
            raise HTTPException(status_code=401, detail="Invalid signature")

    client.pathao_webhook_verified_at = datetime.now(timezone.utc)
    db.add(client)
    processing = await process_courier_status_change(db, courier_order, status)
    await db.commit()

    # Pathao expects the integration secret in response header for every webhook call too
    return pathao_response(processing, secret=webhook_secret)


@router.post("/v1/webhook/redx")
async def redx_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive RedX parcel status callbacks."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    tracking_id = payload.get("tracking_number")
    status = payload.get("status")
    if not tracking_id or not status:
        return {"status": "ignored", "reason": "missing tracking_number or status"}

    result = await db.execute(
        select(CourierOrder)
        .where(
            CourierOrder.courier_provider == "redx",
            CourierOrder.courier_tracking_id == str(tracking_id),
        )
        .with_for_update()
    )
    courier_order = result.scalar_one_or_none()
    if not courier_order:
        return {"status": "ignored", "reason": "order not found"}

    client_result = await db.execute(select(Client).where(Client.id == courier_order.client_id))
    client = client_result.scalar_one_or_none()
    if client and client.redx_webhook_secret:
        provided = _webhook_secret_from_request(request, provider="RedX")
        expected = decrypt_token(client.redx_webhook_secret)
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Invalid courier webhook secret")
    else:
        _verify_courier_webhook_secret(request)

    processing = await process_courier_status_change(db, courier_order, str(status))
    await db.commit()
    return processing
