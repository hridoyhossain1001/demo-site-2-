import time
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse
from fastapi import Header, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.client import Client
from app.models.client_session import ClientSession
from app.security import decrypt_token
from app.services.auth_service import hash_session_token
from app.services.plan_service import effective_monthly_event_limit, has_growth_access
from app.services.redis_pool import get_redis, record_redis_fallback

logger = logging.getLogger(__name__)

# ─── In-Memory API Key Cache ────────────────────────────────────────────────
# প্রতি request-এ DB query বাদ দিতে TTL-based cache
# ORM object cache করলে DetachedInstanceError হতে পারে,
# তাই plain dataclass cache করা হয়। DB থেকে আনা Client-এর সব প্রয়োজনীয় field কপি করে রাখে।


@dataclass(frozen=True, slots=True)
class CachedClient:
    """Lightweight snapshot of a Client row — safe to cache across requests."""
    id: int
    name: str
    api_key: str
    public_key: str | None
    portal_key: str | None
    pixel_id: str
    access_token: str
    test_event_code: str | None          # Facebook test event code
    tiktok_test_event_code: str | None   # TikTok test event code (আলাদা)
    is_active: bool
    domain: str | None
    rate_limit: int
    daily_quota: int
    monthly_limit: int | None
    enable_facebook: bool
    enable_tiktok: bool
    enable_ga4: bool
    tiktok_pixel_id: str | None
    tiktok_access_token: str | None
    ga4_measurement_id: str | None
    ga4_api_secret: str | None
    deferred_purchase: bool
    webhook_url: str | None
    plan_tier: str
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    shopify_shared_secret: str | None = None
    woocommerce_webhook_secret: str | None = None
    capi_signing_secret: str | None = None
    event_rules: dict | list | None = None


_client_cache: dict[str, tuple[CachedClient, float]] = {}
CLIENT_CACHE_INVALIDATION_PREFIX = "client_cache_invalidated_at:"
CACHE_TTL = 60  # 60 সেকেন্ড — client info cache করে রাখো

ALLOWED_CLIENT_AUTH_HOSTS = {
    host.strip().lower()
    for host in os.getenv(
        "CLIENT_AUTH_ALLOWED_HOSTS",
        "buykori.app,www.buykori.app,client.buykori.app,localhost,127.0.0.1",
    ).split(",")
    if host.strip()
}


def _origin_allowed_for_cookie_auth(request: Request) -> bool:
    """
    Cookie-ভিত্তিক সেশন রিকোয়েস্টে Origin যাচাই করে।
    - Origin হেডার না থাকলে: GET/HEAD রিকোয়েস্ট হলে allow (same-origin ব্রাউজার behavior),
      অন্যথায় reject (CSRF প্রতিরোধ)।
    - Origin হেডার থাকলে: allowlist-এ থাকলে allow।
    """
    origin = request.headers.get("origin")
    if not origin:
        # ব্রাউজার same-origin GET/HEAD রিকোয়েস্টে Origin পাঠায় না — allow
        # POST/PATCH/DELETE-এ Origin না থাকলে সন্দেহজনক — reject
        return request.method.upper() in ("GET", "HEAD", "OPTIONS")
    host = (urlparse(origin).hostname or "").lower()
    return host in ALLOWED_CLIENT_AUTH_HOSTS


def _client_cache_invalidation_key(api_key: str) -> str:
    return f"{CLIENT_CACHE_INVALIDATION_PREFIX}{api_key}"


def clear_client_cache(api_key: str):
    """Admin update-এর পর cache ক্লিয়ার করতে ব্যবহৃত হয়।
    api_key এবং public:{public_key} উভয় ধরনের cache entry মুছে ফেলে।"""
    if not api_key:
        return
    keys_to_delete = []
    for cache_key, (cached, _) in list(_client_cache.items()):
        if cache_key == api_key or cached.api_key == api_key:
            keys_to_delete.append(cache_key)
            # Also clear any public key cache entry for this client
            if cached.public_key:
                keys_to_delete.append(f"public:{cached.public_key}")
    for k in keys_to_delete:
        _client_cache.pop(k, None)


async def invalidate_client_cache(api_key: str) -> None:
    clear_client_cache(api_key)
    redis = get_redis()
    if not redis:
        return
    try:
        await redis.set(
            _client_cache_invalidation_key(api_key),
            str(time.time()),
            ex=max(CACHE_TTL * 4, 300),
        )
    except Exception as exc:
        logger.warning("Redis client cache invalidation failed: %s", exc)
        record_redis_fallback("client_cache_invalidation")


async def _client_cache_invalidated(api_key: str, cached_at: float) -> bool:
    redis = get_redis()
    if not redis:
        return False
    try:
        invalidated_at = await redis.get(_client_cache_invalidation_key(api_key))
    except Exception as exc:
        logger.warning("Redis client cache invalidation check failed: %s", exc)
        record_redis_fallback("client_cache_invalidation_check")
        return False
    if not invalidated_at:
        return False
    try:
        return float(invalidated_at) > cached_at
    except (TypeError, ValueError):
        return True


def set_in_client_cache(cache_key: str, cached_client: CachedClient):
    """ক্যাশে নতুন ক্লায়েন্ট অ্যাড করার সময় ক্যাশের সাইজ ১০০০-এর নিচে রাখে যাতে মেমোরি লিক না হয়"""
    now = time.time()
    if len(_client_cache) >= 1000:
        # First pass: evict expired entries
        expired_keys = [k for k, (_, ts) in list(_client_cache.items()) if now - ts >= CACHE_TTL]
        for k in expired_keys:
            _client_cache.pop(k, None)
        # Still full? Evict oldest 200 entries (LRU-style) instead of clearing all
        if len(_client_cache) >= 1000:
            sorted_keys = sorted(_client_cache.keys(), key=lambda k: _client_cache[k][1])
            for k in sorted_keys[:200]:
                _client_cache.pop(k, None)
    _client_cache[cache_key] = (cached_client, now)

def _snapshot(client: Client) -> CachedClient:
    """ORM object থেকে plain dataclass তৈরি করো — session-independent।"""
    return CachedClient(
        id=client.id,
        name=client.name,
        api_key=client.api_key,
        public_key=getattr(client, 'public_key', None),
        portal_key=getattr(client, 'portal_key', None),
        pixel_id=client.pixel_id,
        access_token=client.access_token,
        test_event_code=client.test_event_code,
        tiktok_test_event_code=getattr(client, 'tiktok_test_event_code', None),
        is_active=client.is_active,
        domain=client.domain,
        rate_limit=client.rate_limit or 5000,
        daily_quota=client.daily_quota or 100000,
        monthly_limit=effective_monthly_event_limit(client),
        enable_facebook=getattr(client, 'enable_facebook', True),
        enable_tiktok=has_growth_access(client) and getattr(client, 'enable_tiktok', True),
        enable_ga4=has_growth_access(client) and getattr(client, 'enable_ga4', True),
        tiktok_pixel_id=getattr(client, 'tiktok_pixel_id', None),
        tiktok_access_token=getattr(client, 'tiktok_access_token', None),
        ga4_measurement_id=getattr(client, 'ga4_measurement_id', None),
        ga4_api_secret=getattr(client, 'ga4_api_secret', None),
        deferred_purchase=has_growth_access(client) and (getattr(client, 'deferred_purchase', False) or False),
        webhook_url=getattr(client, 'webhook_url', None),
        plan_tier=getattr(client, 'plan_tier', 'growth') or 'growth',
        trial_started_at=getattr(client, 'trial_started_at', None),
        trial_ends_at=getattr(client, 'trial_ends_at', None),
        shopify_shared_secret=getattr(client, 'shopify_shared_secret', None),
        woocommerce_webhook_secret=getattr(client, 'woocommerce_webhook_secret', None),
        capi_signing_secret=getattr(client, 'capi_signing_secret', None),
        event_rules=getattr(client, 'event_rules', None),
    )


async def get_current_client(
    request: Request,
    x_api_key: str = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> CachedClient:
    """
    প্রতিটি /events রিকোয়েস্টে X-API-Key হেডার চেক করে
    ক্লায়েন্ট ভেরিফাই করে।
    In-memory cache দিয়ে DB query minimize করে (60s TTL)।
    CachedClient রিটার্ন করে — SQLAlchemy session-এ নির্ভর করে না।
    """
    if not x_api_key:
        user_session = request.cookies.get("buykori_client_session")
        if user_session:
            if not _origin_allowed_for_cookie_auth(request):
                raise HTTPException(status_code=403, detail="Origin is not allowed.")
            try:
                session_token = decrypt_token(user_session, allow_legacy_plaintext=False)
                session_result = await db.execute(
                    select(ClientSession).where(ClientSession.token_hash == hash_session_token(session_token))
                )
                client_session = session_result.scalar_one_or_none()
                if client_session and not client_session.revoked_at:
                    from datetime import datetime, timezone

                    expires_at = client_session.expires_at
                    if expires_at and expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if expires_at and expires_at > datetime.now(timezone.utc):
                        result = await db.execute(select(Client).where(Client.id == client_session.client_id))
                        session_client = result.scalar_one_or_none()
                        if session_client and session_client.is_active:
                            x_api_key = session_client.api_key
            except Exception:
                x_api_key = None

    if not x_api_key:
        encrypted_session = request.cookies.get("client_session")
        if encrypted_session:
            if not _origin_allowed_for_cookie_auth(request):
                raise HTTPException(status_code=403, detail="Origin is not allowed.")
            try:
                decrypted = decrypt_token(encrypted_session, allow_legacy_plaintext=False)
            except Exception:
                decrypted = None
            if decrypted and decrypted.startswith("client:"):
                import secrets
                try:
                    _, client_id_str, session_secret = decrypted.split(":", 2)
                    result = await db.execute(select(Client).where(Client.id == int(client_id_str)))
                    portal_client = result.scalar_one_or_none()

                    if portal_client:
                        expected_secret = getattr(portal_client, "portal_key", None)
                        if expected_secret and secrets.compare_digest(session_secret, expected_secret):
                            x_api_key = portal_client.api_key
                except (TypeError, ValueError):
                    pass
            else:
                x_api_key = decrypted

    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="API Key প্রয়োজন। X-API-Key header পাঠান।"
        )

    # ─── Cache থেকে চেক করো আগে ─────────────────────────────────────
    now = time.time()
    if x_api_key in _client_cache:
        cached, cached_at = _client_cache[x_api_key]
        if cached.api_key != x_api_key:
            # Alias/old-key cache entries must be revalidated so rotation grace cannot
            # be extended by the cache TTL.
            del _client_cache[x_api_key]
        elif now - cached_at < CACHE_TTL:
            # Cache hit — DB query বাদ!
            if await _client_cache_invalidated(cached.api_key, cached_at):
                del _client_cache[x_api_key]
            elif cached.is_active:
                return cached
            else:
                # Cache-এ আছে কিন্তু inactive — remove & reject
                del _client_cache[x_api_key]
                raise HTTPException(
                    status_code=401,
                    detail="Invalid বা Inactive API Key।"
                )

    # ─── Cache miss — DB query ─────────────────────────────────────────
    from sqlalchemy import or_
    result = await db.execute(
        select(Client).where(
            Client.is_active == True
        ).where(
            or_(Client.api_key == x_api_key, Client.old_api_key == x_api_key)
        )
    )
    clients = result.scalars().all()
    client = None
    authenticated_with_old_key = False

    for c in clients:
        if c.api_key == x_api_key:
            client = c
            break
        elif c.old_api_key == x_api_key and c.api_key_rotated_at:
            from datetime import timezone
            rotated_at = c.api_key_rotated_at if c.api_key_rotated_at.tzinfo else c.api_key_rotated_at.replace(tzinfo=timezone.utc)
            current_time = datetime.now(timezone.utc)
            if (current_time - rotated_at).total_seconds() <= 900:  # 15 minutes grace period
                client = c
                authenticated_with_old_key = True
                break

    if not client:
        raise HTTPException(
            status_code=401,
            detail="Invalid বা Inactive API Key।"
        )

    # ─── Cache-এ রাখো (plain dataclass — safe across sessions) ──────
    cached = _snapshot(client)
    if not authenticated_with_old_key:
        set_in_client_cache(x_api_key, cached)
    return cached
