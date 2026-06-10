"""
Tracker Router — Custom JavaScript Pixel সার্ভ ও ইভেন্ট কালেকশন।

Endpoints:
  GET  /t.js?key=API_KEY  → ডায়নামিক JS ট্র্যাকার সার্ভ করে
  POST /c?key=API_KEY     → ব্রাউজার থেকে ইভেন্ট রিসিভ করে outbox queue-তে রাখে

এন্ডপয়েন্টের নাম ছোট ও জেনেরিক রাখা হয়েছে যাতে অ্যাড ব্লকার detect করতে না পারে।
"""

import json
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from user_agents import parse as parse_ua

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_client, CachedClient, _client_cache, _snapshot, CACHE_TTL, set_in_client_cache
from app.models.client import Client
from app.services.dedup_service import reserve_unique_event_ids
from app.services.fast_ingest_service import dedup_reserve_usage_and_enqueue_stream
from app.schemas.event import EventData, UserData, CustomData
from app.services.bot_detector import classify_traffic
from app.services.event_quality import boost_event_quality
from app.services.geoip_service import get_location_data
from app.services.event_worker import enqueue_events
from app.services.tracker_sdk import generate_tracker_js
from app.services.usage_service import check_and_reserve_usage
from app.limiter import _get_real_ip

from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter()
TRACKER_COOKIE_DOMAIN = os.getenv("TRACKER_COOKIE_DOMAIN", "").strip() or None
EVENT_INGEST_MODE = os.getenv("EVENT_INGEST_MODE", "db").strip().lower()
TRACKER_ENRICH_IN_REQUEST = os.getenv("TRACKER_ENRICH_IN_REQUEST", "false").lower() in ("true", "1", "yes")
TRACKER_INGEST_TOKEN_TTL_SECONDS = int(os.getenv("TRACKER_INGEST_TOKEN_TTL_SECONDS", "900"))
REQUIRE_TRACKER_INGEST_TOKEN = os.getenv("REQUIRE_TRACKER_INGEST_TOKEN", "false").lower() in ("1", "true", "yes")


def _fast_stream_ingest_enabled() -> bool:
    return EVENT_INGEST_MODE == "redis_stream" and bool(os.getenv("REDIS_URL"))


def _tracker_ingest_signature(secret: str, public_key: str, expires_at: int, nonce: str) -> str:
    message = f"{public_key}.{expires_at}.{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _issue_tracker_ingest_token(client: CachedClient, public_key: str) -> str:
    nonce = hashlib.sha256(f"{client.id}.{time.time_ns()}".encode("utf-8")).hexdigest()[:16]
    expires_at = int(time.time()) + TRACKER_INGEST_TOKEN_TTL_SECONDS
    signature = _tracker_ingest_signature(client.api_key, public_key, expires_at, nonce)
    return f"{expires_at}.{nonce}.{signature}"


def _verify_tracker_ingest_token(client: CachedClient, public_key: str, token: str) -> bool:
    try:
        expires_raw, nonce, signature = (token or "").split(".", 2)
        expires_at = int(expires_raw)
    except (TypeError, ValueError):
        return False
    if expires_at < int(time.time()) or expires_at > int(time.time()) + (TRACKER_INGEST_TOKEN_TTL_SECONDS * 2):
        return False
    expected = _tracker_ingest_signature(client.api_key, public_key, expires_at, nonce)
    return hmac.compare_digest(expected, signature)


def _get_tracker_cookie_domain(request: Request) -> str | None:
    env_val = os.getenv("TRACKER_COOKIE_DOMAIN")
    if env_val is not None:
        val = env_val.strip()
        if val.lower() in ("none", "null", "false", ""):
            return None
        return val
        
    host = (request.url.hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1"):
        return None
        
    parts = host.split(".")
    if len(parts) >= 2:
        if len(parts) >= 3 and len(parts[-2]) <= 3 and parts[-2] in {"com", "co", "org", "net", "gov", "edu"}:
            return "." + ".".join(parts[-3:])
        return "." + ".".join(parts[-2:])
    return None


def _attach_tracker_cookies(resp: JSONResponse, body: dict, request: Request) -> JSONResponse:
    first_event = body.get("data", [{}])[0] if body.get("data") else {}
    ud = first_event.get("user_data", {})
    fbp_val = ud.get("fbp", "")
    fbc_val = ud.get("fbc", "")

    cookie_max_age = 180 * 24 * 60 * 60
    cookie_domain = _get_tracker_cookie_domain(request)

    if fbp_val:
        resp.set_cookie(
            key="_fbp",
            value=fbp_val,
            max_age=cookie_max_age,
            httponly=False,
            secure=True,
            samesite="lax",
            path="/",
            domain=cookie_domain,
        )
    if fbc_val:
        resp.set_cookie(
            key="_fbc",
            value=fbc_val,
            max_age=cookie_max_age,
            httponly=False,
            secure=True,
            samesite="lax",
            path="/",
            domain=cookie_domain,
        )
    return resp


# ─── Negative Cache: Invalid API Key গুলো ৩০ সেকেন্ড ব্লক রাখে (DDoS/Cache Penetration ডিফেন্স) ───
_invalid_key_cache: dict[str, float] = {}
_NEGATIVE_CACHE_TTL = 30      # সেকেন্ড
_NEGATIVE_CACHE_MAX_SIZE = 2000  # সর্বোচ্চ এন্ট্রি (মেমোরি গার্ড)


# ─── Helper: API Key থেকে Client লোড (Query param version) ──────────────────
async def _get_client_by_key(public_key: str, db: AsyncSession) -> CachedClient:
    """
    Query parameter থেকে আসা API Key দিয়ে ক্লায়েন্ট খোঁজে।
    In-memory cache ব্যবহার করে — dependencies.py-এর সাথে cache শেয়ার করে।
    Invalid/non-existent keys ৩০ সেকেন্ডের জন্য Negative Cache-এ রাখা হয়
    যাতে bot spam বা ভুল key দিয়ে বারবার DB hit না হয় (Cache Penetration ডিফেন্স)।
    """
    if not public_key:
        raise HTTPException(status_code=401, detail="API Key প্রয়োজন।")

    now = time.time()
    cache_key = f"public:{public_key}"

    # ─── ১. Negative Cache চেক (DB hit ছাড়াই Invalid Key ব্লক) ────────────
    if public_key in _invalid_key_cache:
        blocked_at = _invalid_key_cache[public_key]
        if now - blocked_at < _NEGATIVE_CACHE_TTL:
            raise HTTPException(status_code=401, detail="Invalid API Key।")
        else:
            # TTL শেষ — negative cache entry মুছে দাও
            del _invalid_key_cache[public_key]

    # ─── ২. Positive Cache চেক ─────────────────────────────────────────────
    if cache_key in _client_cache:
        cached, cached_at = _client_cache[cache_key]
        if now - cached_at < CACHE_TTL:
            if cached.is_active:
                return cached
            raise HTTPException(status_code=401, detail="Inactive API Key।")

    # ─── ৩. DB Lookup ──────────────────────────────────────────────────────
    result = await db.execute(
        select(Client).where(Client.public_key == public_key, Client.is_active == True)
    )
    client = result.scalar_one_or_none()
    if not client:
        # Negative Cache-এ যোগ করো (মেমোরি গার্ড: ২০০০ এন্ট্রির বেশি হলে পুরনোটা বাদ)
        if len(_invalid_key_cache) >= _NEGATIVE_CACHE_MAX_SIZE:
            # সবচেয়ে পুরনো ২০০ এন্ট্রি বাদ দাও
            oldest = sorted(_invalid_key_cache.items(), key=lambda x: x[1])[:200]
            for k, _ in oldest:
                del _invalid_key_cache[k]
        _invalid_key_cache[public_key] = now
        raise HTTPException(status_code=401, detail="Invalid API Key।")

    cached = _snapshot(client)
    set_in_client_cache(cache_key, cached)
    return cached


# ═══════════════════════════════════════════════════════════════════════════════
# GET /t.js — Dynamic JavaScript Tracker সার্ভ করা
# ═══════════════════════════════════════════════════════════════════════════════
@router.get("/t.js", include_in_schema=False)
async def serve_tracker_js(
    request: Request,
    key: str = Query(..., description="Client API Key"),
    db: AsyncSession = Depends(get_db),
):
    """
    ক্লায়েন্টের API Key ভেরিফাই করে ডায়নামিক JavaScript ট্র্যাকার সার্ভ করে।
    Response-এ proper caching headers সেট করা হয়।
    """
    # Validate API Key
    client = await _get_client_by_key(key, db)

    # Gateway origin detect করো — request যেই host-এ আসছে সেটাই ব্যবহার করবে
    # Nginx X-Forwarded-Proto header থেকে scheme নেওয়া হচ্ছে
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", "localhost")
    gateway_origin = f"{scheme}://{host}"

    # Generate JS
    ingest_token = _issue_tracker_ingest_token(client, key)
    js_code = generate_tracker_js(api_key=key, gateway_origin=gateway_origin, ingest_token=ingest_token)

    return Response(
        content=js_code,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
            "X-Content-Type-Options": "nosniff",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# POST /c — Collect Endpoint (ব্রাউজার থেকে ইভেন্ট রিসিভ)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/c", include_in_schema=False)
async def collect_event(
    request: Request,
    key: str = Query(..., description="Client API Key"),
    token: str = Query("", description="Short-lived tracker ingest token"),
    db: AsyncSession = Depends(get_db),
):
    """
    ব্রাউজারের ট্র্যাকার JS থেকে ইভেন্ট রিসিভ করে।
    Bot detection করে, dedup চেক করে, তারপর durable outbox queue-তে রাখে।
    """
    # ─── Parse body (Beacon API sends as blob) ────────────────────────
    try:
        body = await request.json()
    except Exception:
        # Beacon API sometimes sends with wrong content-type
        raw = await request.body()
        try:
            body = json.loads(raw)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not body or not body.get("data"):
        raise HTTPException(status_code=400, detail="Empty event data")

    # ─── Validate API Key ─────────────────────────────────────────────
    client = await _get_client_by_key(key, db)
    if REQUIRE_TRACKER_INGEST_TOKEN and not _verify_tracker_ingest_token(client, key, token):
        raise HTTPException(status_code=401, detail="Invalid tracker ingest token")

    # ─── Real IP Detection ────────────────────────────────────────────
    client_ip = _get_real_ip(request)

    # ─── Bot Detection ────────────────────────────────────────────────
    user_agent = request.headers.get("user-agent", "")
    traffic_class = classify_traffic(user_agent, ip=client_ip, has_cookie=bool(request.cookies))
    if traffic_class == "bot":
        logger.info(f"[{client.name}] 🤖 Bot event dropped from tracker")
        # Return 200 to not leak bot detection to caller
        return {"status": "ok", "events_received": 0, "message": "processed"}
    if traffic_class == "suspicious":
        logger.info(f"[{client.name}] Suspicious tracker traffic accepted for observation")

    # ─── Domain Whitelisting ──────────────────────────────────────────
    if client.domain:
        origin = request.headers.get("origin", "") or ""
        referer = request.headers.get("referer", "") or ""
        origin_host = (urlparse(origin).hostname or "").lower()
        referer_host = (urlparse(referer).hostname or "").lower()

        allowed_domains = [d.strip().lower() for d in client.domain.split(",") if d.strip()]

        def _domain_ok(host: str) -> bool:
            if not host:
                return False
            for allowed in allowed_domains:
                if host == allowed or host.endswith("." + allowed):
                    return True
            return False

        if not (_domain_ok(origin_host) or _domain_ok(referer_host)):
            logger.warning(f"[{client.name}] Tracker domain mismatch: {origin_host} / {referer_host} (Allowed: {client.domain})")
            raise HTTPException(status_code=403, detail="Unauthorized domain.")

    # ─── Parse Events ─────────────────────────────────────────────────
    raw_events = body.get("data", [])
    if len(raw_events) > 50:
        raw_events = raw_events[:50]  # Tracker থেকে max 50 events

    parsed_events = []
    fast_stream = _fast_stream_ingest_enabled()
    for raw in raw_events:
        try:
            # user_data build
            ud_raw = raw.get("user_data", {})
            # Inject real IP if missing
            if not ud_raw.get("client_ip_address") or ud_raw.get("client_ip_address") in ("8.8.8.8", "127.0.0.1", ""):
                ud_raw["client_ip_address"] = client_ip
            if not ud_raw.get("client_user_agent"):
                ud_raw["client_user_agent"] = user_agent

            # ─── GeoIP Enrichment ──────────────────────────────────────────
            if TRACKER_ENRICH_IN_REQUEST and not fast_stream and ud_raw.get("client_ip_address"):
                loc_data = get_location_data(ud_raw["client_ip_address"])
                if loc_data:
                    if loc_data.get("ct") and not ud_raw.get("ct"):
                        ud_raw["ct"] = loc_data["ct"]
                    if loc_data.get("st") and not ud_raw.get("st"):
                        ud_raw["st"] = loc_data["st"]
                    if loc_data.get("country") and not ud_raw.get("country"):
                        ud_raw["country"] = loc_data["country"]
                    if loc_data.get("zp") and not ud_raw.get("zp"):
                        ud_raw["zp"] = loc_data["zp"]

            user_data = UserData(**ud_raw)

            # custom_data (optional)
            custom_data_dict = raw.get("custom_data", {})
            if custom_data_dict is None:
                custom_data_dict = {}

            # ─── User-Agent Parsing ────────────────────────────────────────
            if TRACKER_ENRICH_IN_REQUEST and not fast_stream and ud_raw.get("client_user_agent"):
                ua = parse_ua(ud_raw["client_user_agent"])
                custom_data_dict["device_type"] = "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC"
                custom_data_dict["os_name"] = ua.os.family
                custom_data_dict["browser_name"] = ua.browser.family

            # ─── UTM Parameter Extraction ──────────────────────────────────
            event_url = raw.get("event_source_url", "")
            if event_url:
                parsed_url = urlparse(event_url)
                qs = parse_qs(parsed_url.query)
                for utm_key in ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"]:
                    if utm_key in qs:
                        custom_data_dict[utm_key] = qs[utm_key][0]

            custom_data = CustomData(**custom_data_dict)

            event = EventData(
                event_name=raw.get("event_name", "PageView"),
                event_time=raw.get("event_time", int(datetime.now(timezone.utc).timestamp())),
                event_id=raw.get("event_id"),
                event_source_url=raw.get("event_source_url"),
                action_source=raw.get("action_source", "website"),
                user_data=user_data,
                custom_data=custom_data,
                emq_score=None,
            )
            boost_event_quality(
                event,
                cookies=dict(request.cookies),
                ip_address=client_ip,
                user_agent=user_agent,
            )
            parsed_events.append(event)
        except Exception as e:
            logger.warning(f"[{client.name}] Tracker event parse error: {e}")
            continue

    if not parsed_events:
        return {"status": "ok", "events_received": 0, "message": "no valid events"}

    # ─── Dedup + Usage Reserve + Outbox Enqueue ──────────────────────
    unique_events = []
    try:
        candidate_ids = []
        seen_ids: set[str] = set()
        no_id_events: list = []
        for event in parsed_events:
            # boost_event_quality generates stable event IDs for tracker payloads.
            # Guard: events without an event_id skip dedup and go straight through.
            if not event.event_id:
                no_id_events.append(event)
                continue
            if event.event_id in seen_ids:
                continue
            seen_ids.add(event.event_id)
            candidate_ids.append(event.event_id)

        request_context = {
            "ip_address": client_ip,
            "user_agent": user_agent,
            "cookies": {
                key: value
                for key, value in request.cookies.items()
                if key in {"_ga", "_fbp", "_fbc", "_ttp", "_ttclid"}
            },
        }
        if fast_stream:
            fast_enqueued, accepted_count = await dedup_reserve_usage_and_enqueue_stream(
                client,
                events_data=[event.model_dump(exclude_none=True) for event in parsed_events],
                request_context=request_context,
            )
            if fast_enqueued:
                response_data = {
                    "status": "ok",
                    "events_received": accepted_count,
                    "message": "queued" if accepted_count else "deduplicated",
                }
                return _attach_tracker_cookies(JSONResponse(content=response_data), body, request)

        reserved_ids = await reserve_unique_event_ids(db, client.id, candidate_ids)
        accepted_ids: set[str] = set()
        for event in parsed_events:
            if not event.event_id:
                continue  # already collected above
            if event.event_id in reserved_ids and event.event_id not in accepted_ids:
                accepted_ids.add(event.event_id)
                unique_events.append(event)
        # Include events that had no event_id (cannot be deduplicated)
        unique_events.extend(no_id_events)

        if not unique_events:
            await db.commit()
            response_data = {"status": "ok", "events_received": 0, "message": "deduplicated"}
            resp = JSONResponse(content=response_data)
        else:
            reserved_keys = await check_and_reserve_usage(db, client, len(unique_events))
            events_as_dicts = [event.model_dump(exclude_none=True) for event in unique_events]
            await enqueue_events(
                db,
                client_id=client.id,
                events_data=events_as_dicts,
                request_context=request_context,
                usage_reserved=reserved_keys,
            )
            await db.commit()
            response_data = {"status": "ok", "events_received": len(unique_events), "message": "queued"}
            resp = JSONResponse(content=response_data)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.exception(f"[{client.name}] Tracker enqueue failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to queue tracker event") from None

    # ─── Cookie Life Extension (Safari ITP Bypass) ────────────────────
    # Server-side Set-Cookie ইস্যু করে _fbp ও _fbc কুকির মেয়াদ ৬ মাস পর্যন্ত বাড়ানো হয়।
    # Safari ITP ব্রাউজারে JavaScript দিয়ে সেট করা কুকি ১-৭ দিনে expire হয়, কিন্তু
    # সার্ভার (HTTP header) থেকে Set-Cookie করলে ITP bypass হয়!
    # ব্রাউজার থেকে পাঠানো _fbp/_fbc ভ্যালু খোঁজো (body-তে থাকতে পারে)
    first_event = body.get("data", [{}])[0] if body.get("data") else {}
    ud = first_event.get("user_data", {})
    fbp_val = ud.get("fbp", "")
    fbc_val = ud.get("fbc", "")

    cookie_max_age = 180 * 24 * 60 * 60  # 180 দিন (৬ মাস)
    cookie_domain = _get_tracker_cookie_domain(request)

    if fbp_val:
        resp.set_cookie(
            key="_fbp",
            value=fbp_val,
            max_age=cookie_max_age,
            httponly=False,   # JS থেকেও পড়তে হবে
            secure=True,
            samesite="lax",
            path="/",
            domain=cookie_domain,
        )
    if fbc_val:
        resp.set_cookie(
            key="_fbc",
            value=fbc_val,
            max_age=cookie_max_age,
            httponly=False,
            secure=True,
            samesite="lax",
            path="/",
            domain=cookie_domain,
        )

    return resp
