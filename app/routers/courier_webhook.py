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

logger = logging.getLogger(__name__)
router = APIRouter()

REQUIRE_COURIER_WEBHOOK_SECRET = os.getenv("REQUIRE_COURIER_WEBHOOK_SECRET", "true").lower() in ("1", "true", "yes")
COURIER_WEBHOOK_SECRET = os.getenv("COURIER_WEBHOOK_SECRET", "")


def _official_tracking_url(provider: str, tracking_code: str) -> Optional[str]:
    safe_tracking_code = quote(tracking_code, safe="")
    if provider == "steadfast":
        return f"https://portal.packzy.com/tracking/{safe_tracking_code}"
    if provider == "pathao":
        return "https://pathao.com/courier/tracking"
    if provider == "redx":
        return f"https://redx.com.bd/track-parcel/?trackingId={safe_tracking_code}"
    return None


@router.get("/track/{tracking_code}", response_class=HTMLResponse, include_in_schema=False)
async def public_courier_tracking(
    tracking_code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Privacy-safe landing page for invoice QR scans.

    The QR contains the courier-issued unique ID. Public scans intentionally do
    not expose recipient contact details or the delivery address.
    """
    result = await db.execute(
        select(CourierOrder).where(
            or_(
                CourierOrder.courier_tracking_id == tracking_code,
                CourierOrder.courier_order_id == tracking_code,
            )
        )
    )
    courier_order = result.scalar_one_or_none()

    if not courier_order:
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="en">
              <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Courier tracking</title></head>
              <body style="font-family:Arial,sans-serif;background:#f8fafc;color:#0f172a;padding:32px">
                <main style="max-width:560px;margin:auto;background:white;border:1px solid #e2e8f0;border-radius:16px;padding:24px">
                  <h1 style="margin-top:0">Tracking reference not found</h1>
                  <p>Please check the consignment ID printed on the invoice.</p>
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
    provided = (
        request.headers.get("x-courier-webhook-secret")
        or request.headers.get("x-webhook-secret")
        or request.query_params.get("secret")
        or ""
    )
    if not COURIER_WEBHOOK_SECRET or not hmac.compare_digest(provided, COURIER_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid courier webhook secret")

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
async def process_courier_status_change(
    db: AsyncSession,
    courier_order: CourierOrder,
    new_raw_status: str
) -> None:
    provider = courier_order.courier_provider
    mapped_status = CourierService.map_status(provider, new_raw_status)
    old_status = courier_order.courier_status

    if old_status == mapped_status:
        return # No change

    # Update order details
    courier_order.courier_status = mapped_status

    # Append to history
    history = courier_order.status_history or []
    if not isinstance(history, list):
        history = []
    history.append({
        "status": mapped_status,
        "raw_status": new_raw_status,
        "time": datetime.now(timezone.utc).isoformat()
    })
    courier_order.status_history = history

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
        if mapped_status == "delivered" and not courier_order.purchase_event_sent:
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
            if courier_order.purchase_event_sent and not courier_order.refund_event_sent:
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

# ─── Webhooks ────────────────────────────────────────────────────────────────

@router.post("/v1/webhook/steadfast")
async def steadfast_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    SteadFast Courier Webhook Endpoint.
    """
    _verify_courier_webhook_secret(request)
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    tracking_code = payload.get("tracking_code")
    status = payload.get("status")
    invoice = payload.get("invoice")

    if not tracking_code or not status:
        logger.warning(f"SteadFast webhook received invalid data: {payload}")
        return {"status": "ignored", "reason": "missing tracking_code or status"}

    logger.info(f"SteadFast webhook received for {tracking_code}: status={status}, invoice={invoice}")

    # Find matching courier order
    result = await db.execute(
        select(CourierOrder).where(CourierOrder.courier_tracking_id == str(tracking_code))
    )
    courier_order = result.scalar_one_or_none()

    if not courier_order:
        logger.warning(f"SteadFast courier order not found for tracking: {tracking_code}")
        return {"status": "ignored", "reason": "order not found"}

    # Get associated client and decrypt secret key to verify HMAC-SHA256 signature
    client_result = await db.execute(select(Client).where(Client.id == courier_order.client_id))
    client = client_result.scalar_one_or_none()
    if not client or not client.steadfast_secret_key:
        raise HTTPException(status_code=401, detail="Client steadfast secret key not configured")

    try:
        decrypted_secret = decrypt_token(client.steadfast_secret_key, allow_legacy_plaintext=True)
    except Exception:
        raise HTTPException(status_code=401, detail="Failed to decrypt client secret key")

    received_sig = request.headers.get("x-steadfast-signature") or request.headers.get("x-signature")
    if not received_sig:
        raise HTTPException(status_code=401, detail="Missing signature header")

    computed_sig = hmac.new(
        decrypted_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_sig, received_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    await process_courier_status_change(db, courier_order, status)
    await db.commit()

    return {"status": "success"}

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
    _verify_courier_webhook_secret(request)
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

    if event_type == "webhook_integration":
        logger.info("Pathao webhook_integration handshake received — sending verification response")
        # Pathao expects us to echo back the secret in the response header
        response = JSONResponse(
            status_code=202,
            content={"status": "verified", "message": "Webhook integration verified"}
        )
        response.headers["X-Pathao-Merchant-Webhook-Integration-Secret"] = received_sig
        return response

    # ─── ধাপ ২: Normal status update ────────────────────────────────────────────
    consignment_id = payload.get("consignment_id")
    status = payload.get("status")
    merchant_order_id = payload.get("merchant_order_id")

    if not consignment_id or not status:
        logger.warning(f"Pathao webhook received invalid data: {payload}")
        return {"status": "ignored", "reason": "missing consignment_id or status"}

    logger.info(f"Pathao webhook received for {consignment_id}: status={status}")

    # Find matching courier order
    result = await db.execute(
        select(CourierOrder).where(CourierOrder.courier_order_id == str(consignment_id))
    )
    courier_order = result.scalar_one_or_none()

    if not courier_order:
        logger.warning(f"Pathao courier order not found for consignment: {consignment_id}")
        return {"status": "ignored", "reason": "order not found"}

    # Get associated client and decrypt secret key
    client_result = await db.execute(select(Client).where(Client.id == courier_order.client_id))
    client = client_result.scalar_one_or_none()
    if not client or not client.pathao_secret_key:
        raise HTTPException(status_code=401, detail="Client pathao secret key not configured")

    try:
        raw_secret = decrypt_token(client.pathao_secret_key, allow_legacy_plaintext=True)
        # pathao_secret_key format: "client_secret|password" — শুধু client_secret দিয়ে verify হবে
        client_secret = raw_secret.split("|", 1)[0] if "|" in raw_secret else raw_secret
    except Exception:
        raise HTTPException(status_code=401, detail="Failed to decrypt client pathao secret key")

    if not received_sig:
        raise HTTPException(status_code=401, detail="Missing signature header")

    # ─── Signature ভেরিফিকেশন ──────────────────────────────────────────────────
    # Pathao-র actual mechanism: X-PATHAO-Signature header-এ configured secret verbatim থাকে।
    # তাই আমরা প্রথমে verbatim compare করি।
    verbatim_match = hmac.compare_digest(
        client_secret.encode("utf-8"),
        received_sig.encode("utf-8")
    )

    if not verbatim_match:
        # Fallback: কিছু implementation HMAC-SHA256 ব্যবহার করে — সেটিও চেক করি
        computed_hmac = hmac.new(
            client_secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(computed_hmac, received_sig):
            logger.warning(f"Pathao webhook signature mismatch for consignment {consignment_id}")
            raise HTTPException(status_code=401, detail="Invalid signature")

    await process_courier_status_change(db, courier_order, status)
    await db.commit()

    # Pathao expects the integration secret in response header for every webhook call too
    response = JSONResponse(status_code=200, content={"status": "success"})
    response.headers["X-Pathao-Merchant-Webhook-Integration-Secret"] = client_secret
    return response


@router.post("/v1/webhook/redx")
async def redx_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive RedX parcel status callbacks."""
    _verify_courier_webhook_secret(request)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    tracking_id = payload.get("tracking_number")
    status = payload.get("status")
    if not tracking_id or not status:
        return {"status": "ignored", "reason": "missing tracking_number or status"}

    result = await db.execute(
        select(CourierOrder).where(CourierOrder.courier_tracking_id == str(tracking_id))
    )
    courier_order = result.scalar_one_or_none()
    if not courier_order:
        return {"status": "ignored", "reason": "order not found"}

    await process_courier_status_change(db, courier_order, str(status))
    await db.commit()
    return {"status": "success"}
