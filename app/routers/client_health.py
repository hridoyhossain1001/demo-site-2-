"""
Client Health & Usage Analytics Router
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Feature 5: Client Health Dashboard â€” à¦ªà§à¦°à¦¤à¦¿à¦Ÿà¦¿ à¦•à§à¦²à¦¾à¦¯à¦¼à§‡à¦¨à§à¦Ÿà§‡à¦° "à¦¸à§à¦¬à¦¾à¦¸à§à¦¥à§à¦¯" à¦¦à§‡à¦–à¦¾
Feature 7: Usage Analytics Page â€” à¦®à¦¾à¦¸à¦¿à¦• à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦Ÿà§à¦°à§à¦¯à¦¾à¦•à¦¿à¦‚

Endpoints:
  GET /api/v1/client/health        â€” à¦•à§à¦²à¦¾à¦¯à¦¼à§‡à¦¨à§à¦Ÿà§‡à¦° tracking health status
  GET /api/v1/client/usage         â€” à¦®à¦¾à¦¸à¦¿à¦• usage analytics + limit info
  GET /api/v1/admin/clients/health â€” (Admin) à¦¸à¦¬ à¦•à§à¦²à¦¾à¦¯à¦¼à§‡à¦¨à§à¦Ÿà§‡à¦° health overview
"""

import logging
import hmac
import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import select, and_, func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_client, CachedClient
from app.models.event_log import EventLog
from app.models.client import Client
from app.routers.admin_api import verify_admin_api_key
from app.security import decrypt_token, meta_credentials_configured
from app.services.plan_service import (
    has_growth_access,
    plan_summary,
    record_trial_identity,
    require_growth_access,
    require_trial_available,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# â”€â”€â”€ Response Schemas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ClientHealthResponse(BaseModel):
    status: str
    client_name: str
    is_healthy: bool
    last_event_at: Optional[str] = None
    hours_since_last_event: Optional[float] = None
    today_events: int
    today_success: int
    today_failed: int
    success_rate: float
    health_issues: List[str]


class UsageResponse(BaseModel):
    status: str
    client_name: str
    period: str
    events_used: int
    events_limit: int
    usage_percentage: float
    daily_breakdown: List[dict]
    top_events: List[dict]


class AdminHealthItem(BaseModel):
    client_id: int
    client_name: str
    is_active: bool
    is_healthy: bool
    last_event_at: Optional[str] = None
    hours_silent: Optional[float] = None
    today_events: int
    success_rate: float
    health_status: str  # "healthy", "warning", "critical", "inactive"


class AdminHealthResponse(BaseModel):
    status: str
    total_clients: int
    healthy: int
    warning: int
    critical: int
    inactive: int
    clients: List[AdminHealthItem]


@router.get("/health", summary="Client API health check")
async def client_api_health(
    client: CachedClient = Depends(get_current_client),
):
    """Lightweight authenticated health check for plugins and client dashboards."""
    return {
        "status": "ok",
        "service": "Buykori AdSync",
        "client_name": client.name,
        "client_id": client.id,
    }


# â”€â”€â”€ GET /client/health â€” Client Self-Health Check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/client/health",
    response_model=ClientHealthResponse,
    summary="Client tracking health status",
)
async def client_health(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """à¦•à§à¦²à¦¾à¦¯à¦¼à§‡à¦¨à§à¦Ÿà§‡à¦° à¦Ÿà§à¦°à§à¦¯à¦¾à¦•à¦¿à¦‚ à¦¸à¦¿à¦¸à§à¦Ÿà§‡à¦®à§‡à¦° à¦¸à§à¦¬à¦¾à¦¸à§à¦¥à§à¦¯ à¦ªà¦°à§€à¦•à§à¦·à¦¾ â€” à¦¸à¦®à¦¸à§à¦¯à¦¾ à¦¥à¦¾à¦•à¦²à§‡ issues à¦¤à¦¾à¦²à¦¿à¦•à¦¾à¦¯à¦¼ à¦¦à§‡à¦–à¦¾à¦¯à¦¼"""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Last event time
    last_r = await db.execute(
        select(sql_func.max(EventLog.created_at))
        .where(EventLog.client_id == client.id)
    )
    last_event = last_r.scalar()

    hours_since = None
    if last_event:
        last_event_utc = last_event.replace(tzinfo=timezone.utc) if last_event.tzinfo is None else last_event
        hours_since = round((now - last_event_utc).total_seconds() / 3600, 1)

    # Today's stats
    today_r = await db.execute(
        select(EventLog.status, sql_func.sum(EventLog.event_count))
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.created_at >= today_start,
        ))
        .group_by(EventLog.status)
    )
    today_success = 0
    today_failed = 0
    for row in today_r:
        if row[0] == "success":
            today_success = int(row[1] or 0)
        elif row[0] == "failed":
            today_failed = int(row[1] or 0)
    today_total = today_success + today_failed
    rate = round((today_success / today_total * 100) if today_total > 0 else 0, 1)

    # Health issues detection
    issues = []
    is_healthy = True

    if last_event is None:
        issues.append("âš ï¸ à¦•à§‹à¦¨à§‹ à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦ªà¦¾à¦“à¦¯à¦¼à¦¾ à¦¯à¦¾à¦¯à¦¼à¦¨à¦¿ â€” à¦Ÿà§à¦°à§à¦¯à¦¾à¦•à¦¿à¦‚ à¦¸à§‡à¦Ÿà¦†à¦ª à¦šà§‡à¦• à¦•à¦°à§à¦¨")
        is_healthy = False
    elif hours_since and hours_since > 24:
        issues.append(f"ðŸ”´ {hours_since} à¦˜à¦¨à§à¦Ÿà¦¾ à¦§à¦°à§‡ à¦•à§‹à¦¨à§‹ à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦†à¦¸à§‡à¦¨à¦¿")
        is_healthy = False
    elif hours_since and hours_since > 6:
        issues.append(f"ðŸŸ¡ {hours_since} à¦˜à¦¨à§à¦Ÿà¦¾ à¦§à¦°à§‡ à¦•à§‹à¦¨à§‹ à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦†à¦¸à§‡à¦¨à¦¿")

    if rate < 90 and today_total > 0:
        issues.append(f"ðŸ”´ Success rate à¦•à¦®: {rate}% â€” Facebook API à¦¸à¦®à¦¸à§à¦¯à¦¾ à¦¹à¦¤à§‡ à¦ªà¦¾à¦°à§‡")
        is_healthy = False

    if today_failed > 10:
        issues.append(f"âš ï¸ à¦†à¦œà¦•à§‡ {today_failed}à¦Ÿà¦¿ à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦«à§‡à¦‡à¦² à¦¹à¦¯à¦¼à§‡à¦›à§‡")

    if not issues:
        issues.append("âœ… à¦¸à¦¬ à¦•à¦¿à¦›à§ à¦ à¦¿à¦• à¦†à¦›à§‡!")

    return ClientHealthResponse(
        status="success",
        client_name=client.name,
        is_healthy=is_healthy,
        last_event_at=last_event.isoformat() if last_event else None,
        hours_since_last_event=hours_since,
        today_events=today_total,
        today_success=today_success,
        today_failed=today_failed,
        success_rate=rate,
        health_issues=issues,
    )


# â”€â”€â”€ GET /client/usage â€” Monthly Usage Analytics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/client/usage",
    response_model=UsageResponse,
    summary="Monthly usage analytics",
)
async def client_usage(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=90),
):
    """à¦à¦‡ à¦®à¦¾à¦¸à§‡ à¦•à¦¤à¦Ÿà¦¿ à¦‡à¦­à§‡à¦¨à§à¦Ÿ à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦¹à¦¯à¦¼à§‡à¦›à§‡, à¦²à¦¿à¦®à¦¿à¦Ÿ à¦•à¦¤, à¦à¦¬à¦‚ à¦¦à§ˆà¦¨à¦¿à¦• à¦¬à§à¦°à§‡à¦•à¦¡à¦¾à¦‰à¦¨"""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    # Total usage
    total_r = await db.execute(
        select(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0))
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
    )
    events_used = int(total_r.scalar() or 0)

    # Get client's monthly limit from DB
    client_r = await db.execute(
        select(Client).where(Client.id == client.id)
    )
    client_row = client_r.scalar_one_or_none()
    events_limit = getattr(client_row, 'monthly_limit', None)
    if events_limit is None:
        events_limit = 50000  # Default 50K

    usage_pct = round((events_used / events_limit * 100) if events_limit > 0 else 0, 1)

    # Daily breakdown
    daily_r = await db.execute(
        select(
            sql_func.date(EventLog.created_at).label("day"),
            sql_func.sum(EventLog.event_count),
        )
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
        .group_by("day")
        .order_by("day")
    )
    daily = [{"date": str(r[0]), "count": int(r[1] or 0)} for r in daily_r]

    # Top events this period
    top_r = await db.execute(
        select(EventLog.event_name, sql_func.sum(EventLog.event_count))
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
        .group_by(EventLog.event_name)
        .order_by(sql_func.sum(EventLog.event_count).desc())
        .limit(10)
    )
    top_events = [{"name": r[0] or "Unknown", "count": int(r[1] or 0)} for r in top_r]

    return UsageResponse(
        status="success",
        client_name=client.name,
        period=f"Last {days} days",
        events_used=events_used,
        events_limit=events_limit,
        usage_percentage=usage_pct,
        daily_breakdown=daily,
        top_events=top_events,
    )


# â”€â”€â”€ GET /admin/clients/health â€” Admin: All Clients Health â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/admin/clients/health",
    response_model=AdminHealthResponse,
    summary="All clients health overview (Admin only)",
)
async def admin_clients_health(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_api_key),
):
    """à¦…à§à¦¯à¦¾à¦¡à¦®à¦¿à¦¨ â€” à¦¸à¦¬ à¦•à§à¦²à¦¾à¦¯à¦¼à§‡à¦¨à§à¦Ÿà§‡à¦° health status à¦à¦• à¦ªà§‡à¦œà§‡ à¦¦à§‡à¦–à§à¦¨"""

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # All clients
    clients_r = await db.execute(select(Client))
    all_clients = clients_r.scalars().all()

    items = []
    healthy = warning = critical = inactive = 0

    for c in all_clients:
        if not c.is_active:
            items.append(AdminHealthItem(
                client_id=c.id, client_name=c.name, is_active=False,
                is_healthy=False, today_events=0, success_rate=0,
                health_status="inactive",
            ))
            inactive += 1
            continue

        # Last event
        last_r = await db.execute(
            select(sql_func.max(EventLog.created_at))
            .where(EventLog.client_id == c.id)
        )
        last_event = last_r.scalar()

        hours_silent = None
        if last_event:
            le_utc = last_event.replace(tzinfo=timezone.utc) if last_event.tzinfo is None else last_event
            hours_silent = round((now - le_utc).total_seconds() / 3600, 1)

        # Today's stats
        stats_r = await db.execute(
            select(EventLog.status, sql_func.sum(EventLog.event_count))
            .where(and_(
                EventLog.client_id == c.id,
                EventLog.created_at >= today_start,
            ))
            .group_by(EventLog.status)
        )
        success = failed = 0
        for row in stats_r:
            if row[0] == "success":
                success = int(row[1] or 0)
            elif row[0] == "failed":
                failed = int(row[1] or 0)
        total = success + failed
        rate = round((success / total * 100) if total > 0 else 0, 1)

        # Determine health status
        if last_event is None or (hours_silent and hours_silent > 48):
            health_status = "critical"
            critical += 1
        elif hours_silent and hours_silent > 12:
            health_status = "warning"
            warning += 1
        elif rate < 80 and total > 0:
            health_status = "warning"
            warning += 1
        else:
            health_status = "healthy"
            healthy += 1

        items.append(AdminHealthItem(
            client_id=c.id,
            client_name=c.name,
            is_active=True,
            is_healthy=(health_status == "healthy"),
            last_event_at=last_event.isoformat() if last_event else None,
            hours_silent=hours_silent,
            today_events=total,
            success_rate=rate,
            health_status=health_status,
        ))

    # Sort: critical first, then warning, then healthy
    order = {"critical": 0, "warning": 1, "healthy": 2, "inactive": 3}
    items.sort(key=lambda x: order.get(x.health_status, 4))

    return AdminHealthResponse(
        status="success",
        total_clients=len(all_clients),
        healthy=healthy,
        warning=warning,
        critical=critical,
        inactive=inactive,
        clients=items,
    )


# â”€â”€â”€ GET /client/setup â€” Client: View Setup + API Key â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/client/setup",
    summary="Get client setup details and API key",
)
async def client_get_setup(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client-à¦à¦° à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨ setup à¦à¦¬à¦‚ API key à¦¦à§‡à¦–à§à¦¨à¥¤"""
    client_r = await db.execute(select(Client).where(Client.id == client.id))
    c = client_r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Client not found")

    # Safely decrypt access token for display (masked)
    raw_token = ""
    try:
        raw_token = decrypt_token(c.access_token) if meta_credentials_configured(c) else ""
    except Exception:
        raw_token = ""

    return {
        "status": "success",
        "api_key": c.api_key,
        "monthly_limit": plan_summary(c)["eventsQuota"],
        "domain": c.domain or "",
        "pixel_id": c.pixel_id if c.pixel_id and c.pixel_id != "0" else "",
        "access_token_set": bool(raw_token),
        "test_event_code_set": bool((getattr(c, "test_event_code", "") or "").strip()),
        "tiktok_pixel_id": getattr(c, "tiktok_pixel_id", "") or "",
        "tiktok_test_event_code_set": bool((getattr(c, "tiktok_test_event_code", "") or "").strip()),
        "ga4_measurement_id": getattr(c, "ga4_measurement_id", "") or "",
        "enable_facebook": getattr(c, "enable_facebook", False),
        "enable_tiktok": has_growth_access(c) and getattr(c, "enable_tiktok", False),
        "enable_ga4": has_growth_access(c) and getattr(c, "enable_ga4", False),
        "deferred_purchase": has_growth_access(c) and getattr(c, "deferred_purchase", False),
        "auto_confirm_days": min(max(0, getattr(c, "auto_confirm_days", 0)), 7),
        "auto_confirm_status": getattr(c, "auto_confirm_status", "completed"),
    }


# â”€â”€â”€ PATCH /client/setup â€” Client: Update Tracking Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class ClientSetupRequest(BaseModel):
    domain: Optional[str] = None
    pixel_id: Optional[str] = None
    access_token: Optional[str] = None
    test_event_code: Optional[str] = None
    tiktok_pixel_id: Optional[str] = None
    tiktok_access_token: Optional[str] = None
    tiktok_test_event_code: Optional[str] = None
    ga4_measurement_id: Optional[str] = None
    ga4_api_secret: Optional[str] = None
    deferred_purchase: Optional[bool] = None
    auto_confirm_days: Optional[int] = None
    auto_confirm_status: Optional[str] = None


@router.patch(
    "/client/setup",
    summary="Update client tracking setup",
)
async def client_update_setup(
    payload: ClientSetupRequest,
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Client à¦¨à¦¿à¦œà§‡à¦° Facebook Pixel, TikTok, GA4 settings à¦†à¦ªà¦¡à§‡à¦Ÿ à¦•à¦°à¦¤à§‡ à¦ªà¦¾à¦°à¦¬à§‡à¥¤"""
    from app.security import encrypt_token
    import re

    client_r = await db.execute(select(Client).where(Client.id == client.id))
    c = client_r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Client not found")
    growth_enabled = has_growth_access(c)

    if not growth_enabled and any((
        payload.tiktok_pixel_id,
        payload.tiktok_access_token,
        payload.ga4_measurement_id,
        payload.ga4_api_secret,
        payload.deferred_purchase,
        payload.auto_confirm_days,
    )):
        require_growth_access(c, "TikTok, GA4, and Deferred Purchase setup")

    # Domain validation (supports comma-separated multiple domains)
    if payload.domain is not None:
        parts = []
        for raw_part in payload.domain.split(","):
            d = raw_part.strip().lower()
            if not d:
                continue
            d = re.sub(r"^https?://", "", d).split("/", 1)[0].rstrip(".")
            if d.startswith("www."):
                d = d[4:]
            if d and ("." not in d or len(d) > 255):
                raise HTTPException(status_code=400, detail=f"Invalid domain format: {raw_part}")
            parts.append(d)
        c.domain = ",".join(parts) if parts else None

    # Facebook
    if payload.pixel_id is not None:
        clean_pixel_id = payload.pixel_id.strip()
        if clean_pixel_id and not clean_pixel_id.isdigit():
            raise HTTPException(status_code=400, detail="Meta Pixel ID must be numeric.")
        c.pixel_id = clean_pixel_id or "0"
        c.enable_facebook = bool(clean_pixel_id)
    if payload.access_token is not None and payload.access_token.strip():
        c.access_token = encrypt_token(payload.access_token.strip())
        c.enable_facebook = True
    if payload.test_event_code is not None:
        c.test_event_code = payload.test_event_code.strip() or None

    # TikTok
    if payload.tiktok_pixel_id is not None:
        clean_tiktok_pixel_id = payload.tiktok_pixel_id.strip()
        if clean_tiktok_pixel_id and not clean_tiktok_pixel_id.isdigit():
            raise HTTPException(status_code=400, detail="TikTok Pixel ID must be numeric.")
        c.tiktok_pixel_id = clean_tiktok_pixel_id or None
        c.enable_tiktok = growth_enabled and bool(clean_tiktok_pixel_id)
    if payload.tiktok_access_token is not None and payload.tiktok_access_token.strip():
        c.tiktok_access_token = encrypt_token(payload.tiktok_access_token.strip())
        c.enable_tiktok = growth_enabled
    if payload.tiktok_test_event_code is not None:
        c.tiktok_test_event_code = payload.tiktok_test_event_code.strip() or None

    # GA4
    if payload.ga4_measurement_id is not None:
        clean_measurement_id = payload.ga4_measurement_id.strip()
        if clean_measurement_id and not clean_measurement_id.startswith("G-"):
            raise HTTPException(status_code=400, detail="GA4 Measurement ID must start with G-.")
        c.ga4_measurement_id = clean_measurement_id or None
        c.enable_ga4 = growth_enabled and bool(clean_measurement_id)
    if payload.ga4_api_secret is not None and payload.ga4_api_secret.strip():
        c.ga4_api_secret = encrypt_token(payload.ga4_api_secret.strip())
        c.enable_ga4 = growth_enabled

    # Deferred Purchase
    if payload.deferred_purchase is not None:
        c.deferred_purchase = growth_enabled and payload.deferred_purchase

    # COD Automation
    if payload.auto_confirm_days is not None:
        c.auto_confirm_days = min(max(0, payload.auto_confirm_days), 7) if growth_enabled else 0
    if payload.auto_confirm_status is not None:
        requested_status = payload.auto_confirm_status.strip().lower().removeprefix("wc-")
        c.auto_confirm_status = requested_status if requested_status in {"completed", "processing"} else "completed"

    if getattr(c, "trial_started_at", None):
        await require_trial_available(db, domain=c.domain, pixel_id=c.pixel_id, exclude_client_id=c.id)
        await record_trial_identity(db, c, source="setup")

    await db.commit()

    # Clear dependency cache so new settings take effect
    from app.dependencies import invalidate_client_cache
    await invalidate_client_cache(c.api_key)

    return {
        "status": "success",
        "message": "Setup saved. Your tracking configuration has been updated.",
        "api_key": c.api_key,
        "enable_facebook": bool(c.enable_facebook),
        "enable_tiktok": bool(c.enable_tiktok),
        "enable_ga4": bool(c.enable_ga4),
        "deferred_purchase": bool(getattr(c, "deferred_purchase", False)),
        "auto_confirm_days": min(max(0, int(getattr(c, "auto_confirm_days", 0))), 7),
        "auto_confirm_status": str(getattr(c, "auto_confirm_status", "completed")),
        "test_event_code_set": bool((getattr(c, "test_event_code", "") or "").strip()),
        "tiktok_test_event_code_set": bool((getattr(c, "tiktok_test_event_code", "") or "").strip()),
    }


@router.get(
    "/client/system-health",
    summary="Client system health metrics",
)
async def client_system_health(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns client-specific system health metrics:
    - outbox_depth (queued, processing, dead, sent counts)
    - processed_rate_per_min (sent count in last 5 minutes / 5)
    - avg_latency_sec (average seconds between outbox creation and sent_at in last 5 minutes)
    - redis_fallback_total (system-wide Redis status counters)
    """
    try:
        from app.models.event_outbox import EventOutbox
        # Outbox depth counts
        rows = await db.execute(
            select(EventOutbox.status, sql_func.count(EventOutbox.id))
            .where(EventOutbox.client_id == client.id)
            .group_by(EventOutbox.status)
        )
        outbox_depth = {str(status): int(count or 0) for status, count in rows}

        # Ensure all standard statuses exist in dict
        for status in ("queued", "processing", "dead", "sent"):
            if status not in outbox_depth:
                outbox_depth[status] = 0

        # Processed rate & latency over last 5 minutes
        five_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
        sent_items_r = await db.execute(
            select(EventOutbox.created_at, EventOutbox.sent_at)
            .where(and_(
                EventOutbox.client_id == client.id,
                EventOutbox.status == "sent",
                EventOutbox.sent_at >= five_mins_ago
            ))
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

        # Global Redis fallback counts
        from app.services.redis_pool import redis_fallback_counts
        redis_fallback = redis_fallback_counts()

        return {
            "status": "success",
            "outbox_depth": outbox_depth,
            "processed_rate_per_min": processed_rate_per_min,
            "avg_latency_sec": avg_latency_sec,
            "redis_fallback_total": redis_fallback,
        }
    except Exception as e:
        logger.exception("Client system-health endpoint failed")
        raise HTTPException(status_code=500, detail="Internal server error")
