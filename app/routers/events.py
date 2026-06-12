import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_client, CachedClient
from app.models.event_log import EventLog
from app.services.dedup_service import reserve_unique_event_ids, rollback_redis_dedup
from app.schemas.event import EventsPayload, EventsResponse, UserData, _clean_and_hash
from app.services.geoip_service import get_location_data
from app.services.event_quality import boost_event_quality
from app.services.event_worker import enqueue_events
from app.services.fast_ingest_service import reserve_usage_and_enqueue_stream
from app.services.usage_service import check_and_reserve_usage
from app.services.plan_service import has_growth_access, remaining_monthly_order_capacity
from app.services.site_binding_service import check_site_event_rate_limit, validate_event_site_binding
from app.services.visitor_context import extract_device_metadata
from app.models.pending_event import PendingEvent
from app.routers.incomplete_checkouts import recover_open_checkouts_for_order

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_EVENTS_PER_REQUEST = int(os.getenv("MAX_EVENTS_PER_REQUEST", "500"))
CAPI_SIGNATURE_WINDOW_SECONDS = int(os.getenv("CAPI_SIGNATURE_WINDOW_SECONDS", "300"))
GEOIP_ENRICH_IN_REQUEST = os.getenv("GEOIP_ENRICH_IN_REQUEST", "false").lower() in ("true", "1", "yes")
FRAUD_INTERNAL_CUSTOM_KEYS = {
    "raw_first_name",
    "billing_first_name_raw",
    "email_domain",
    "billing_email_domain",
    "_bk_device_type",
    "_bk_device_os",
    "_bk_device_browser",
    "_bk_screen_width",
    "_bk_screen_height",
}




# ─── Domain Validation Helper ────────────────────────────────────────────────
def _is_domain_allowed(request_host: str, allowed_domain: str) -> bool:
    """Check if request_host is exactly allowed_domain or a real subdomain of it."""
    if not request_host:
        return False
    return request_host == allowed_domain or request_host.endswith("." + allowed_domain)


def _verify_capi_signature(
    raw_body: bytes,
    api_key: str,
    timestamp: str,
    signature: str,
) -> bool:
    """Verify signed X-CAPI-Origin proof without trusting a spoofable header alone."""
    if not raw_body or not api_key or not timestamp or not signature:
        return False
    try:
        issued_at = int(timestamp)
    except (TypeError, ValueError):
        return False

    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - issued_at) > CAPI_SIGNATURE_WINDOW_SECONDS:
        return False

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected = hmac.new(
        api_key.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


REQUIRE_SIGNED_DOMAIN_PROOF = os.getenv("REQUIRE_SIGNED_DOMAIN_PROOF", "true").lower() in ("1", "true", "yes")
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in ("1", "true", "yes")


class BrowserPixelAuditPayload(BaseModel):
    platform: str
    event_name: str
    event_id: str
    event_source_url: str
    page_title: str | None = None
    user_agent: str | None = None
    device_type: str | None = None
    device_os: str | None = None
    device_browser: str | None = None
    screen_width: int | None = None
    screen_height: int | None = None


def _request_client_ip(request: Request) -> str | None:
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/events/browser-audit", status_code=status.HTTP_202_ACCEPTED)
async def record_browser_pixel_audit(
    request: Request,
    payload: BrowserPixelAuditPayload,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Record that a browser pixel call was fired without claiming upstream acceptance."""
    if payload.platform.lower() != "tiktok" or payload.event_name != "PageView":
        raise HTTPException(status_code=400, detail="Unsupported browser pixel audit.")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", payload.event_id):
        raise HTTPException(status_code=400, detail="Invalid browser pixel event ID.")

    declared_origin = request.headers.get("x-capi-origin", "")
    declared_host = (urlparse(declared_origin).hostname or "").lower()
    if not declared_host:
        raise HTTPException(status_code=403, detail="Signed site origin required.")

    raw_body = await request.body()
    if not _verify_capi_signature(
        raw_body,
        client.api_key,
        request.headers.get("x-capi-timestamp", ""),
        request.headers.get("x-capi-signature", ""),
    ):
        raise HTTPException(status_code=403, detail="Invalid signed site proof.")

    allowed_domains = [d.strip().lower() for d in (client.domain or "").split(",") if d.strip()]
    if allowed_domains and not any(_is_domain_allowed(declared_host, domain) for domain in allowed_domains):
        raise HTTPException(status_code=403, detail="Unauthorized browser audit domain.")

    await validate_event_site_binding(
        db,
        client=client,
        events=[payload],
        signed_site_host=declared_origin,
        installation_id=(request.headers.get("x-buykori-installation-id") or "").strip()[:128] or None,
        ip_address=_request_client_ip(request),
    )
    await check_site_event_rate_limit(client, declared_host, 1)

    stored_name = "BrowserTikTok:PageView"
    existing = await db.execute(
        select(EventLog.id).where(
            EventLog.client_id == client.id,
            EventLog.event_name == stored_name,
            EventLog.event_id == payload.event_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return {"accepted": True, "duplicate": True}

    db.add(EventLog(
        client_id=client.id,
        event_name=stored_name,
        event_id=payload.event_id,
        event_count=0,
        status="fired",
        fb_response=json.dumps({
            "channel": "TikTok Browser Pixel",
            "state": "fired",
            "acceptance_unverified": True,
            "page_url": payload.event_source_url,
        })[:5000],
        device_type=(payload.device_type or "")[:24] or None,
        device_os=(payload.device_os or "")[:40] or None,
        device_browser=(payload.device_browser or "")[:40] or None,
        screen_width=payload.screen_width,
        screen_height=payload.screen_height,
    ))
    await db.commit()
    return {"accepted": True, "duplicate": False}


def _strip_internal_custom_data(event_dict: dict) -> dict:
    """Remove fraud-only fields before storing or queueing ad-platform payloads."""
    event_dict.pop("raw_order_data", None)
    custom_data = event_dict.get("custom_data")
    if isinstance(custom_data, dict):
        for key in FRAUD_INTERNAL_CUSTOM_KEYS:
            custom_data.pop(key, None)
    return event_dict


def _device_quality(device: dict | None) -> int:
    if not isinstance(device, dict):
        return 0
    score = 0
    if device.get("device_type"):
        score += 1
    if device.get("device_os") and str(device.get("device_os")).lower() != "unknown":
        score += 2
    if device.get("device_browser") and str(device.get("device_browser")).lower() != "unknown":
        score += 3
    if device.get("screen_width") or device.get("screen_height"):
        score += 1
    return score


def _best_request_device(events, user_agent: str | None) -> dict:
    best_device: dict = {}
    best_score = 0
    for event in events:
        custom_data = event.custom_data.model_dump() if event.custom_data else {}
        device = extract_device_metadata(custom_data, user_agent=user_agent)
        score = _device_quality(device)
        if score > best_score:
            best_device = device
            best_score = score
            if score >= 7:
                break
    return best_device


def _event_order_id(event) -> str:
    if event.custom_data and getattr(event.custom_data, "order_id", None):
        return str(event.custom_data.order_id)
    if event.event_id:
        return str(event.event_id)
    return f"auto-{event.event_time}-{id(event)}"


async def _upsert_order_record(
    db: AsyncSession,
    client: CachedClient,
    event,
    *,
    portal_state: str | None = None,
    fraud_score: int | None = None,
    fraud_details: dict | None = None,
) -> None:
    order_id = _event_order_id(event)
    raw_order_data = event.raw_order_data
    event_dict = _strip_internal_custom_data(event.model_dump(exclude_none=True))

    try:
        async with db.begin_nested():
            db.add(PendingEvent(
                client_id=client.id,
                order_id=order_id,
                event_data=event_dict,
                raw_order_data=raw_order_data,
                status="pending",
                portal_state=portal_state,
                fraud_score=fraud_score,
                fraud_details=fraud_details,
            ))
            await db.flush()
        logger.info(f"[{client.name}] Order record captured: {order_id}")
        return
    except Exception as exc:
        existing_result = await db.execute(
            select(PendingEvent)
            .where(
                and_(
                    PendingEvent.client_id == client.id,
                    PendingEvent.order_id == order_id,
                )
            )
            .order_by(PendingEvent.id.desc())
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if not existing:
            logger.exception(f"[{client.name}] Order record insert failed ({order_id}): {exc}")
            raise

    old_status = existing.status
    existing.event_data = event_dict
    existing.raw_order_data = raw_order_data
    existing.status = "pending"
    existing.portal_state = portal_state
    existing.is_confirmed = False
    existing.is_deleted = False
    existing.fraud_score = fraud_score
    existing.fraud_details = fraud_details
    existing.confirmed_at = None
    existing.created_at = datetime.now(timezone.utc)
    logger.info(f"[{client.name}] Refreshed order record: {order_id} (was {old_status})")


async def reserve_unique_events(
    db: AsyncSession,
    client: CachedClient,
    events: list,
) -> tuple[list, set[str]]:
    """
    Reserve event IDs atomically before sending to Facebook.
    The unique index on (client_id, event_id) closes the concurrent duplicate race.
    commit/rollback এখানে করা হয় না — caller নিজে transaction manage করবে।
    """
    candidate_ids: list[str] = []
    seen_ids: set[str] = set()
    for event in events:
        if not event.event_id or event.event_id in seen_ids:
            continue
        seen_ids.add(event.event_id)
        candidate_ids.append(event.event_id)

    if not candidate_ids:
        return [event for event in events if not event.event_id], set()

    reserved_ids = await reserve_unique_event_ids(db, client.id, candidate_ids)

    unique_events = []
    accepted_ids: set[str] = set()
    for event in events:
        if not event.event_id:
            unique_events.append(event)
            continue
        if event.event_id in accepted_ids:
            logger.info(f"[{client.name}] Duplicate event skipped in request: {event.event_id}")
            continue
        accepted_ids.add(event.event_id)
        if event.event_id not in reserved_ids:
            logger.info(f"[{client.name}] Duplicate event skipped: {event.event_id}")
            continue
        unique_events.append(event)

    return unique_events, reserved_ids


@router.post(
    "/events",
    response_model=EventsResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Facebook CAPI Events Endpoint",
)
async def receive_events(
    request: Request,
    payload: EventsPayload,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    hold: bool = Query(False, description="True হলে Purchase event hold হবে"),
    force_send: bool = Query(False, description="True হলে deferred Purchase bypass করে queue করা হবে"),
):
    """
    ক্লায়েন্টের API Key ভেরিফাই করে ইভেন্ট durable outbox queue-তে জমা করে।
    Deduplication DB unique constraint দিয়ে atomic করা হয়েছে।

    Transaction Flow:
    ─────────────────
    1. reserve_unique_events()       → dedup entries INSERT করে
    2. check_and_reserve_usage()     → non-held events quota reserve করে
    3. enqueue_events()              → outbox row একই transaction-এ save করে
    4. db.commit()                   → dedup + usage + outbox durable
    5. worker                        → পরে Facebook/TikTok/GA4/Webhook delivery করে
    """
    if not payload.data:
        raise HTTPException(status_code=400, detail="ইভেন্ট ডাটা খালি!")
    if len(payload.data) > MAX_EVENTS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"Too many events in one request. Max {MAX_EVENTS_PER_REQUEST}.",
        )
    version_header = request.headers.get("x-buykori-version")
    if version_header:
        logger.info(f"[{client.name}] Request received from plugin version: {version_header}")
    else:
        logger.debug(f"[{client.name}] Request received without X-Buykori-Version header")

    client_ip = _request_client_ip(request)
    site_host_for_binding = request.headers.get("x-capi-origin", "") or ""
    # ─── Domain Whitelisting (API Key চুরি প্রতিরোধ) ─────────────────
    # Client-এর domain সেট করা থাকলে, শুধু সেই ডোমেইন থেকে রিকোয়েস্ট নেবে
    # urlparse দিয়ে hostname extract করে exact match বা real subdomain চেক
    if client.domain:
        origin = request.headers.get("origin", "") or ""
        referer = request.headers.get("referer", "") or ""
        declared_origin = request.headers.get("x-capi-origin", "") or ""

        # Split allowed domains by comma
        allowed_domains = [d.strip().lower() for d in client.domain.split(",") if d.strip()]

        def _extract_hostname(url: str) -> str:
            url = url.strip()
            if not url:
                return ""
            if "://" not in url:
                # Prepend dummy scheme so urlparse identifies hostname properly when scheme is missing
                parsed = urlparse("http://" + url)
            else:
                parsed = urlparse(url)
            return (parsed.hostname or url).lower().strip()

        origin_host = _extract_hostname(origin)
        referer_host = _extract_hostname(referer)
        declared_host = _extract_hostname(declared_origin)
        site_host_for_binding = declared_host or origin_host or referer_host
        signed_declared_origin_ok = False

        if declared_host and allowed_domains:
            # Check if declared origin is allowed under any allowed domains
            any_declared_allowed = any(_is_domain_allowed(declared_host, ad) for ad in allowed_domains)
            if any_declared_allowed:
                raw_body = await request.body()
                signed_declared_origin_ok = _verify_capi_signature(
                    raw_body,
                    client.api_key,
                    request.headers.get("x-capi-timestamp", ""),
                    request.headers.get("x-capi-signature", ""),
                )

        if REQUIRE_SIGNED_DOMAIN_PROOF and not signed_declared_origin_ok:
            logger.warning(f"[{client.name}] Signed domain proof missing or invalid for locked domains: {client.domain}")
            raise HTTPException(
                status_code=403,
                detail="Signed domain proof required. Send X-CAPI-Origin, X-CAPI-Timestamp, and X-CAPI-Signature.",
            )

        if not (origin_host or referer_host or signed_declared_origin_ok):
            logger.warning(f"[{client.name}] Domain header missing for locked domains: {client.domain}")
            raise HTTPException(
                status_code=403,
                detail="Missing domain proof. Send Origin, Referer, or signed X-CAPI-Origin for this API Key.",
            )

        # Verify if request host is allowed under any allowed domains
        is_allowed = False
        for ad in allowed_domains:
            if (
                _is_domain_allowed(origin_host, ad)
                or _is_domain_allowed(referer_host, ad)
                or (signed_declared_origin_ok and _is_domain_allowed(declared_host, ad))
            ):
                is_allowed = True
                break

        if not is_allowed:
            logger.warning(
                f"[{client.name}] Domain mismatch! "
                f"Allowed: {client.domain}, Origin: {origin_host}, Referer: {referer_host}, Declared: {declared_host}"
            )
            raise HTTPException(
                status_code=403,
                detail="Unauthorized domain. এই API Key আপনার ডোমেইনে রেজিস্টার্ড নয়।",
            )

    if site_host_for_binding:
        await validate_event_site_binding(
            db,
            client=client,
            events=payload.data,
            signed_site_host=site_host_for_binding,
            installation_id=(request.headers.get("x-buykori-installation-id") or "").strip()[:128] or None,
            ip_address=client_ip,
        )
        await check_site_event_rate_limit(client, site_host_for_binding, len(payload.data))

    # ─── Auto-inject real IP & User-Agent into user_data ─────────────────
    # ফ্রন্টএন্ড থেকে placeholder IP (8.8.8.8) বা কোনো IP না আসলে
    # সার্ভার নিজেই ইউজারের আসল IP বসিয়ে দেবে
    PLACEHOLDER_IPS = {"8.8.8.8", "127.0.0.1", "0.0.0.0", None, ""}
    for event in payload.data:
        # Ensure user_data exists (schema now allows Optional)
        if not event.user_data:
            event.user_data = UserData()
        if event.user_data.client_ip_address in PLACEHOLDER_IPS:
            event.user_data.client_ip_address = client_ip
        if not event.user_data.client_user_agent:
            event.user_data.client_user_agent = request.headers.get("user-agent")

        # ─── GeoIP Enrichment ──────────────────────────────────────────
        if GEOIP_ENRICH_IN_REQUEST and event.user_data.client_ip_address:
            import anyio
            loc_data = await anyio.to_thread.run_sync(get_location_data, event.user_data.client_ip_address)
            if loc_data:
                if loc_data.get("ct") and not event.user_data.ct:
                    event.user_data.ct = [_clean_and_hash(loc_data["ct"], "ct")]
                if loc_data.get("st") and not event.user_data.st:
                    event.user_data.st = [_clean_and_hash(loc_data["st"], "st")]
                if loc_data.get("country") and not event.user_data.country:
                    event.user_data.country = [_clean_and_hash(loc_data["country"], "country")]
                if loc_data.get("zp") and not event.user_data.zp:
                    event.user_data.zp = [_clean_and_hash(loc_data["zp"], "zp")]
        boost_event_quality(
            event,
            cookies=dict(request.cookies),
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent"),
        )

    # ─── Durable Outbox Transaction ───────────────────────────────────
    # Dedup + usage reserve + queue insert একই transaction-এ commit হবে।
    should_hold = has_growth_access(client) and (hold or client.deferred_purchase) and not force_send
    deferred_count = 0
    queued_count = 0
    reserved_ids = set()
    try:
        deferred_events = [
            event for event in payload.data
            if should_hold and event.event_name == "Purchase"
        ]
        queue_candidates = [
            event for event in payload.data
            if not (should_hold and event.event_name == "Purchase")
        ]

        # Held Purchase events are idempotent by pending_events(client_id, order_id).
        # Reserving their event_id globally before the pending insert can strand COD
        # orders if a retry already wrote a dedup row but failed before queueing.
        queue_events, reserved_ids = await reserve_unique_events(db, client, queue_candidates)
        if not deferred_events and not queue_events:
            await db.commit()
            return EventsResponse(
                status="accepted",
                events_received=0,
                message="All events were already received earlier (deduplicated).",
            )

        reserved_keys = {}

        # Purchase events → pending_events table; confirm flow sends these later.
        # Parallelize fraud score calculation using asyncio.gather instead of sequential await in loops
        from app.services.fraud_service import calculate_fraud_score
        import asyncio

        fraud_tasks = []
        for event in deferred_events:
            fraud_tasks.append(calculate_fraud_score(db, client.id, event, client_ip))

        fraud_results = []
        if fraud_tasks:
            try:
                fraud_results = await asyncio.gather(*fraud_tasks)
            except Exception as e:
                logger.error(f"[{client.name}] Parallel fraud score calculation failed: {e}")
                # Fallback to None for all tasks if gather failed
                fraud_results = [(None, None) for _ in deferred_events]
        else:
            fraud_results = []

        for event, fraud_res in zip(deferred_events, fraud_results):
            fraud_score_val, fraud_details_val = fraud_res if fraud_res else (None, None)
            if event.custom_data and getattr(event.custom_data, "order_id", None):
                order_id = event.custom_data.order_id
            elif event.event_id:
                order_id = event.event_id
            else:
                order_id = f"auto-{event.event_time}-{id(event)}"

            raw_order_data = event.raw_order_data
            event_dict = _strip_internal_custom_data(event.model_dump(exclude_none=True))

            try:
                async with db.begin_nested():
                    pending = PendingEvent(
                        client_id=client.id,
                        order_id=order_id,
                        event_data=event_dict,
                        raw_order_data=raw_order_data,
                        status="pending",
                        fraud_score=fraud_score_val,
                        fraud_details=fraud_details_val,
                    )
                    db.add(pending)
                    await db.flush()  # unique constraint check
                deferred_count += 1
                logger.info(f"[{client.name}] Purchase hold: {order_id}")
            except Exception as exc:
                existing_result = await db.execute(
                    select(PendingEvent)
                    .where(
                        and_(
                            PendingEvent.client_id == client.id,
                            PendingEvent.order_id == order_id,
                        )
                    )
                    .order_by(PendingEvent.id.desc())
                    .limit(1)
                )
                existing = existing_result.scalar_one_or_none()

                if existing and existing.status != "pending":
                    old_status = existing.status
                    existing.event_data = event_dict
                    existing.raw_order_data = raw_order_data
                    existing.status = "pending"
                    existing.portal_state = None
                    existing.is_confirmed = False
                    existing.is_deleted = False
                    existing.fraud_score = fraud_score_val
                    existing.fraud_details = fraud_details_val
                    existing.confirmed_at = None
                    existing.created_at = datetime.now(timezone.utc)
                    deferred_count += 1
                    logger.info(
                        f"[{client.name}] Revived pending order: {order_id} "
                        f"(was {old_status})"
                    )
                elif existing:
                    existing.event_data = event_dict
                    existing.raw_order_data = raw_order_data
                    existing.fraud_score = fraud_score_val
                    existing.fraud_details = fraud_details_val
                    existing.created_at = datetime.now(timezone.utc)
                    deferred_count += 1
                    logger.info(
                        f"[{client.name}] Refreshed duplicate pending order: {order_id}"
                    )
                else:
                    logger.exception(
                        f"[{client.name}] Pending order insert failed ({order_id}): {exc}"
                    )
                    raise

            if isinstance(raw_order_data, dict):
                recipient_phone = str(
                    raw_order_data.get("recipient_phone")
                    or raw_order_data.get("customer_phone")
                    or raw_order_data.get("billing_phone")
                    or ""
                ).strip()
                if recipient_phone:
                    recovered = await recover_open_checkouts_for_order(
                        db,
                        client_id=client.id,
                        phone=recipient_phone,
                        order_id=str(order_id),
                    )
                    if recovered:
                        logger.info(f"[{client.name}] Recovered incomplete checkout for order: {order_id}")

        operations_events = [
            event for event in queue_events
            if event.event_name == "Purchase"
        ]
        if operations_events:
            operations_order_ids = {_event_order_id(event) for event in operations_events}
            existing_operations_r = await db.execute(
                select(PendingEvent.order_id).where(
                    and_(
                        PendingEvent.client_id == client.id,
                        PendingEvent.order_id.in_(operations_order_ids),
                    )
                )
            )
            existing_operations = set(existing_operations_r.scalars().all())
            remaining_orders = await remaining_monthly_order_capacity(db, client.id, client)
            for event in operations_events:
                order_id = _event_order_id(event)
                is_new_order = order_id not in existing_operations
                if is_new_order and remaining_orders is not None and remaining_orders <= 0:
                    logger.warning(f"[{client.name}] Monthly manual order dashboard quota reached; skipped capture: {order_id}")
                    continue
                await _upsert_order_record(
                    db,
                    client,
                    event,
                    portal_state="operations_only",
                )
                if is_new_order:
                    existing_operations.add(order_id)
                    if remaining_orders is not None:
                        remaining_orders -= 1

        if queue_events:
            request_user_agent = request.headers.get("user-agent", "")
            request_device = _best_request_device(queue_events, request_user_agent)
            events_as_dicts = [
                _strip_internal_custom_data(event.model_dump(exclude_none=True))
                for event in queue_events
            ]
            request_context = {
                "ip_address": client_ip,
                "user_agent": request_user_agent,
                "device": request_device,
                "cookies": {
                    key: value
                    for key, value in request.cookies.items()
                    if key in {"_ga", "_fbp", "_fbc", "_ttp", "_ttclid"}
                },
            }
            fast_enqueued = False
            if not deferred_events:
                fast_enqueued, reserved_keys = await reserve_usage_and_enqueue_stream(
                    client,
                    events_data=events_as_dicts,
                    request_context=request_context,
                )
            if not fast_enqueued:
                reserved_keys = await check_and_reserve_usage(db, client, len(queue_events))
                await enqueue_events(
                    db,
                    client_id=client.id,
                    events_data=events_as_dicts,
                    request_context=request_context,
                    usage_reserved=reserved_keys,
                )
            queued_count = len(queue_events)

        # Enqueue WhatsApp owner notification jobs for new Purchase events
        has_purchase = any(e.event_name == "Purchase" for e in payload.data)
        if has_purchase:
            from app.models.client import Client as ClientModel
            from app.services.notification_service import create_purchase_whatsapp_job
            client_model = await db.get(ClientModel, client.id)
            if client_model:
                for event in payload.data:
                    if event.event_name == "Purchase":
                        event_dict = event.model_dump(exclude_none=True)
                        await create_purchase_whatsapp_job(db, client_model, event_dict)

        await db.commit()
    except HTTPException:
        await db.rollback()
        if reserved_ids:
            await rollback_redis_dedup(client.id, reserved_ids)
        raise
    except Exception:
        await db.rollback()
        if reserved_ids:
            await rollback_redis_dedup(client.id, reserved_ids)
        logger.exception(f"[{client.name}] Event enqueue failed")
        raise HTTPException(status_code=500, detail="Failed to enqueue events") from None

    total_received = queued_count + deferred_count
    message_parts = []
    if queued_count:
        message_parts.append(f"{queued_count} event(s) queued for delivery")
    if deferred_count:
        message_parts.append(f"{deferred_count} Purchase event(s) held for confirmation")

    return EventsResponse(
        status="accepted",
        events_received=total_received,
        message="; ".join(message_parts) if message_parts else "No new events accepted.",
    )
