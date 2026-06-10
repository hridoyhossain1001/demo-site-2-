import os
import secrets
import logging
import hmac
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Request, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, delete as sql_delete, select, func, text
from pydantic import BaseModel

from app.database import get_db
from app.models.client import Client
from app.models.client_session import ClientSession
from app.models.client_support_note import ClientSupportNote
from app.models.client_user import ClientUser
from app.models.courier_booking_job import CourierBookingJob
from app.models.courier_order import CourierOrder
from app.models.event_dedup import EventDedup
from app.models.event_log import EventLog
from app.models.event_outbox import EventOutbox
from app.models.failed_event import FailedEvent
from app.models.pending_event import PendingEvent
from app.models.site_binding import SiteBinding
from app.models.usage_counter import UsageCounter
from app.security import encrypt_token
from app.services.auth_service import verify_admin_password
from app.services.webhook_service import _webhook_url_allowed
from app.services.courier_booking_service import requeue_failed_booking_job
from app.dependencies import clear_client_cache
from app.utils.display import normalize_domain_input, display_domain_url, mask_secret
from app.routers.admin_views import log_admin_action
from app.limiter import limiter
from app.services.plan_service import (
    assign_paid_plan,
    default_monthly_event_limit,
    normalize_billing_status,
    normalize_plan_tier,
    plan_summary,
    record_trial_identity,
    require_trial_available,
    start_growth_trial,
)
from app.services.site_binding_service import (
    get_active_site_binding,
    release_site_binding,
    root_domain_for_site,
    transfer_site_binding,
)

logger = logging.getLogger(__name__)
router = APIRouter()

ADMIN_SESSION_COOKIE = os.getenv("ADMIN_SESSION_COOKIE", "buykori_admin_session")
ADMIN_COOKIE_DOMAIN = os.getenv("ADMIN_COOKIE_DOMAIN") or None
ADMIN_COOKIE_SECURE = os.getenv("ADMIN_COOKIE_SECURE", "true").lower() in ("1", "true", "yes")
ADMIN_COOKIE_SAMESITE = os.getenv("ADMIN_COOKIE_SAMESITE", "none").lower()
if ADMIN_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    ADMIN_COOKIE_SAMESITE = "none"
ADMIN_SESSION_SECONDS = int(os.getenv("ADMIN_SESSION_SECONDS", str(24 * 3600)))
ADMIN_CSRF_COOKIE = os.getenv("ADMIN_CSRF_COOKIE", "buykori_admin_csrf")
ADMIN_CSRF_HEADER = os.getenv("ADMIN_CSRF_HEADER", "X-Admin-CSRF-Token")
ADMIN_CSRF_PREFIX = "admin-csrf"
SAFE_ADMIN_METHODS = {"GET", "HEAD", "OPTIONS"}

class AdminClientCreate(BaseModel):
    name: str
    pixel_id: str
    access_token: str
    test_event_code: str | None = None
    domain: str | None = None
    tiktok_pixel_id: str | None = None
    tiktok_access_token: str | None = None
    tiktok_test_event_code: str | None = None
    ga4_measurement_id: str | None = None
    ga4_api_secret: str | None = None
    enable_facebook: bool = True
    enable_tiktok: bool = True
    enable_ga4: bool = True
    deferred_purchase: bool = False
    webhook_url: str | None = None

class AdminClientUpdate(BaseModel):
    name: str | None = None
    domain: str | None = None
    monthly_limit: int | None = None
    rate_limit: int | None = None
    daily_quota: int | None = None
    is_active: bool | None = None
    enable_facebook: bool | None = None
    enable_tiktok: bool | None = None
    enable_ga4: bool | None = None
    deferred_purchase: bool | None = None
    webhook_url: str | None = None
    test_event_code: str | None = None
    tiktok_test_event_code: str | None = None
    plan_tier: str | None = None
    billing_status: str | None = None

class AdminPlanUpdate(BaseModel):
    action: str
    plan_tier: str | None = None
    monthly_limit: int | None = None
    billing_status: str | None = None

class AdminSupportNoteCreate(BaseModel):
    note: str


class AdminSiteBindingRelease(BaseModel):
    reason: str


class AdminSiteBindingTransfer(BaseModel):
    site_host: str
    target_client_id: int
    reason: str

import jwt as pyjwt

def create_jwt(payload: dict, secret: str) -> str:
    """PyJWT à¦¦à¦¿à¦¯à¦¼à§‡ HS256 JWT token à¦¤à§ˆà¦°à¦¿ à¦•à¦°à§‹à¥¤"""
    return pyjwt.encode(payload, secret, algorithm="HS256")

def decode_jwt(token: str, secret: str) -> dict:
    """PyJWT à¦¦à¦¿à¦¯à¦¼à§‡ JWT token decode à¦“ verify à¦•à¦°à§‹à¥¤"""
    try:
        return pyjwt.decode(token, secret, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        raise ValueError("Token expired")
    except pyjwt.InvalidTokenError as e:
        raise ValueError(f"Token decoding failed: {e}")

def verify_admin_api_key(
    request: Request,
    authorization: str = Header(None, alias="Authorization"),
    x_admin_api_key: str = Header(None, alias="X-Admin-API-Key"),
) -> str:
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="Admin API key is not configured")

    # Evaluate X-Admin-API-Key first (HMAC-based key has higher priority and should not be bypassed by Bearer exceptions)
    if x_admin_api_key and hmac.compare_digest(x_admin_api_key, admin_key):
        return "admin-api-key"

    # Evaluate Bearer token next
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        try:
            payload = decode_jwt(token, admin_key)
            if payload.get("sub") == "admin":
                return "admin-bearer"
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}")

    cookie_token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if cookie_token:
        try:
            payload = decode_jwt(cookie_token, admin_key)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid or expired admin session")
        if payload.get("sub") == "admin":
            require_admin_csrf(request, admin_key)
            return "admin-cookie"

    raise HTTPException(status_code=403, detail="Admin access required")


def create_admin_csrf_token(secret: str) -> str:
    issued_at = int(time.time())
    nonce = secrets.token_urlsafe(24)
    payload = f"{ADMIN_CSRF_PREFIX}:{issued_at}:{nonce}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), "sha256").hexdigest()
    return f"{issued_at}:{nonce}:{signature}"


def verify_admin_csrf_token(token: str, secret: str) -> bool:
    try:
        issued_raw, nonce, signature = token.split(":", 2)
        issued_at = int(issued_raw)
    except (TypeError, ValueError):
        return False

    now = int(time.time())
    if issued_at > now + 60:
        return False
    if issued_at < now - ADMIN_SESSION_SECONDS:
        return False

    payload = f"{ADMIN_CSRF_PREFIX}:{issued_at}:{nonce}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), "sha256").hexdigest()
    return hmac.compare_digest(signature, expected)


def require_admin_csrf(request: Request, secret: str) -> None:
    if request.method.upper() in SAFE_ADMIN_METHODS:
        return

    header_token = request.headers.get(ADMIN_CSRF_HEADER)
    cookie_token = request.cookies.get(ADMIN_CSRF_COOKIE)
    if not header_token or not cookie_token:
        raise HTTPException(status_code=403, detail="Admin CSRF token is required")
    if not hmac.compare_digest(header_token, cookie_token):
        raise HTTPException(status_code=403, detail="Admin CSRF token mismatch")
    if not verify_admin_csrf_token(header_token, secret):
        raise HTTPException(status_code=403, detail="Invalid or expired admin CSRF token")


def set_admin_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        secure=ADMIN_COOKIE_SECURE,
        samesite=ADMIN_COOKIE_SAMESITE,
        domain=ADMIN_COOKIE_DOMAIN,
        path="/",
    )


def set_admin_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        ADMIN_CSRF_COOKIE,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        httponly=False,
        secure=ADMIN_COOKIE_SECURE,
        samesite=ADMIN_COOKIE_SAMESITE,
        domain=ADMIN_COOKIE_DOMAIN,
        path="/",
    )


def clear_admin_session_cookie(response: Response) -> None:
    response.delete_cookie(
        ADMIN_SESSION_COOKIE,
        domain=ADMIN_COOKIE_DOMAIN,
        path="/",
        secure=ADMIN_COOKIE_SECURE,
        samesite=ADMIN_COOKIE_SAMESITE,
        httponly=True,
    )


def clear_admin_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        ADMIN_CSRF_COOKIE,
        domain=ADMIN_COOKIE_DOMAIN,
        path="/",
        secure=ADMIN_COOKIE_SECURE,
        samesite=ADMIN_COOKIE_SAMESITE,
        httponly=False,
    )


def client_to_api_dict(client: Client, event_total: int = 0, last_event_at=None, mask_keys: bool = False) -> dict:
    api_key = mask_secret(client.api_key) if mask_keys else client.api_key
    public_key = getattr(client, "public_key", None)
    if public_key and mask_keys:
        public_key = mask_secret(public_key)
    portal_key = getattr(client, "portal_key", None)
    if portal_key and mask_keys:
        portal_key = mask_secret(portal_key)
    plan = plan_summary(client)

    return {
        "id": client.id,
        "name": client.name,
        "domain": client.domain,
        "display_domain": display_domain_url(client.domain),
        "is_active": bool(client.is_active),
        "api_key": api_key,
        "public_key": public_key,
        "portal_key": portal_key,
        "pixel_id": client.pixel_id,
        "test_event_code": client.test_event_code,
        "monthly_limit": getattr(client, "monthly_limit", None),
        "orders_quota": plan["ordersQuota"],
        "rate_limit": client.rate_limit,
        "daily_quota": client.daily_quota,
        "enable_facebook": getattr(client, "enable_facebook", True),
        "enable_tiktok": getattr(client, "enable_tiktok", True),
        "enable_ga4": getattr(client, "enable_ga4", True),
        "deferred_purchase": getattr(client, "deferred_purchase", False),
        "plan_tier": plan["tier"],
        "base_plan_tier": plan["baseTier"],
        "billing_status": plan["billingStatus"],
        "is_trial": plan["isTrial"],
        "trial_ends_at": plan["trialEndsAt"],
        "trial_days_remaining": plan["trialDaysRemaining"],
        "webhook_url": getattr(client, "webhook_url", None),
        "tiktok_pixel_id": getattr(client, "tiktok_pixel_id", None),
        "tiktok_test_event_code": getattr(client, "tiktok_test_event_code", None),
        "ga4_measurement_id": getattr(client, "ga4_measurement_id", None),
        "created_at": client.created_at.isoformat() if client.created_at else None,
        "event_total": int(event_total or 0),
        "last_event_at": last_event_at.isoformat() if last_event_at else None,
    }


def site_binding_to_api_dict(binding: SiteBinding, client_name: str | None = None) -> dict:
    return {
        "id": binding.id,
        "client_id": binding.client_id,
        "client_name": client_name,
        "site_host": binding.site_host,
        "root_domain": binding.root_domain,
        "installation_id": mask_secret(binding.installation_id) if binding.installation_id else None,
        "status": binding.status,
        "source": binding.source,
        "connected_at": binding.connected_at.isoformat() if binding.connected_at else None,
        "last_seen_at": binding.last_seen_at.isoformat() if binding.last_seen_at else None,
        "last_event_at": binding.last_event_at.isoformat() if binding.last_event_at else None,
        "released_at": binding.released_at.isoformat() if binding.released_at else None,
        "released_by": binding.released_by,
        "release_reason": binding.release_reason,
        "created_at": binding.created_at.isoformat() if binding.created_at else None,
    }


def domain_includes_root(domain_value: str | None, root_domain: str) -> bool:
    existing = [part.strip().lower() for part in str(domain_value or "").split(",") if part.strip()]
    return any(root_domain_for_site(part) == root_domain for part in existing)


def append_domain_root(domain_value: str | None, root_domain: str) -> str:
    existing = [part.strip().lower() for part in str(domain_value or "").split(",") if part.strip()]
    if root_domain not in existing:
        existing.append(root_domain)
    return normalize_domain_input(",".join(existing))

async def validate_webhook_url_or_400(webhook_url: str | None) -> str | None:
    clean_webhook_url = webhook_url.strip() if webhook_url and webhook_url.strip() else None
    if not clean_webhook_url:
        return None
    parsed_webhook = urlparse(clean_webhook_url)
    if parsed_webhook.scheme not in ("https", "http") or not parsed_webhook.netloc:
        raise HTTPException(status_code=400, detail="Webhook URL must be a valid http(s) URL.")
    if not await _webhook_url_allowed(clean_webhook_url):
        raise HTTPException(status_code=400, detail="Webhook URL is not allowed.")
    return clean_webhook_url


async def courier_booking_queue_counts(db: AsyncSession) -> dict:
    rows = await db.execute(
        select(CourierBookingJob.status, func.count(CourierBookingJob.id))
        .group_by(CourierBookingJob.status)
    )
    counts = {status: int(count or 0) for status, count in rows}
    queued_oldest_r = await db.execute(
        select(func.min(CourierBookingJob.created_at)).where(CourierBookingJob.status == "queued")
    )
    processing_oldest_r = await db.execute(
        select(func.min(func.coalesce(CourierBookingJob.locked_at, CourierBookingJob.created_at)))
        .where(CourierBookingJob.status == "processing")
    )

    def age_seconds(value) -> int:
        if not value:
            return 0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - value).total_seconds()))

    def positive_int_env(name: str, default: int) -> int:
        try:
            return max(1, int(os.getenv(name, str(default))))
        except ValueError:
            return default

    queued_warn_seconds = positive_int_env("COURIER_BOOKING_QUEUE_WARN_SECONDS", 60)
    processing_warn_seconds = positive_int_env("COURIER_BOOKING_PROCESSING_WARN_SECONDS", 600)
    oldest_queued_age_seconds = age_seconds(queued_oldest_r.scalar())
    oldest_processing_age_seconds = age_seconds(processing_oldest_r.scalar())
    alerts = []
    if counts.get("dead", 0):
        alerts.append({"code": "dead_letter_jobs", "severity": "critical", "count": counts["dead"]})
    if oldest_processing_age_seconds >= processing_warn_seconds:
        alerts.append({
            "code": "processing_stalled",
            "severity": "critical",
            "age_seconds": oldest_processing_age_seconds,
        })
    if oldest_queued_age_seconds >= queued_warn_seconds:
        alerts.append({
            "code": "queued_delayed",
            "severity": "warning",
            "age_seconds": oldest_queued_age_seconds,
        })
    alert_status = "critical" if any(alert["severity"] == "critical" for alert in alerts) else "warning" if alerts else "healthy"
    return {
        "queued": counts.get("queued", 0),
        "processing": counts.get("processing", 0),
        "sent": counts.get("sent", 0),
        "dead": counts.get("dead", 0),
        "cancelled": counts.get("cancelled", 0),
        "total": sum(counts.values()),
        "oldest_queued_age_seconds": oldest_queued_age_seconds,
        "oldest_processing_age_seconds": oldest_processing_age_seconds,
        "queued_warn_seconds": queued_warn_seconds,
        "processing_warn_seconds": processing_warn_seconds,
        "alert_status": alert_status,
        "alerts": alerts,
    }


def courier_booking_job_to_api_dict(job: CourierBookingJob, order_id: str | None) -> dict:
    return {
        "id": job.id,
        "client_id": job.client_id,
        "order_id": order_id,
        "courier_order_id": job.courier_order_id,
        "provider": job.provider,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
        "locked_at": job.locked_at.isoformat() if job.locked_at else None,
        "locked_by": job.locked_by,
        "last_error": job.last_error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "sent_at": job.sent_at.isoformat() if job.sent_at else None,
    }


def _as_utc(value: datetime | None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _courier_configured(client: Client) -> bool:
    return bool(
        getattr(client, "default_courier", None)
        or getattr(client, "steadfast_api_key", None)
        or getattr(client, "pathao_api_key", None)
        or getattr(client, "redx_access_token", None)
    )


def _client_health_score(client: Client, owner: ClientUser | None, stats: dict, plan: dict) -> dict:
    now = datetime.now(timezone.utc)
    last_event_at = _as_utc(stats.get("last_event_at"))
    total_events = int(stats.get("event_total") or 0)
    failed_events = int(stats.get("failed_events") or 0)
    purchase_events = int(stats.get("purchase_events") or 0)
    success_rate = 100.0 if total_events <= 0 else max(0.0, ((total_events - failed_events) / total_events) * 100)

    score = 0
    reasons = []
    if client.is_active:
        score += 10
    else:
        reasons.append("Client inactive")
    if owner and owner.phone_number:
        score += 10
    else:
        reasons.append("Phone missing")
    if client.domain:
        score += 10
    else:
        reasons.append("Domain missing")
    if total_events > 0:
        score += 25
    else:
        reasons.append("No events received")
    if last_event_at and (now - last_event_at) <= timedelta(days=3):
        score += 20
    elif total_events > 0:
        reasons.append("No recent events")
    if purchase_events > 0:
        score += 10
    else:
        reasons.append("No purchase event yet")
    if total_events > 0 and success_rate >= 95:
        score += 10
    elif total_events > 0:
        reasons.append("Failure rate needs review")
    if _courier_configured(client):
        score += 15
    else:
        reasons.append("Courier not configured")

    score = min(100, score)
    status = "healthy" if score >= 80 else "warning" if score >= 50 else "critical"
    if plan.get("isTrial") and int(plan.get("trialDaysRemaining") or 0) <= 3:
        reasons.append("Trial ending soon")
    return {
        "score": score,
        "status": status,
        "success_rate": round(success_rate, 1),
        "reasons": reasons[:5],
    }


def _onboarding_funnel(client: Client, owner: ClientUser | None, stats: dict) -> list[dict]:
    total_events = int(stats.get("event_total") or 0)
    pageviews = int(stats.get("pageview_events") or 0)
    purchases = int(stats.get("purchase_events") or 0)
    courier_ready = _courier_configured(client)
    return [
        {"key": "signed_up", "label": "Signed up", "done": True},
        {"key": "phone_collected", "label": "Phone collected", "done": bool(owner and owner.phone_number)},
        {"key": "domain_added", "label": "Domain added", "done": bool(client.domain)},
        {"key": "tracking_configured", "label": "Tracking configured", "done": bool(total_events > 0 or client.pixel_id != "0")},
        {"key": "first_pageview", "label": "First PageView", "done": pageviews > 0},
        {"key": "first_purchase", "label": "First Purchase", "done": purchases > 0},
        {"key": "courier_configured", "label": "Courier configured", "done": courier_ready},
    ]


def _trial_followup(client: Client, owner: ClientUser | None, stats: dict, plan: dict) -> dict | None:
    total_events = int(stats.get("event_total") or 0)
    days_remaining = int(plan.get("trialDaysRemaining") or 0)
    billing_status = str(plan.get("billingStatus") or "").lower()
    trial_ends_at = _as_utc(getattr(client, "trial_ends_at", None))
    now = datetime.now(timezone.utc)
    if plan.get("isTrial") and days_remaining <= 3:
        return {"priority": "high", "reason": f"Trial ends in {days_remaining} day(s)", "action": "Call before trial ends"}
    if billing_status == "trial" and trial_ends_at and trial_ends_at <= now:
        return {"priority": "high", "reason": "Trial expired", "action": "Call for feedback and conversion"}
    if total_events == 0:
        return {"priority": "medium", "reason": "Signed up but no events", "action": "Help finish setup"}
    if plan.get("isTrial") and total_events >= 100:
        return {"priority": "medium", "reason": "Active trial usage", "action": "Ask about upgrade fit"}
    if owner and not owner.phone_number:
        return {"priority": "low", "reason": "Phone missing", "action": "Collect contact number"}
    return None


def _support_note_to_api(note: ClientSupportNote) -> dict:
    return {
        "id": note.id,
        "client_id": note.client_id,
        "note": note.note,
        "created_by": note.created_by,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


async def _client_intelligence(db: AsyncSession) -> list[dict]:
    clients_r = await db.execute(select(Client).order_by(Client.created_at.desc()))
    clients = clients_r.scalars().all()
    owners_r = await db.execute(
        select(ClientUser).where(ClientUser.role == "owner", ClientUser.is_active == True)
    )
    owners = {owner.client_id: owner for owner in owners_r.scalars().all()}
    stats_r = await db.execute(
        select(
            EventLog.client_id,
            func.coalesce(func.sum(EventLog.event_count), 0).label("event_total"),
            func.coalesce(func.sum(case((EventLog.status == "failed", EventLog.event_count), else_=0)), 0).label("failed_events"),
            func.max(EventLog.created_at).label("last_event_at"),
            func.coalesce(func.sum(case((EventLog.event_name.ilike("%PageView%"), EventLog.event_count), else_=0)), 0).label("pageview_events"),
            func.coalesce(func.sum(case((EventLog.event_name.ilike("%Purchase%"), EventLog.event_count), else_=0)), 0).label("purchase_events"),
        )
        .group_by(EventLog.client_id)
    )
    stats_by_client = {
        client_id: {
            "event_total": int(event_total or 0),
            "failed_events": int(failed_events or 0),
            "last_event_at": last_event_at,
            "pageview_events": int(pageview_events or 0),
            "purchase_events": int(purchase_events or 0),
        }
        for client_id, event_total, failed_events, last_event_at, pageview_events, purchase_events in stats_r
    }
    note_counts_r = await db.execute(
        select(ClientSupportNote.client_id, func.count(ClientSupportNote.id), func.max(ClientSupportNote.created_at))
        .group_by(ClientSupportNote.client_id)
    )
    note_meta = {
        client_id: {"support_note_count": int(count or 0), "last_support_note_at": latest}
        for client_id, count, latest in note_counts_r
    }

    rows = []
    for client in clients:
        owner = owners.get(client.id)
        stats = stats_by_client.get(client.id, {})
        plan = plan_summary(client)
        health = _client_health_score(client, owner, stats, plan)
        followup = _trial_followup(client, owner, stats, plan)
        meta = note_meta.get(client.id, {})
        rows.append({
            "client": client_to_api_dict(client, stats.get("event_total", 0), stats.get("last_event_at"), mask_keys=True),
            "owner": {
                "email": owner.email if owner else None,
                "full_name": owner.full_name if owner else None,
                "phone_number": owner.phone_number if owner else None,
                "last_login_at": owner.last_login_at.isoformat() if owner and owner.last_login_at else None,
            },
            "health_score": health,
            "onboarding_funnel": _onboarding_funnel(client, owner, stats),
            "trial_followup": followup,
            "support_note_count": meta.get("support_note_count", 0),
            "last_support_note_at": meta.get("last_support_note_at").isoformat() if meta.get("last_support_note_at") else None,
        })
    return rows


def _read_linux_memory() -> dict:
    try:
        values = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        used = max(0, total - available)
        percent = round((used / total) * 100, 1) if total else None
        return {"total_bytes": total, "available_bytes": available, "used_bytes": used, "used_percent": percent}
    except Exception:
        return {"total_bytes": None, "available_bytes": None, "used_bytes": None, "used_percent": None}


def _read_cpu_metrics(load_avg: list[float] | None) -> dict:
    cores = os.cpu_count() or 1
    load_1m = load_avg[0] if load_avg else 0.0
    used_percent = round(min(100.0, (load_1m / cores) * 100), 1) if load_1m is not None else 0.0
    return {
        "used_percent": used_percent,
        "cores": cores,
        "load_1m_per_core": round(load_1m / cores, 3) if load_1m is not None and cores else None,
    }


def _read_server_runtime() -> dict:
    import shutil
    import socket
    import sys

    # Try using psutil for memory, cpu, and disk details
    psutil_data = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        cpu_pct = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count(logical=True)
        psutil_data = {
            "memory": {
                "total_bytes": vm.total,
                "used_bytes": vm.used,
                "free_bytes": vm.free,
                "used_percent": vm.percent,
            },
            "cpu": {
                "percent": cpu_pct,
                "used_percent": cpu_pct,
                "cores": cpu_count,
            },
            "disk": {
                "total_bytes": psutil.disk_usage("/").total,
                "used_bytes": psutil.disk_usage("/").used,
                "free_bytes": psutil.disk_usage("/").free,
                "used_percent": psutil.disk_usage("/").percent,
            },
            "process": {
                "pid": os.getpid(),
                "rss_bytes": psutil.Process(os.getpid()).memory_info().rss,
            }
        }
    except Exception:
        pass

    load_avg = None
    try:
        load_avg = list(os.getloadavg())
    except Exception:
        pass
    uptime_seconds = None
    try:
        # Check psutil for boot time
        if "psutil" in sys.modules:
            import psutil
            uptime_seconds = time.time() - psutil.boot_time()
        else:
            with open("/proc/uptime", "r", encoding="utf-8") as handle:
                uptime_seconds = float(handle.read().split()[0])
    except Exception:
        pass
    process_rss_bytes = psutil_data.get("process", {}).get("rss_bytes")
    if process_rss_bytes is None:
        try:
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            process_rss_bytes = int(rss * 1024)
        except Exception:
            pass
    disk = shutil.disk_usage("/")
    return {
        "hostname": socket.gethostname(),
        "load_average": load_avg,
        "cpu": psutil_data.get("cpu") or _read_cpu_metrics(load_avg),
        "uptime_seconds": uptime_seconds,
        "memory": psutil_data.get("memory") or _read_linux_memory(),
        "disk": psutil_data.get("disk") or {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": round((disk.used / disk.total) * 100, 1) if disk.total else None,
        },
        "process": {
            "pid": os.getpid(),
            "rss_bytes": process_rss_bytes,
        },
    }


async def _queue_status_counts(db: AsyncSession, model, status_column) -> dict:
    rows = await db.execute(select(status_column, func.count(model.id)).group_by(status_column))
    return {status: int(count or 0) for status, count in rows}


async def _worker_monitor(db: AsyncSession) -> dict:
    event_outbox = await _queue_status_counts(db, EventOutbox, EventOutbox.status)
    failed_events = await _queue_status_counts(db, FailedEvent, FailedEvent.status)
    courier_queue = await courier_booking_queue_counts(db)
    active_event_jobs = event_outbox.get("queued", 0) + event_outbox.get("processing", 0)
    dead_events = event_outbox.get("dead", 0) + failed_events.get("dead", 0)
    status = "critical" if dead_events or courier_queue["alert_status"] == "critical" else "warning" if active_event_jobs > 100 or courier_queue["alert_status"] == "warning" else "healthy"

    # Processed rate and latency metrics over the last 5 minutes
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import and_
    five_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    sent_items_r = await db.execute(
        select(EventOutbox.created_at, EventOutbox.sent_at)
        .where(and_(EventOutbox.status == "sent", EventOutbox.sent_at >= five_mins_ago))
    )
    sent_items = sent_items_r.all()

    processed_count = len(sent_items)
    processed_rate_per_min = round(processed_count / 5.0, 2)

    latencies = []
    for created_at, sent_at in sent_items:
        if created_at and sent_at:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            latencies.append((sent_at - created_at).total_seconds())

    avg_latency_sec = round(sum(latencies) / len(latencies), 2) if latencies else 0.0

    from app.services.redis_pool import redis_fallback_counts
    fallback_counts = redis_fallback_counts()

    return {
        "status": status,
        "event_outbox": event_outbox,
        "failed_events": failed_events,
        "courier_booking_queue": courier_queue,
        "active_event_jobs": active_event_jobs,
        "dead_event_jobs": dead_events,
        "processed_rate_per_min": processed_rate_per_min,
        "avg_latency_sec": avg_latency_sec,
        "redis_fallback_total": fallback_counts,
    }


@router.get("/admin/api/summary")
async def admin_api_summary(
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    clients_r = await db.execute(select(Client))
    clients = clients_r.scalars().all()
    events_r = await db.execute(select(func.coalesce(func.sum(EventLog.event_count), 0)))
    total_events = int(events_r.scalar() or 0)
    failed_r = await db.execute(
        select(func.coalesce(func.sum(EventLog.event_count), 0)).where(EventLog.status == "failed")
    )
    failed_events = int(failed_r.scalar() or 0)
    courier_booking_queue = await courier_booking_queue_counts(db)
    return {
        "status": "success",
        "total_clients": len(clients),
        "active_clients": sum(1 for c in clients if c.is_active),
        "inactive_clients": sum(1 for c in clients if not c.is_active),
        "total_events": total_events,
        "failed_events": failed_events,
        "courier_booking_queue": courier_booking_queue,
    }


@router.get("/admin/api/client-intelligence")
async def admin_api_client_intelligence(
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    rows = await _client_intelligence(db)
    followups = [row for row in rows if row.get("trial_followup")]
    priority_order = {"high": 0, "medium": 1, "low": 2}
    followups.sort(key=lambda row: priority_order.get(row["trial_followup"]["priority"], 9))
    return {
        "status": "success",
        "clients": rows,
        "trial_followups": followups,
    }


@router.get("/admin/api/server-health")
async def admin_api_server_health(
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    db_ok = False
    redis_ok = False
    redis_fallback_total = {}
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("Admin server-health DB check failed")
    try:
        from app.services.redis_pool import get_redis, redis_fallback_counts

        redis = get_redis()
        redis_fallback_total = redis_fallback_counts()
        if redis:
            await redis.ping()
            redis_ok = True
    except Exception:
        logger.exception("Admin server-health Redis check failed")

    worker_monitor = await _worker_monitor(db)
    status = "healthy" if db_ok and redis_ok and worker_monitor["status"] == "healthy" else "warning"
    if not db_ok or worker_monitor["status"] == "critical":
        status = "critical"
    return {
        "status": status,
        "db": db_ok,
        "redis": redis_ok,
        "redis_fallback_total": redis_fallback_total,
        "server": _read_server_runtime(),
        "worker_monitor": worker_monitor,
    }


@router.get("/admin/api/courier-booking-queue")
async def admin_api_courier_booking_queue(
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    rows = await db.execute(
        select(CourierBookingJob, CourierOrder.order_id)
        .outerjoin(CourierOrder, CourierOrder.id == CourierBookingJob.courier_order_id)
        .order_by(CourierBookingJob.created_at.desc())
        .limit(limit)
    )
    return {
        "status": "success",
        "counts": await courier_booking_queue_counts(db),
        "jobs": [courier_booking_job_to_api_dict(job, order_id) for job, order_id in rows],
    }


@router.post("/admin/api/courier-booking-queue/{job_id}/retry")
@limiter.limit("30/minute")
async def admin_api_retry_courier_booking_job(
    job_id: int,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    try:
        job = await requeue_failed_booking_job(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not job:
        raise HTTPException(status_code=404, detail="Courier booking job not found.")

    await db.commit()
    await log_admin_action(
        db,
        request,
        actor,
        "courier_booking.retry_queued",
        job.client_id,
        f"Courier booking job {job.id} requeued by operator",
    )
    await db.commit()
    return {"status": "success", "job_id": job.id, "job_status": job.status}


@router.get("/admin/api/site-bindings")
async def admin_api_site_bindings(
    status_filter: str | None = Query("active", alias="status"),
    client_id: int | None = Query(None),
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status_filter and status_filter != "all":
        filters.append(SiteBinding.status == status_filter)
    if client_id is not None:
        filters.append(SiteBinding.client_id == client_id)
    stmt = (
        select(SiteBinding, Client.name)
        .outerjoin(Client, Client.id == SiteBinding.client_id)
        .order_by(SiteBinding.updated_at.desc(), SiteBinding.id.desc())
        .limit(250)
    )
    if filters:
        stmt = stmt.where(*filters)
    rows = await db.execute(stmt)
    return {
        "status": "success",
        "bindings": [site_binding_to_api_dict(binding, client_name) for binding, client_name in rows],
    }


@router.post("/admin/api/site-bindings/{binding_id}/release")
@limiter.limit("20/minute")
async def admin_api_release_site_binding(
    binding_id: int,
    payload: AdminSiteBindingRelease,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    reason = payload.reason.strip()
    if not reason or len(reason) > 500:
        raise HTTPException(status_code=400, detail="Release reason must be 1-500 characters.")
    binding = await release_site_binding(db, binding_id=binding_id, actor=actor, reason=reason)
    await log_admin_action(
        db,
        request,
        actor,
        "site_binding.released",
        binding.client_id,
        f"Released {binding.root_domain}: {reason}",
    )
    await db.commit()
    return {"status": "success", "binding": site_binding_to_api_dict(binding)}


@router.post("/admin/api/site-bindings/transfer")
@limiter.limit("20/minute")
async def admin_api_transfer_site_binding(
    payload: AdminSiteBindingTransfer,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    reason = payload.reason.strip()
    if not reason or len(reason) > 500:
        raise HTTPException(status_code=400, detail="Transfer reason must be 1-500 characters.")
    site_host = payload.site_host.strip().lower()
    binding = await get_active_site_binding(db, site_host)
    if not binding:
        raise HTTPException(status_code=404, detail="Active site binding not found.")
    old_client_id = binding.client_id
    new_binding = await transfer_site_binding(
        db,
        site_host=site_host,
        target_client_id=payload.target_client_id,
        actor=actor,
        reason=reason,
    )
    target = await db.get(Client, payload.target_client_id)
    if target and not domain_includes_root(target.domain, new_binding.root_domain):
        target.domain = append_domain_root(target.domain, new_binding.root_domain)
        clear_client_cache(target.api_key)
    await log_admin_action(
        db,
        request,
        actor,
        "site_binding.transferred",
        payload.target_client_id,
        f"Transferred {new_binding.root_domain} from client {old_client_id} to {payload.target_client_id}: {reason}",
    )
    await db.commit()
    return {"status": "success", "binding": site_binding_to_api_dict(new_binding, target.name if target else None)}


@router.get("/admin/api/clients")
async def admin_api_clients(
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(
            Client,
            func.coalesce(func.sum(EventLog.event_count), 0).label("event_total"),
            func.max(EventLog.created_at).label("last_event_at"),
        )
        .outerjoin(EventLog, EventLog.client_id == Client.id)
        .group_by(Client.id)
        .order_by(Client.created_at.desc())
    )
    return {
        "status": "success",
        "clients": [client_to_api_dict(client, event_total, last_event_at, mask_keys=True) for client, event_total, last_event_at in rows],
    }

@router.post("/admin/api/clients")
@limiter.limit("10/minute")
async def admin_api_create_client(
    payload: AdminClientCreate,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    name = payload.name.strip()
    pixel_id = payload.pixel_id.strip()
    access_token = payload.access_token.strip()
    if not name or len(name) > 100:
        raise HTTPException(status_code=400, detail="Client name must be 1-100 characters.")
    if not pixel_id.isdigit():
        raise HTTPException(status_code=400, detail="Pixel ID must be numeric.")
    if len(access_token) < 10:
        raise HTTPException(status_code=400, detail="Access token must be at least 10 characters.")

    client = Client(
        name=name,
        pixel_id=pixel_id,
        access_token=encrypt_token(access_token),
        test_event_code=payload.test_event_code.strip() if payload.test_event_code else None,
        domain=normalize_domain_input(payload.domain),
        api_key=secrets.token_urlsafe(32),
        public_key=secrets.token_urlsafe(24),
        portal_key=secrets.token_urlsafe(24),
        enable_facebook=payload.enable_facebook,
        enable_tiktok=payload.enable_tiktok,
        enable_ga4=payload.enable_ga4,
        tiktok_pixel_id=payload.tiktok_pixel_id.strip() if payload.tiktok_pixel_id else None,
        tiktok_access_token=encrypt_token(payload.tiktok_access_token.strip()) if payload.tiktok_access_token else None,
        tiktok_test_event_code=payload.tiktok_test_event_code.strip() if payload.tiktok_test_event_code else None,
        ga4_measurement_id=payload.ga4_measurement_id.strip() if payload.ga4_measurement_id else None,
        ga4_api_secret=encrypt_token(payload.ga4_api_secret.strip()) if payload.ga4_api_secret else None,
        deferred_purchase=payload.deferred_purchase,
        webhook_url=await validate_webhook_url_or_400(payload.webhook_url),
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    await log_admin_action(db, request, actor, "client.api_added", client.id, f"Client {name} added from admin frontend")
    await db.commit()
    return {"status": "success", "client": client_to_api_dict(client)}

@router.patch("/admin/api/clients/{client_id}")
@limiter.limit("20/minute")
async def admin_api_update_client(
    client_id: int,
    payload: AdminClientUpdate,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    old_api_key = client.api_key
    if payload.name is not None:
        clean_name = payload.name.strip()
        if not clean_name or len(clean_name) > 100:
            raise HTTPException(status_code=400, detail="Client name must be 1-100 characters.")
        client.name = clean_name
    if payload.domain is not None:
        client.domain = normalize_domain_input(payload.domain)
    if payload.rate_limit is not None:
        if payload.rate_limit < 0:
            raise HTTPException(status_code=400, detail="Rate limit cannot be negative.")
        client.rate_limit = payload.rate_limit
    if payload.daily_quota is not None:
        if payload.daily_quota < 0:
            raise HTTPException(status_code=400, detail="Daily quota cannot be negative.")
        client.daily_quota = payload.daily_quota
    if payload.is_active is not None:
        client.is_active = payload.is_active
    if payload.monthly_limit is not None and payload.monthly_limit < 0:
        raise HTTPException(status_code=400, detail="Monthly limit cannot be negative.")

    if payload.plan_tier is not None or payload.billing_status is not None:
        requested_tier = (payload.plan_tier or client.plan_tier).strip().lower()
        normalized_tier = normalize_plan_tier(requested_tier)
        if requested_tier != normalized_tier:
            raise HTTPException(status_code=400, detail="Plan tier must be free, growth, scale, or agency.")
        billing_status = normalize_billing_status(payload.billing_status or client.billing_status)
        if billing_status == "trial":
            await require_trial_available(
                db,
                domain=client.domain,
                pixel_id=client.pixel_id,
                exclude_client_id=client.id,
            )
            start_growth_trial(client)
            await record_trial_identity(db, client, source="admin_edit")
        elif billing_status == "free" or normalized_tier == "free":
            assign_paid_plan(client, "free")
        else:
            assign_paid_plan(client, normalized_tier, payload.monthly_limit, billing_status)
    elif payload.monthly_limit is not None:
        client.monthly_limit = payload.monthly_limit
    for field in ("enable_facebook", "enable_tiktok", "enable_ga4", "deferred_purchase"):
        value = getattr(payload, field)
        if value is not None:
            setattr(client, field, value)
    if payload.webhook_url is not None:
        client.webhook_url = await validate_webhook_url_or_400(payload.webhook_url)
    if payload.test_event_code is not None:
        client.test_event_code = payload.test_event_code.strip() or None
    if payload.tiktok_test_event_code is not None:
        client.tiktok_test_event_code = payload.tiktok_test_event_code.strip() or None

    await db.commit()
    await db.refresh(client)
    clear_client_cache(old_api_key)
    await log_admin_action(db, request, actor, "client.api_updated", client.id, f"Client {client.name} updated from admin frontend")
    await db.commit()
    return {"status": "success", "client": client_to_api_dict(client)}

@router.post("/admin/api/clients/{client_id}/plan")
@limiter.limit("20/minute")
async def admin_api_update_plan(
    client_id: int,
    payload: AdminPlanUpdate,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    action = payload.action.strip().lower()
    old_api_key = client.api_key
    if action == "start_trial":
        await require_trial_available(db, domain=client.domain, pixel_id=client.pixel_id, exclude_client_id=client.id)
        start_growth_trial(client)
        await record_trial_identity(db, client, source="admin")
        log_action = "client.plan_trial_started"
        details = "Growth trial started from admin API"
    elif action == "cancel":
        assign_paid_plan(client, "free")
        log_action = "client.plan_cancelled"
        details = "Plan cancelled to Free from admin API"
    elif action == "confirm":
        requested_tier = (payload.plan_tier or "growth").strip().lower()
        normalized_tier = normalize_plan_tier(requested_tier)
        if requested_tier != normalized_tier or normalized_tier == "free":
            raise HTTPException(status_code=400, detail="Confirmed plan must be growth, scale, or agency.")
        if payload.monthly_limit is not None and payload.monthly_limit < 0:
            raise HTTPException(status_code=400, detail="Monthly limit cannot be negative.")
        assign_paid_plan(client, normalized_tier, payload.monthly_limit, payload.billing_status)
        log_action = "client.plan_confirmed"
        details = f"{normalized_tier.title()} plan confirmed from admin API"
    else:
        raise HTTPException(status_code=400, detail="Action must be start_trial, confirm, or cancel.")

    await db.commit()
    await db.refresh(client)
    clear_client_cache(old_api_key)
    await log_admin_action(db, request, actor, log_action, client.id, details)
    await db.commit()
    return {"status": "success", "client": client_to_api_dict(client)}

@router.get("/admin/api/clients/{client_id}")
async def admin_api_get_client(
    client_id: int,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    data = client_to_api_dict(client)
    data["access_token"] = mask_secret(client.access_token) if client.access_token else ""
    data["portal_key"] = client.portal_key
    data["public_key"] = getattr(client, "public_key", None)
    return {"status": "success", "client": data}


@router.get("/admin/api/clients/{client_id}/support-notes")
async def admin_api_client_support_notes(
    client_id: int,
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    rows = await db.execute(
        select(ClientSupportNote)
        .where(ClientSupportNote.client_id == client_id)
        .order_by(ClientSupportNote.created_at.desc(), ClientSupportNote.id.desc())
        .limit(50)
    )
    return {
        "status": "success",
        "notes": [_support_note_to_api(note) for note in rows.scalars().all()],
    }


@router.post("/admin/api/clients/{client_id}/support-notes")
@limiter.limit("30/minute")
async def admin_api_create_client_support_note(
    client_id: int,
    payload: AdminSupportNoteCreate,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    note_text = payload.note.strip()
    if not note_text or len(note_text) > 2000:
        raise HTTPException(status_code=400, detail="Support note must be 1-2000 characters.")
    note = ClientSupportNote(client_id=client_id, note=note_text, created_by=actor)
    db.add(note)
    await db.flush()
    await log_admin_action(db, request, actor, "client.support_note_added", client_id, "Support note added")
    await db.commit()
    await db.refresh(note)
    return {"status": "success", "note": _support_note_to_api(note)}

@router.post("/admin/api/clients/{client_id}/keys/rotate")
@limiter.limit("10/minute")
async def admin_api_rotate_key(
    client_id: int,
    request: Request,
    payload: dict,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    key_type = payload.get("key_type")
    if key_type not in ["api_key", "portal_key", "public_key"]:
        raise HTTPException(status_code=400, detail="Invalid key type")

    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    old_key = client.api_key
    if key_type == "api_key":
        client.old_api_key = old_key
        client.api_key_rotated_at = datetime.now(timezone.utc)
        client.api_key = secrets.token_urlsafe(32)
        clear_client_cache(old_key)
        clear_client_cache(client.api_key)
    elif key_type == "portal_key":
        client.portal_key = secrets.token_urlsafe(16)
    elif key_type == "public_key" and hasattr(client, "public_key"):
        client.public_key = secrets.token_hex(16)

    await log_admin_action(db, request, actor, f"client.{key_type}_rotated", client.id, f"{key_type} rotated via admin API")
    await db.commit()
    await db.refresh(client)
    return {"status": "success", "key_type": key_type, "new_value": getattr(client, key_type)}

@router.delete("/admin/api/clients/{client_id}")
@limiter.limit("5/minute")
async def admin_api_delete_client(
    client_id: int,
    request: Request,
    actor: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    client_name = client.name
    client_api_key = client.api_key

    await db.execute(sql_delete(EventOutbox).where(EventOutbox.client_id == client_id))
    await db.execute(sql_delete(FailedEvent).where(FailedEvent.client_id == client_id))
    await db.execute(sql_delete(PendingEvent).where(PendingEvent.client_id == client_id))
    await db.execute(sql_delete(EventDedup).where(EventDedup.client_id == client_id))
    await db.execute(sql_delete(UsageCounter).where(UsageCounter.client_id == client_id))
    await db.execute(sql_delete(EventLog).where(EventLog.client_id == client_id))
    await db.execute(sql_delete(ClientSupportNote).where(ClientSupportNote.client_id == client_id))
    await db.execute(sql_delete(SiteBinding).where(SiteBinding.client_id == client_id))

    # Delete client sessions and users to avoid foreign key constraint violations
    await db.execute(sql_delete(ClientSession).where(ClientSession.client_id == client_id))
    await db.execute(sql_delete(ClientUser).where(ClientUser.client_id == client_id))

    await db.delete(client)
    clear_client_cache(client_api_key)

    await log_admin_action(db, request, actor, "client.deleted", client_id, f"Client {client_name} deleted via API")
    await db.commit()
    return {"status": "success", "message": f"Client {client_name} deleted"}

class AdminLoginRequest(BaseModel):
    username: str
    password: str

@router.post("/admin/api/login")
@limiter.limit("5/minute")
async def admin_api_login(request: Request, payload: AdminLoginRequest, response: Response):
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD")
    admin_key = os.getenv("ADMIN_API_KEY")

    if not admin_pass or not admin_key:
        raise HTTPException(
            status_code=500,
            detail="Admin authentication is not configured on the server."
        )

    username_ok = hmac.compare_digest(payload.username, admin_user)

    password_ok = verify_admin_password(payload.password, admin_pass)

    if not username_ok or not password_ok:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password"
        )

    token_payload = {
        "sub": "admin",
        "exp": int(time.time()) + ADMIN_SESSION_SECONDS
    }
    jwt_token = create_jwt(token_payload, admin_key)
    csrf_token = create_admin_csrf_token(admin_key)
    set_admin_session_cookie(response, jwt_token)
    set_admin_csrf_cookie(response, csrf_token)

    return {
        "status": "success",
        "admin_api_key": jwt_token,
        "token": jwt_token,
        "csrf_token": csrf_token,
    }


@router.post("/admin/api/logout")
async def admin_api_logout(response: Response):
    clear_admin_session_cookie(response)
    clear_admin_csrf_cookie(response)
    return {"status": "success"}

def escape_sql_wildcards(search_term: str) -> str:
    if not search_term:
        return ""
    return search_term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

@router.get("/admin/api/events")
async def admin_api_events(
    _: str = Depends(verify_admin_api_key),
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
    client_id: int | None = None,
    status: str | None = None,
    platform: str | None = None,
    search: str | None = None,
):
    from datetime import datetime, timezone
    from sqlalchemy import desc

    query = select(EventLog).order_by(desc(EventLog.created_at))
    count_query = select(func.count(EventLog.id))

    if client_id:
        query = query.where(EventLog.client_id == client_id)
        count_query = count_query.where(EventLog.client_id == client_id)

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

    if search:
        escaped_search = escape_sql_wildcards(search)
        query = query.where(
            EventLog.event_name.ilike(f"%{escaped_search}%", escape="\\") |
            EventLog.ip_address.ilike(f"%{escaped_search}%", escape="\\") |
            EventLog.error_message.ilike(f"%{escaped_search}%", escape="\\")
        )
        count_query = count_query.where(
            EventLog.event_name.ilike(f"%{escaped_search}%", escape="\\") |
            EventLog.ip_address.ilike(f"%{escaped_search}%", escape="\\") |
            EventLog.error_message.ilike(f"%{escaped_search}%", escape="\\")
        )

    result = await db.execute(query.offset(offset).limit(limit))
    logs = result.scalars().all()

    count_r = await db.execute(count_query)
    total_count = count_r.scalar() or 0

    clients_r = await db.execute(select(Client))
    clients = clients_r.scalars().all()
    client_map = {c.id: c.name for c in clients}

    events_list = []
    for log in logs:
        raw_event_name = log.event_name
        log_platform = "Meta CAPI"
        display_event_name = raw_event_name

        if ":" in raw_event_name:
            parts = raw_event_name.split(":", 1)
            channel = parts[0]
            display_event_name = parts[1]
            if channel.lower() == "tiktok":
                log_platform = "TikTok Events API"
            elif channel.lower() == "ga4":
                log_platform = "GA4"
            elif channel.lower() in ("facebook", "capi", "meta"):
                log_platform = "Meta CAPI"
            elif channel.lower() == "webhook":
                log_platform = "Webhook"

        events_list.append({
            "id": f"evt_{log.id}",
            "client_id": log.client_id,
            "client_name": client_map.get(log.client_id, f"Client #{log.client_id}"),
            "timestamp": log.created_at.isoformat() if log.created_at else datetime.now(timezone.utc).isoformat(),
            "name": display_event_name,
            "platform": log_platform,
            "status": "Success" if log.status == "success" else "Failed",
            "httpCode": 200 if log.status == "success" else 400,
            "deduplicationKey": log.event_id or f"did_{log.id}",
            "payload": {
                "event_name": display_event_name,
                "event_time": int(log.created_at.timestamp()) if log.created_at else int(datetime.now().timestamp()),
                "user_data": {
                    "client_ip_address": log.ip_address or "127.0.0.1",
                    "client_user_agent": "Mozilla/5.0"
                },
                "custom_data": {"value": log.value, "currency": log.currency or "BDT"} if log.value else {}
            },
            "headers": {
                "Content-Type": "application/json",
                "X-Client-IP": log.ip_address or "127.0.0.1"
            },
            "responseBody": {
                "events_received": 1,
                "status": "accepted",
                "fb_trace_id": f"FBT_trace_{log.id}"
            } if log.status == "success" else {
                "error": {"message": log.error_message or "API execution failed", "code": 400}
            },
            "latencyMs": None
        })

    return {
        "status": "success",
        "events": events_list,
        "totalCount": total_count
    }
