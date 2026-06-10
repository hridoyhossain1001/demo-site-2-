"""
WooCommerce Webhook Router
────────────────────────────
WordPress-এর বিল্ট-ইন Webhook সিস্টেম দিয়ে অর্ডার স্ট্যাটাস চেঞ্জ রিসিভ করে।
প্লাগইন ব্যবহার না করলেও এই endpoint দিয়ে Deferred Purchase কনফার্ম করে
durable outbox queue-তে পাঠানো যায়।

Endpoints:
  POST /api/v1/webhook/woocommerce  — WooCommerce Webhook রিসিভ করে
"""

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.client import Client
from app.models.pending_event import PendingEvent
from app.schemas.event import EventData
from app.services.event_quality import boost_event_quality
from app.services.event_worker import enqueue_events
from app.services.usage_service import check_and_reserve_usage
from app.services.identity import hash_phone
from app.dependencies import CachedClient, _snapshot
from app.routers.deferred_events import _auto_book_courier_for_pending, _queue_confirmed_event
from app.security import decrypt_token
from app.limiter import _get_real_ip

logger = logging.getLogger(__name__)
router = APIRouter()

REQUIRE_WC_WEBHOOK_SIGNATURE = os.getenv("REQUIRE_WC_WEBHOOK_SIGNATURE", "true").lower() in ("1", "true", "yes")
ALLOW_WC_WEBHOOK_API_KEY_SECRET_FALLBACK = os.getenv(
    "ALLOW_WC_WEBHOOK_API_KEY_SECRET_FALLBACK",
    "false",
).lower() in ("1", "true", "yes")
ALLOW_SHOPIFY_PLAINTEXT_SHARED_SECRET = os.getenv(
    "ALLOW_SHOPIFY_PLAINTEXT_SHARED_SECRET",
    "false",
).lower() in ("1", "true", "yes")


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    return ""


def _client_api_key_from_request(request: Request, *, provider: str) -> str:
    api_key = (
        request.headers.get("x-api-key")
        or request.headers.get("x-buykori-api-key")
        or _bearer_token(request)
        or ""
    ).strip()
    if api_key:
        return api_key

    legacy_key = (request.query_params.get("key") or "").strip()
    if legacy_key:
        logger.warning(
            "%s webhook authenticated with legacy query key. Move the secret to X-API-Key or Authorization header.",
            provider,
        )
    return legacy_key


def _verify_wc_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify WooCommerce X-WC-Webhook-Signature (base64 HMAC-SHA256)."""
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def _verify_shopify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify Shopify X-Shopify-Hmac-Sha256 (base64 HMAC-SHA256)."""
    if not signature or not secret:
        return False
    try:
        decrypted_secret = decrypt_token(secret)
    except Exception as exc:
        if not ALLOW_SHOPIFY_PLAINTEXT_SHARED_SECRET:
            logger.warning(f"Shopify shared secret decrypt failed; plaintext fallback disabled: {exc}")
            return False
        logger.warning("Shopify shared secret decrypt failed; using legacy plaintext fallback.")
        decrypted_secret = secret
    digest = hmac.new(decrypted_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


async def _get_client_by_api_key(api_key: str, db: AsyncSession):
    """API Key দিয়ে ক্লায়েন্ট খুঁজে বের করে।"""
    result = await db.execute(
        select(Client).where(
            and_(Client.api_key == api_key, Client.is_active == True)
        )
    )
    return result.scalar_one_or_none()


@router.post(
    "/webhook/woocommerce",
    summary="WooCommerce Webhook Receiver",
    description="WooCommerce order status change webhook রিসিভ করে এবং pending Purchase event কনফার্ম করে।",
)
async def woocommerce_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    WooCommerce Webhook Format:
    Header: X-WC-Webhook-Topic: order.updated / order.completed
    Header: X-API-Key: <client_api_key>
    Body: Full WooCommerce order JSON
    """

    # ─── API Key Authentication ───────────────────────────────────────
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key missing")

    client_row = await _get_client_by_api_key(api_key, db)
    if not client_row:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    client = _snapshot(client_row)
    raw_body = await request.body()
    signature = request.headers.get("x-wc-webhook-signature", "")
    webhook_secret = os.getenv("WC_WEBHOOK_SECRET", "")
    if not webhook_secret and ALLOW_WC_WEBHOOK_API_KEY_SECRET_FALLBACK:
        logger.warning("Using legacy WooCommerce webhook API-key signature fallback.")
        webhook_secret = api_key
    if REQUIRE_WC_WEBHOOK_SIGNATURE and not webhook_secret:
        raise HTTPException(status_code=401, detail="WooCommerce webhook secret is not configured")
    if REQUIRE_WC_WEBHOOK_SIGNATURE and not _verify_wc_signature(raw_body, signature, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid WooCommerce webhook signature")

    # ─── Parse Webhook Body ───────────────────────────────────────────
    try:
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # WooCommerce sends order data directly
    order_id = body.get("id") or body.get("order_id")
    status = body.get("status", "")

    if not order_id:
        raise HTTPException(status_code=400, detail="Order ID not found in payload")

    # Webhook Replay Protection
    from app.services.redis_pool import get_redis
    redis = get_redis()
    if redis:
        webhook_id = request.headers.get("x-wc-webhook-id") or f"wc:{client.id}:{order_id}:{status}"
        try:
            is_new = await redis.set(f"webhook_replay:{webhook_id}", "1", ex=86400, nx=True)
            if not is_new:
                logger.info(f"[{client.name}] WooCommerce duplicate webhook ignored: {webhook_id}")
                return {"status": "ignored", "detail": "Duplicate webhook delivery"}
        except Exception as re_exc:
            logger.warning(f"[{client.name}] Redis WooCommerce replay check failed: {re_exc}")

    logger.info(f"[{client.name}] 📬 WooCommerce webhook: order #{order_id}, status: {status}")

    # ─── Only process completed/processing/courier_booked orders ──────
    status_clean = status.lower().replace("wc-", "")
    if status_clean not in ("completed", "processing", "courier_booked", "courier-booked"):
        return {
            "status": "ignored",
            "message": f"Order #{order_id} status '{status}' — no action needed",
        }

    # ─── Find pending event for this order ────────────────────────────
    # Try both 'wc_purchase_<id>' (plugin format) and raw order_id
    possible_ids = [f"wc_purchase_{order_id}", str(order_id)]

    pending = None
    for pid in possible_ids:
        result = await db.execute(
            select(PendingEvent).where(
                and_(
                    PendingEvent.client_id == client.id,
                    PendingEvent.order_id == pid,
                    PendingEvent.status == "pending",
                )
            ).with_for_update()
        )
        pending = result.scalar_one_or_none()
        if pending:
            break

    if not pending:
        confirmed_result = await db.execute(
            select(PendingEvent).where(
                and_(
                    PendingEvent.client_id == client.id,
                    PendingEvent.order_id.in_(possible_ids),
                    PendingEvent.status.in_(["confirmed", "courier_booking_queued", "courier_booked"]),
                )
            )
        )
        if confirmed_result.scalar_one_or_none():
            return {
                "status": "success",
                "order_id": str(order_id),
                "message": "Purchase event was already confirmed or booked with courier.",
            }
        return {
            "status": "skipped",
            "message": f"No pending Purchase event found for order #{order_id}",
        }

    if pending.portal_state == "operations_only":
        return {
            "status": "ignored",
            "order_id": str(order_id),
            "message": "Purchase event was already queued. Order remains available for manual courier booking.",
        }

    booking = await _auto_book_courier_for_pending(client.id, pending, db)
    if booking["mode"] in {"queued", "already_booked"}:
        message = booking["message"]
    elif booking["mode"] == "not_configured":
        try:
            await _queue_confirmed_event(client, pending, db)
        except HTTPException:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            logger.error(f"[{client.name}] Webhook confirm queue failed (order #{order_id}): {e}")
            raise HTTPException(
                status_code=500,
                detail="Purchase event queue failed.",
            ) from None
        pending.status = "confirmed"
        pending.portal_state = "confirmed"
        pending.is_confirmed = True
        pending.confirmed_at = datetime.now(timezone.utc)
        message = "Purchase event confirmed and queued for delivery!"
    else:
        await db.rollback()
        raise HTTPException(status_code=400, detail=booking["message"])

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"[{client.name}] WooCommerce webhook commit failed (order #{order_id}): {e}")
        raise HTTPException(
            status_code=500,
            detail="Webhook confirmation could not be saved.",
        )

    logger.info(f"[{client.name}] Webhook confirmed order #{order_id}: {message}")

    return {
        "status": "success",
        "order_id": str(order_id),
        "message": message,
    }


@router.post(
    "/webhook/shopify",
    summary="Shopify Webhook Receiver",
    description="Shopify order creation/paid webhook রিসিভ করে এবং pending event কনফার্ম করে অথবা নতুন Purchase event queue করে।",
)
async def shopify_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Shopify Webhook Handler:
    URL: /api/v1/webhook/shopify?key=<client_api_key>
    Header: X-Shopify-Hmac-Sha256: <base64 signature>
    Header: X-Shopify-Topic: orders/create or orders/paid
    """
    # ─── API Key Authentication ───────────────────────────────────────
    api_key = _client_api_key_from_request(request, provider="Shopify")
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key missing")

    client_row = await _get_client_by_api_key(api_key, db)
    if not client_row:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    client = _snapshot(client_row)
    raw_body = await request.body()

    # ─── Shopify Signature Validation ───────────────────────────────
    signature = request.headers.get("x-shopify-hmac-sha256", "")
    if not client.shopify_shared_secret:
        raise HTTPException(status_code=403, detail="Shopify webhook secret is not configured")
    if not signature or not _verify_shopify_signature(raw_body, signature, client.shopify_shared_secret):
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature")

    # ─── Parse Webhook Body ───────────────────────────────────────────
    try:
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    order_id = body.get("id")
    order_number = body.get("order_number") or body.get("name")

    if not order_id:
        raise HTTPException(status_code=400, detail="Shopify Order ID not found in payload")

    # Webhook Replay Protection
    from app.services.redis_pool import get_redis
    redis = get_redis()
    if redis:
        webhook_id = request.headers.get("x-shopify-webhook-id") or f"shopify:{client.id}:{order_id}:{body.get('updated_at')}"
        try:
            is_new = await redis.set(f"webhook_replay:{webhook_id}", "1", ex=86400, nx=True)
            if not is_new:
                logger.info(f"[{client.name}] Shopify duplicate webhook ignored: {webhook_id}")
                return {"status": "ignored", "detail": "Duplicate webhook delivery"}
        except Exception as re_exc:
            logger.warning(f"[{client.name}] Redis Shopify replay check failed: {re_exc}")

    logger.info(f"[{client.name}] 📬 Shopify webhook: order #{order_id} ({order_number})")

    # ─── Find pending event for this order ────────────────────────────
    possible_ids = [f"shopify_purchase_{order_id}", str(order_id), str(order_number)]
    pending = None
    for pid in possible_ids:
        result = await db.execute(
            select(PendingEvent).where(
                and_(
                    PendingEvent.client_id == client.id,
                    PendingEvent.order_id == pid,
                    PendingEvent.status == "pending",
                )
            ).with_for_update()
        )
        pending = result.scalar_one_or_none()
        if pending:
            break

    if not pending:
        # ─── Check if already confirmed or courier_booked ─────────────────
        confirmed_check = await db.execute(
            select(PendingEvent).where(
                and_(
                    PendingEvent.client_id == client.id,
                    PendingEvent.order_id.in_(possible_ids),
                    PendingEvent.status.in_(["confirmed", "courier_booking_queued", "courier_booked"]),
                )
            )
        )
        if confirmed_check.scalar_one_or_none():
            return {
                "status": "success",
                "order_id": str(order_id),
                "message": "Purchase event was already confirmed/processed.",
            }

    if pending:
        if pending.portal_state == "operations_only":
            return {
                "status": "ignored",
                "order_id": str(order_id),
                "message": "Purchase event was already queued. Order remains available for manual courier booking.",
            }
        # Confirm pending event
        booking = await _auto_book_courier_for_pending(client.id, pending, db)
        if booking["mode"] in {"queued", "already_booked"}:
            message = booking["message"]
        elif booking["mode"] == "not_configured":
            try:
                await _queue_confirmed_event(client, pending, db)
            except HTTPException:
                await db.rollback()
                raise
            except Exception as e:
                await db.rollback()
                logger.error(f"[{client.name}] Shopify confirm queue failed (order #{order_id}): {e}")
                raise HTTPException(
                    status_code=500,
                    detail="Purchase event queue failed.",
                ) from None
            pending.status = "confirmed"
            pending.portal_state = "confirmed"
            pending.is_confirmed = True
            pending.confirmed_at = datetime.now(timezone.utc)
            message = "Purchase event confirmed and queued for delivery!"
        else:
            await db.rollback()
            raise HTTPException(status_code=400, detail=booking["message"])

        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"[{client.name}] Shopify webhook commit failed (order #{order_id}): {e}")
            raise HTTPException(
                status_code=500,
                detail="Webhook confirmation could not be saved.",
            )
        return {
            "status": "success",
            "order_id": str(order_id),
            "message": message,
        }

    # ─── If no pending event found, create and queue a new Purchase event directly ───
    # Read user data
    email = body.get("email") or body.get("customer", {}).get("email") or ""
    phone = body.get("phone") or body.get("customer", {}).get("phone") or ""
    first_name = body.get("customer", {}).get("first_name") or ""
    last_name = body.get("customer", {}).get("last_name") or ""

    billing = body.get("billing_address", {})
    if not phone and billing.get("phone"):
        phone = billing.get("phone")
    if not first_name and billing.get("first_name"):
        first_name = billing.get("first_name")
    if not last_name and billing.get("last_name"):
        last_name = billing.get("last_name")

    city = billing.get("city") or ""
    state = billing.get("province") or ""
    country = billing.get("country_code") or billing.get("country") or ""
    zip_code = billing.get("zip") or ""

    # Hash helper
    def _sha(v: str) -> list[str]:
        if not v:
            return []
        v_clean = "".join(c for c in v if c.isalnum() or c in ("@", ".", "-", "_", "+")).strip().lower()
        if not v_clean:
            return []
        # Check if already hashed
        if len(v_clean) == 64 and all(c in "0123456789abcdef" for c in v_clean):
            return [v_clean]
        return [hashlib.sha256(v_clean.encode("utf-8")).hexdigest()]

    def _sha_phone(v: str) -> list[str]:
        if not v:
            return []
        hashed = hash_phone(v)
        return [hashed] if hashed else []

    user_data = {
        "client_ip_address": body.get("client_ip") or body.get("browser_ip") or "8.8.8.8",
        "client_user_agent": body.get("user_agent") or request.headers.get("user-agent", ""),
    }

    if email:
        user_data["em"] = _sha(email)
    if phone:
        user_data["ph"] = _sha_phone(phone)
    if first_name:
        user_data["fn"] = _sha(first_name)
    if last_name:
        user_data["ln"] = _sha(last_name)
    if city:
        user_data["ct"] = _sha(city)
    if state:
        user_data["st"] = _sha(state)
    if country:
        user_data["country"] = _sha(country)
    if zip_code:
        user_data["zp"] = _sha(zip_code)

    # Line items
    contents = []
    content_ids = []
    num_items = 0
    for item in body.get("line_items", []):
        p_id = str(item.get("product_id") or item.get("variant_id") or "")
        qty = int(item.get("quantity") or 1)
        price = float(item.get("price") or 0.0)
        if p_id:
            content_ids.append(p_id)
            contents.append({
                "id": p_id,
                "quantity": qty,
                "item_price": price
            })
            num_items += qty

    event_payload = {
        "event_name": "Purchase",
        "event_time": int(datetime.now(timezone.utc).timestamp()),
        "event_id": f"shopify_{order_id}",
        "event_source_url": body.get("order_status_url") or "",
        "action_source": "website",
        "user_data": user_data,
        "custom_data": {
            "value": float(body.get("total_price") or 0.0),
            "currency": body.get("currency") or "BDT",
            "content_type": "product",
            "content_ids": content_ids,
            "contents": contents,
            "num_items": num_items,
            "order_id": str(order_number or order_id)
        }
    }

    # Auto detect Cloudflare / Nginx headers
    client_ip = _get_real_ip(request)

    # Enrich
    enriched_event = boost_event_quality(
        EventData(**event_payload),
        cookies={},
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    event_payload = enriched_event.model_dump(exclude_none=True)

    try:
        reserved_keys = await check_and_reserve_usage(db, client, 1)
        await enqueue_events(
            db,
            client_id=client.id,
            events_data=[event_payload],
            request_context={
                "ip_address": client_ip,
                "user_agent": request.headers.get("user-agent", ""),
                "cookies": {}
            },
            usage_reserved=reserved_keys,
        )

        # Save a confirmed PendingEvent to prevent duplicate queuing
        new_pending = PendingEvent(
            client_id=client.id,
            order_id=f"shopify_purchase_{order_id}",
            event_data=event_payload,
            status="confirmed",
            is_confirmed=True,
            confirmed_at=datetime.now(timezone.utc)
        )
        db.add(new_pending)

        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.exception(f"[{client.name}] Shopify webhook queue failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to queue Shopify event")

    return {
        "status": "success",
        "order_id": str(order_id),
        "message": "Purchase event dynamically queued for delivery",
    }
