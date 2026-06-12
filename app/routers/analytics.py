"""
Advanced Analytics Router
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
EMQ Score, Conversion Funnel, Event Breakdown, Hourly Heatmap, Top Products
Client Portal-à¦à¦° à¦œà¦¨à§à¦¯ analytics data APIà¥¤

Endpoints:
  GET /api/v1/analytics/overview     â€” Overall stats (EMQ, funnel, breakdown)
  GET /api/v1/analytics/hourly       â€” Hourly heatmap data
  GET /api/v1/analytics/top-products â€” Top products by events
  GET /api/v1/analytics/export       â€” CSV export
"""

import csv
import io
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, and_, func as sql_func, extract, case, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_client, CachedClient
from app.models.event_log import EventLog
from app.models.ad_account import AdAccount
from app.models.ad_campaign import AdCampaign
from app.models.ad_insight_daily import AdInsightDaily
from app.models.pending_event import PendingEvent

logger = logging.getLogger(__name__)
router = APIRouter()


# â”€â”€â”€ Response Schemas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class EventBreakdown(BaseModel):
    event_name: str
    count: int
    percentage: float


class FunnelStep(BaseModel):
    step: str
    count: int
    drop_off: float  # % drop from previous step


class HourlyData(BaseModel):
    hour: int
    count: int


class TopProduct(BaseModel):
    product_id: str
    event_count: int
    total_value: float


class CampaignRow(BaseModel):
    source: str
    campaign: str
    view_content: int
    add_to_cart: int
    initiate_checkout: int
    purchase: int
    revenue: float


class AudienceBreakdownRow(BaseModel):
    label: str
    count: int
    percentage: float


class DistrictFunnelRow(BaseModel):
    district: str
    page_view: int
    add_to_cart: int
    initiate_checkout: int
    purchase: int
    revenue: float


class OverviewResponse(BaseModel):
    status: str
    period_days: int
    total_events: int
    success_count: int
    failed_count: int
    success_rate: float
    avg_daily_events: float
    emq_score: Optional[float] = None
    event_breakdown: list[EventBreakdown]
    funnel: list[FunnelStep]


class HourlyResponse(BaseModel):
    status: str
    data: list[HourlyData]


class TopProductsResponse(BaseModel):
    status: str
    products: list[TopProduct]


class CampaignsResponse(BaseModel):
    status: str
    campaigns: list[CampaignRow]


class AudienceResponse(BaseModel):
    status: str
    period_days: int
    total_events: int
    top_districts: list[AudienceBreakdownRow]
    device_mix: list[AudienceBreakdownRow]
    browser_mix: list[AudienceBreakdownRow]
    district_funnel: list[DistrictFunnelRow]
    visitor_district_funnel: list[DistrictFunnelRow] = []
    notice: str


class SignalIssue(BaseModel):
    severity: str
    title: str
    metric: str
    impact: str
    fix: str


class SignalDoctorResponse(BaseModel):
    status: str
    period_days: int
    score: int
    grade: str
    total_events: int
    platform_readiness: dict
    signal_rates: dict
    event_counts: dict
    issues: list[SignalIssue]


class AdPerformanceRow(BaseModel):
    campaign_id: str
    campaign_name: str
    platform: str
    spend: float
    spend_currency: str = ""
    clicks: int
    impressions: int
    ctr: float
    cpc: float
    placed_purchases: int
    placed_revenue: float
    placed_roas: float
    placed_cpa: float
    confirmed_purchases: int
    confirmed_revenue: float
    revenue_currency: str = ""
    confirmed_roas: float
    confirmed_cpa: float
    browser_page_views: int
    server_page_views: int
    tracking_bypass_rate: float


class AdPerformanceResponse(BaseModel):
    status: str
    period_days: int
    sync_enabled: bool = False
    data: list[AdPerformanceRow]


# â”€â”€â”€ EMQ Score Calculator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _calculate_emq_estimate(events_data: list[dict]) -> float:
    """
    Facebook Event Match Quality (EMQ) à¦¸à§à¦•à§‹à¦° estimate à¦•à¦°à§‡à¥¤
    EMQ depends on: email, phone, IP, UA, fbp, fbc, external_id, country, city
    Score: 0-10 (higher = better match quality)
    """
    if not events_data:
        return 0.0

    total_score = 0
    for event in events_data:
        score = 0
        ud = event.get("user_data", {}) if isinstance(event, dict) else {}

        if ud.get("em"):
            score += 2.5  # Email = highest weight
        if ud.get("ph"):
            score += 2.0  # Phone
        if ud.get("client_ip_address"):
            score += 1.5  # IP
        if ud.get("client_user_agent"):
            score += 1.0  # User Agent
        if ud.get("fbp"):
            score += 1.5  # FB Pixel cookie
        if ud.get("fbc"):
            score += 1.0  # FB Click ID
        if ud.get("external_id"):
            score += 0.5  # External ID

        total_score += min(score, 10.0)

    return round(total_score / len(events_data), 1)


def _pct(part: int | float, total: int | float) -> float:
    return round((part / total * 100) if total else 0.0, 1)


def _grade(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 55:
        return "Needs Work"
    return "Critical"


# â”€â”€â”€ GET /analytics/overview â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/analytics/overview",
    response_model=OverviewResponse,
    summary="Analytics overview â€” EMQ, funnel, breakdown",
)
async def analytics_overview(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="à¦•à¦¤ à¦¦à¦¿à¦¨à§‡à¦° à¦¡à§‡à¦Ÿà¦¾"),
):
    """EMQ Score, Event Breakdown, Conversion Funnel à¦¸à¦¹ analytics overview"""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    # Total success/failed
    stats_r = await db.execute(
        select(EventLog.status, sql_func.sum(EventLog.event_count))
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.created_at >= start,
        ))
        .group_by(EventLog.status)
    )
    success = 0
    failed = 0
    for row in stats_r:
        if row[0] == "success":
            success = row[1] or 0
        elif row[0] == "failed":
            failed = row[1] or 0
    total = success + failed
    rate = round((success / total * 100) if total > 0 else 0, 1)

    # Event Breakdown
    breakdown_r = await db.execute(
        select(EventLog.event_name, sql_func.sum(EventLog.event_count))
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
        .group_by(EventLog.event_name)
        .order_by(sql_func.sum(EventLog.event_count).desc())
    )
    breakdown = []
    for row in breakdown_r:
        name = row[0] or "Unknown"
        count = row[1] or 0
        pct = round((count / success * 100) if success > 0 else 0, 1)
        breakdown.append(EventBreakdown(event_name=name, count=count, percentage=pct))

    # Conversion Funnel â€” single query instead of N+1 per event type
    funnel_events = ["PageView", "ViewContent", "AddToCart", "InitiateCheckout", "Purchase"]
    funnel_r = await db.execute(
        select(EventLog.event_name, sql_func.coalesce(sql_func.sum(EventLog.event_count), 0))
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.event_name.in_(funnel_events),
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
        .group_by(EventLog.event_name)
    )
    funnel_counts = {fe: 0 for fe in funnel_events}
    for row in funnel_r:
        funnel_counts[row[0]] = row[1] or 0

    funnel = []
    prev_count = None
    for fe in funnel_events:
        count = funnel_counts[fe]
        if prev_count is not None and prev_count > 0:
            drop = round((1 - count / prev_count) * 100, 1)
        else:
            drop = 0.0
        funnel.append(FunnelStep(step=fe, count=count, drop_off=drop))
        prev_count = count if count > 0 else prev_count

    # EMQ Score â€” average stored estimate from recent events
    sample_r = await db.execute(
        select(sql_func.avg(EventLog.emq_score))
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.emq_score.is_not(None),
            EventLog.created_at >= now - timedelta(hours=24),
        ))
    )
    emq_avg = sample_r.scalar()
    emq_score = round(float(emq_avg), 1) if emq_avg is not None else None

    return OverviewResponse(
        status="success",
        period_days=days,
        total_events=total,
        success_count=success,
        failed_count=failed,
        success_rate=rate,
        avg_daily_events=round(total / max(days, 1), 0),
        emq_score=emq_score,
        event_breakdown=breakdown,
        funnel=funnel,
    )


# â”€â”€â”€ GET /analytics/hourly â€” Hourly Heatmap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/analytics/hourly",
    response_model=HourlyResponse,
    summary="Hourly event distribution",
)
async def analytics_hourly(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30),
):
    """à¦•à§‹à¦¨ à¦¸à¦®à¦¯à¦¼à§‡ à¦¸à¦¬à¦šà§‡à¦¯à¦¼à§‡ à¦¬à§‡à¦¶à¦¿ event fire à¦¹à¦¯à¦¼"""
    start = datetime.now(timezone.utc) - timedelta(days=days)

    hourly_r = await db.execute(
        select(
            extract("hour", EventLog.created_at).label("hour"),
            sql_func.sum(EventLog.event_count),
        )
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
        .group_by("hour")
        .order_by("hour")
    )

    hourly_map = {int(r[0]): int(r[1]) for r in hourly_r}
    data = [HourlyData(hour=h, count=hourly_map.get(h, 0)) for h in range(24)]

    return HourlyResponse(status="success", data=data)


# â”€â”€â”€ GET /analytics/top-products â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/analytics/top-products",
    response_model=TopProductsResponse,
    summary="Top products by event count",
)
async def analytics_top_products(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(10, ge=1, le=50),
):
    """à¦•à§‹à¦¨ product à¦¸à¦¬à¦šà§‡à¦¯à¦¼à§‡ à¦¬à§‡à¦¶à¦¿ AddToCart / Purchase à¦¹à¦šà§à¦›à§‡"""
    start = datetime.now(timezone.utc) - timedelta(days=days)

    # Extract product ID from event_id (WP snippet uses 'view-123', 'cart-123')
    # Using Postgres split_part to get the part after the hyphen
    product_id_expr = sql_func.split_part(EventLog.event_id, '-', 2)

    result = await db.execute(
        select(
            product_id_expr,
            sql_func.count(EventLog.id),
            sql_func.coalesce(sql_func.sum(EventLog.value), 0.0),
        )
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.event_name.in_(["AddToCart", "ViewContent"]),
            product_id_expr != "",
            EventLog.created_at >= start,
        ))
        .group_by(product_id_expr)
        .order_by(sql_func.count(EventLog.id).desc())
        .limit(limit)
    )

    products = []
    for row in result:
        pid = row[0]
        # Ignore timestamps or random strings if they're too long
        if not pid or len(pid) > 15:
            continue

        products.append(TopProduct(
            product_id=f"Product #{pid}",
            event_count=row[1] or 0,
            total_value=float(row[2] or 0),
        ))

    return TopProductsResponse(status="success", products=products)


@router.get(
    "/analytics/campaigns",
    response_model=CampaignsResponse,
    summary="UTM campaign performance",
)
async def analytics_campaigns(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=180),
    limit: int = Query(50, ge=1, le=200),
):
    """Campaign-wise funnel and revenue from stored UTM attribution."""
    start = datetime.now(timezone.utc) - timedelta(days=days)
    source_expr = sql_func.coalesce(EventLog.utm_source, EventLog.campaign_source, "direct")
    campaign_expr = sql_func.coalesce(EventLog.utm_campaign, "(not set)")
    revenue_expr = sql_func.coalesce(
        sql_func.sum(case((EventLog.event_name == "Purchase", EventLog.value), else_=0)),
        0.0,
    )

    result = await db.execute(
        select(
            source_expr.label("source"),
            campaign_expr.label("campaign"),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "ViewContent", EventLog.event_count), else_=0)), 0),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "AddToCart", EventLog.event_count), else_=0)), 0),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "InitiateCheckout", EventLog.event_count), else_=0)), 0),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "Purchase", EventLog.event_count), else_=0)), 0),
            revenue_expr,
        )
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
        .group_by(source_expr, campaign_expr)
        .order_by(revenue_expr.desc())
        .limit(limit)
    )

    campaigns = [
        CampaignRow(
            source=row[0] or "direct",
            campaign=row[1] or "(not set)",
            view_content=int(row[2] or 0),
            add_to_cart=int(row[3] or 0),
            initiate_checkout=int(row[4] or 0),
            purchase=int(row[5] or 0),
            revenue=float(row[6] or 0),
        )
        for row in result
    ]
    return CampaignsResponse(status="success", campaigns=campaigns)


@router.get(
    "/analytics/audience",
    response_model=AudienceResponse,
    summary="Estimated district and device breakdown",
)
async def analytics_audience(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=180),
    limit: int = Query(10, ge=1, le=50),
):
    """Approximate geo/device analytics from EventLog enrichment."""
    start = datetime.now(timezone.utc) - timedelta(days=days)
    base_filter = and_(
        EventLog.client_id == client.id,
        EventLog.status == "success",
        EventLog.created_at >= start,
        EventLog.event_count > 0,
    )

    total_r = await db.execute(
        select(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0)).where(base_filter)
    )
    total = int(total_r.scalar() or 0)

    district_expr = sql_func.coalesce(EventLog.geo_district, EventLog.geo_city, "Unknown")
    district_r = await db.execute(
        select(district_expr.label("district"), sql_func.coalesce(sql_func.sum(EventLog.event_count), 0))
        .where(base_filter)
        .group_by(district_expr)
        .order_by(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0).desc())
        .limit(limit)
    )
    top_districts = [
        AudienceBreakdownRow(label=row[0] or "Unknown", count=int(row[1] or 0), percentage=_pct(int(row[1] or 0), total))
        for row in district_r
    ]

    device_expr = sql_func.coalesce(EventLog.device_type, "unknown")
    device_r = await db.execute(
        select(device_expr.label("device"), sql_func.coalesce(sql_func.sum(EventLog.event_count), 0))
        .where(base_filter)
        .group_by(device_expr)
        .order_by(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0).desc())
    )
    device_mix = [
        AudienceBreakdownRow(label=(row[0] or "unknown").title(), count=int(row[1] or 0), percentage=_pct(int(row[1] or 0), total))
        for row in device_r
    ]

    browser_expr = sql_func.coalesce(EventLog.device_browser, "Unknown")
    browser_r = await db.execute(
        select(browser_expr.label("browser"), sql_func.coalesce(sql_func.sum(EventLog.event_count), 0))
        .where(base_filter)
        .group_by(browser_expr)
        .order_by(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0).desc())
        .limit(limit)
    )
    browser_mix = [
        AudienceBreakdownRow(label=row[0] or "Unknown", count=int(row[1] or 0), percentage=_pct(int(row[1] or 0), total))
        for row in browser_r
    ]

    district_total_expr = sql_func.coalesce(sql_func.sum(EventLog.event_count), 0)
    funnel_r = await db.execute(
        select(
            district_expr.label("district"),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "PageView", EventLog.event_count), else_=0)), 0),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "AddToCart", EventLog.event_count), else_=0)), 0),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "InitiateCheckout", EventLog.event_count), else_=0)), 0),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "Purchase", EventLog.event_count), else_=0)), 0),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "Purchase", EventLog.value), else_=0)), 0.0),
            district_total_expr,
        )
        .where(base_filter)
        .group_by(district_expr)
        .order_by(district_total_expr.desc())
        .limit(limit)
    )
    district_funnel = [
        DistrictFunnelRow(
            district=row[0] or "Unknown",
            page_view=int(row[1] or 0),
            add_to_cart=int(row[2] or 0),
            initiate_checkout=int(row[3] or 0),
            purchase=int(row[4] or 0),
            revenue=float(row[5] or 0),
        )
        for row in funnel_r
    ]

    visitor_base_filter = and_(base_filter, EventLog.visitor_key.is_not(None))
    visitor_funnel_r = await db.execute(
        select(
            district_expr.label("district"),
            sql_func.count(sql_func.distinct(case((EventLog.event_name == "PageView", EventLog.visitor_key), else_=None))),
            sql_func.count(sql_func.distinct(case((EventLog.event_name == "AddToCart", EventLog.visitor_key), else_=None))),
            sql_func.count(sql_func.distinct(case((EventLog.event_name == "InitiateCheckout", EventLog.visitor_key), else_=None))),
            sql_func.count(sql_func.distinct(case((EventLog.event_name == "Purchase", EventLog.visitor_key), else_=None))),
            sql_func.coalesce(sql_func.sum(case((EventLog.event_name == "Purchase", EventLog.value), else_=0)), 0.0),
            sql_func.count(sql_func.distinct(EventLog.visitor_key)),
        )
        .where(visitor_base_filter)
        .group_by(district_expr)
        .order_by(sql_func.count(sql_func.distinct(EventLog.visitor_key)).desc())
        .limit(limit)
    )
    visitor_district_funnel = [
        DistrictFunnelRow(
            district=row[0] or "Unknown",
            page_view=int(row[1] or 0),
            add_to_cart=int(row[2] or 0),
            initiate_checkout=int(row[3] or 0),
            purchase=int(row[4] or 0),
            revenue=float(row[5] or 0),
        )
        for row in visitor_funnel_r
    ]

    return AudienceResponse(
        status="success",
        period_days=days,
        total_events=total,
        top_districts=top_districts,
        device_mix=device_mix,
        browser_mix=browser_mix,
        district_funnel=district_funnel,
        visitor_district_funnel=visitor_district_funnel,
        notice="City and district data is estimated from IP/checkout information. It is not 100% accurate and should be used for trend and targeting decisions, not exact user location.",
    )


@router.get(
    "/analytics/signal-doctor",
    response_model=SignalDoctorResponse,
    summary="Signal Health Doctor â€” event quality diagnostics",
)
async def signal_doctor(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90),
):
    """Diagnose Facebook/TikTok/GA4 signal quality from recently delivered events."""
    start = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(EventLog)
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.status == "success",
            EventLog.created_at >= start,
        ))
        .order_by(EventLog.created_at.desc())
        .limit(5000)
    )
    logs = result.scalars().all()
    total = sum(int(log.event_count or 1) for log in logs)
    event_counts: dict[str, int] = {}
    for log in logs:
        name = log.event_name or "Unknown"
        event_counts[name] = event_counts.get(name, 0) + int(log.event_count or 1)

    commerce_events = {"ViewContent", "AddToCart", "ViewCart", "RemoveFromCart", "InitiateCheckout", "AddPaymentInfo", "Purchase"}
    commerce_logs = [log for log in logs if log.event_name in commerce_events]
    purchase_logs = [log for log in logs if log.event_name == "Purchase"]

    def rate(attr: str, source=None) -> float:
        source = logs if source is None else source
        source_total = sum(int(log.event_count or 1) for log in source)
        good = sum(int(log.event_count or 1) for log in source if bool(getattr(log, attr, False)))
        return _pct(good, source_total)

    signal_rates = {
        "event_id": rate("has_event_id"),
        "user_match": rate("has_user_match"),
        "email_or_phone": rate("has_email_phone"),
        "click_id": rate("has_click_id"),
        "content_ids": rate("has_content_ids", commerce_logs),
        "contents": rate("has_contents", commerce_logs),
        "value": rate("has_value", purchase_logs or commerce_logs),
        "currency": rate("has_currency", purchase_logs or commerce_logs),
        "utm": rate("has_utm"),
    }

    if total == 0:
        return SignalDoctorResponse(
            status="success",
            period_days=days,
            score=0,
            grade="No Data",
            total_events=0,
            platform_readiness={"facebook": 0, "tiktok": 0, "ga4": 0},
            signal_rates=signal_rates,
            event_counts={},
            issues=[
                SignalIssue(
                    severity="critical",
                    title="No events received",
                    metric="0 events",
                    impact="Facebook/TikTok/GA4 à¦•à§‹à¦¨à§‹ platform-à¦‡ optimize à¦•à¦°à¦¾à¦° à¦®à¦¤à§‹ signal à¦ªà¦¾à¦šà§à¦›à§‡ à¦¨à¦¾à¥¤",
                    fix="WordPress plugin/API key/domain setup à¦šà§‡à¦• à¦•à¦°à§‡ à¦à¦•à¦Ÿà¦¿ PageView à¦à¦¬à¦‚ Purchase test event à¦ªà¦¾à¦ à¦¾à¦¨à¥¤",
                )
            ],
        )

    issues: list[SignalIssue] = []
    score = 100

    if signal_rates["event_id"] < 95:
        score -= 12
        issues.append(SignalIssue(
            severity="high",
            title="Event ID coverage low",
            metric=f"{signal_rates['event_id']}%",
            impact="Browser/server deduplication à¦¦à§à¦°à§à¦¬à¦² à¦¹à¦¤à§‡ à¦ªà¦¾à¦°à§‡, à¦à¦•à¦‡ conversion double count à¦¹à¦“à§Ÿà¦¾à¦° à¦à§à¦à¦•à¦¿ à¦¥à¦¾à¦•à§‡à¥¤",
            fix="Official plugin à¦¬à¦¾ tracker à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦•à¦°à§à¦¨; server à¦à¦–à¦¨ missing event_id auto-generate à¦•à¦°à¦¬à§‡à¥¤",
        ))

    if commerce_logs and signal_rates["content_ids"] < 95:
        score -= 18
        issues.append(SignalIssue(
            severity="high",
            title="Content ID missing in commerce events",
            metric=f"{signal_rates['content_ids']}%",
            impact="TikTok/Facebook catalog product matching à¦¦à§à¦°à§à¦¬à¦² à¦¹à¦¬à§‡, shop ads optimization à¦•à¦®à§‡ à¦¯à¦¾à¦¬à§‡à¥¤",
            fix="Product ID/SKU à¦¥à§‡à¦•à§‡ content_ids à¦ªà¦¾à¦ à¦¾à¦¨à¥¤ Booster content_ids à¦¥à§‡à¦•à§‡ contents auto-build à¦•à¦°à¦¬à§‡, à¦•à¦¿à¦¨à§à¦¤à§ source payload-à¦ product ID à¦¥à¦¾à¦•à¦¾ à¦œà¦°à§à¦°à¦¿à¥¤",
        ))

    if purchase_logs and (signal_rates["value"] < 95 or signal_rates["currency"] < 95):
        score -= 14
        issues.append(SignalIssue(
            severity="high",
            title="Purchase value/currency incomplete",
            metric=f"value {signal_rates['value']}%, currency {signal_rates['currency']}%",
            impact="ROAS, revenue à¦à¦¬à¦‚ value optimization à¦­à§à¦² à¦¦à§‡à¦–à¦¾à¦¤à§‡ à¦ªà¦¾à¦°à§‡à¥¤",
            fix="Purchase custom_data-à¦¤à§‡ value à¦à¦¬à¦‚ ISO currency à¦¦à¦¿à¦¨à¥¤ Value à¦¥à¦¾à¦•à¦²à§‡ server default currency auto-fill à¦•à¦°à¦¬à§‡à¥¤",
        ))

    if signal_rates["user_match"] < 80:
        score -= 14
        issues.append(SignalIssue(
            severity="medium",
            title="User match signal weak",
            metric=f"{signal_rates['user_match']}%",
            impact="EMQ/Event Match Quality à¦•à¦®à§‡ à¦¯à§‡à¦¤à§‡ à¦ªà¦¾à¦°à§‡, conversion attribution à¦•à¦® match à¦¹à¦¬à§‡à¥¤",
            fix="Email/phone capture enable à¦°à¦¾à¦–à§à¦¨ à¦à¦¬à¦‚ browser cookies (_fbp/_fbc/_ttp/ttclid) pass à¦¹à¦šà§à¦›à§‡ à¦•à¦¿ à¦¨à¦¾ à¦¦à§‡à¦–à§à¦¨à¥¤",
        ))

    if signal_rates["email_or_phone"] < 30:
        score -= 8
        issues.append(SignalIssue(
            severity="medium",
            title="Email/phone signal low",
            metric=f"{signal_rates['email_or_phone']}%",
            impact="Purchase/Lead match quality à¦•à¦® à¦¹à¦¤à§‡ à¦ªà¦¾à¦°à§‡, à¦¬à¦¿à¦¶à§‡à¦· à¦•à¦°à§‡ COD/ecommerce orders-à¦à¥¤",
            fix="Checkout/order data à¦¥à§‡à¦•à§‡ email à¦à¦¬à¦‚ phone à¦ªà¦¾à¦ à¦¾à¦¨à¥¤ Server raw value à¦ªà§‡à¦²à§‡ SHA-256 hash à¦•à¦°à§‡ à¦¦à§‡à¦¬à§‡à¥¤",
        ))

    if signal_rates["utm"] < 50:
        score -= 8
        issues.append(SignalIssue(
            severity="low",
            title="Campaign attribution missing",
            metric=f"{signal_rates['utm']}%",
            impact="Facebook vs TikTok campaign comparison à¦ªà¦°à¦¿à¦·à§à¦•à¦¾à¦° à¦¹à¦¬à§‡ à¦¨à¦¾à¥¤",
            fix="Campaign URL Builder à¦¦à¦¿à§Ÿà§‡ ad destination URL à¦¬à¦¾à¦¨à¦¿à§Ÿà§‡ à¦ªà§à¦°à¦¤à¦¿à¦Ÿà¦¿ campaign-à¦ à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦•à¦°à§à¦¨à¥¤",
        ))

    for required_event in ["ViewContent", "AddToCart", "InitiateCheckout", "Purchase"]:
        if event_counts.get(required_event, 0) == 0:
            score -= 5
            issues.append(SignalIssue(
                severity="medium",
                title=f"{required_event} event not seen",
                metric="0 events",
                impact="Full funnel optimization à¦“ diagnostics à¦…à¦¸à¦®à§à¦ªà§‚à¦°à§à¦£ à¦¥à¦¾à¦•à¦¬à§‡à¥¤",
                fix=f"Plugin settings-à¦ {required_event} enabled à¦†à¦›à§‡ à¦•à¦¿ à¦¨à¦¾ à¦à¦¬à¦‚ site flow à¦¥à§‡à¦•à§‡ event fire à¦¹à¦šà§à¦›à§‡ à¦•à¦¿ à¦¨à¦¾ test à¦•à¦°à§à¦¨à¥¤",
            ))

    if not issues:
        issues.append(SignalIssue(
            severity="ok",
            title="Signals look healthy",
            metric="All key checks passed",
            impact="Facebook/TikTok/GA4 optimization-à¦à¦° à¦œà¦¨à§à¦¯ current event quality à¦­à¦¾à¦²à§‹à¥¤",
            fix="Campaign URL Builder à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦•à¦°à§‡ UTM discipline maintain à¦•à¦°à§à¦¨à¥¤",
        ))

    score = max(0, min(100, score))
    fb_ready = round((signal_rates["event_id"] + signal_rates["user_match"] + signal_rates["value"] + signal_rates["currency"]) / 4)
    tt_ready = round((signal_rates["event_id"] + signal_rates["user_match"] + signal_rates["content_ids"] + signal_rates["contents"]) / 4)
    ga_ready = round((signal_rates["value"] + signal_rates["currency"] + signal_rates["content_ids"] + signal_rates["utm"]) / 4)

    return SignalDoctorResponse(
        status="success",
        period_days=days,
        score=score,
        grade=_grade(score),
        total_events=total,
        platform_readiness={"facebook": fb_ready, "tiktok": tt_ready, "ga4": ga_ready},
        signal_rates=signal_rates,
        event_counts=event_counts,
        issues=issues,
    )


# â”€â”€â”€ GET /analytics/export â€” CSV Export â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/analytics/export",
    summary="Export event logs as CSV",
)
async def analytics_export(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30),
):
    """à¦¸à¦°à§à¦¬à¦¶à§‡à¦· N à¦¦à¦¿à¦¨à§‡à¦° event logs CSV à¦¹à¦¿à¦¸à§‡à¦¬à§‡ à¦¡à¦¾à¦‰à¦¨à¦²à§‹à¦¡"""
    start = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(EventLog)
        .where(and_(
            EventLog.client_id == client.id,
            EventLog.created_at >= start,
        ))
        .order_by(EventLog.created_at.desc())
        .limit(5000)
    )
    logs = result.scalars().all()

    # CSV generate
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "Event Name", "Event ID", "Status", "Count", "Value", "Currency", "UTM Source", "UTM Campaign", "IP Address"])

    for log in logs:
        writer.writerow([
            log.created_at.isoformat() if log.created_at else "",
            log.event_name or "",
            log.event_id or "",
            log.status or "",
            log.event_count or 0,
            log.value or "",
            log.currency or "",
            log.utm_source or log.campaign_source or "",
            log.utm_campaign or "",
            log.ip_address or "",
        ])

    output.seek(0)
    filename = f"events_{client.name}_{days}days.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# â”€â”€â”€ GET /analytics/ad-performance â€” Ad Performance & ROAS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/analytics/ad-performance",
    response_model=AdPerformanceResponse,
    summary="Get ad performance and ROAS analytics",
)
async def ad_performance_analytics(
    client: CachedClient = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="à¦•à¦¤ à¦¦à¦¿à¦¨à§‡à¦° à¦¡à§‡à¦Ÿà¦¾"),
):
    """
    Meta à¦à¦¬à¦‚ TikTok à¦•à§à¦¯à¦¾à¦®à§à¦ªà§‡à¦‡à¦¨à§‡à¦° ad spend à¦à¦¬à¦‚ CAPI conversion-à¦à¦° timezone-aware aggregate ROAS/CPA à¦¹à¦¿à¦¸à¦¾à¦¬ à¦•à¦°à§‡à¥¤
    """
    # Calculate start and end date based on days parameter
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days - 1)

    is_postgresql = db.bind.dialect.name == "postgresql"

    if is_postgresql:
        tz_log_expr = "DATE(timezone(a.account_timezone, el.created_at))"
        tz_pending_expr = "DATE(timezone(a.account_timezone, pe.created_at))"
        json_camp_id_expr = "COALESCE(pe.event_data->'custom_data'->>'ad_campaign_id', pe.event_data->'custom_data'->>'bk_campaign_id', pe.event_data->>'ad_campaign_id')"
        json_platform_expr = "COALESCE(pe.event_data->'custom_data'->>'ad_platform', pe.event_data->'custom_data'->>'bk_platform', pe.event_data->>'ad_platform')"
        json_val_expr = "CAST(COALESCE(pe.event_data->'custom_data'->>'value', '0.0') AS float)"
        json_currency_expr = "COALESCE(pe.event_data->'custom_data'->>'currency', pe.event_data->>'currency')"
    else:
        tz_log_expr = "DATE(el.created_at)"
        tz_pending_expr = "DATE(pe.created_at)"
        json_camp_id_expr = "COALESCE(json_extract(pe.event_data, '$.custom_data.ad_campaign_id'), json_extract(pe.event_data, '$.custom_data.bk_campaign_id'), json_extract(pe.event_data, '$.ad_campaign_id'))"
        json_platform_expr = "COALESCE(json_extract(pe.event_data, '$.custom_data.ad_platform'), json_extract(pe.event_data, '$.custom_data.bk_platform'), json_extract(pe.event_data, '$.ad_platform'))"
        json_val_expr = "CAST(COALESCE(json_extract(pe.event_data, '$.custom_data.value'), 0.0) AS float)"
        json_currency_expr = "COALESCE(json_extract(pe.event_data, '$.custom_data.currency'), json_extract(pe.event_data, '$.currency'))"

    query_str = f"""
    WITH campaign_identity AS (
        SELECT
            c.external_campaign_id,
            COUNT(DISTINCT c.platform) AS platform_count
        FROM ad_campaigns c
        JOIN ad_accounts a ON c.ad_account_id = a.id
        WHERE a.client_id = :client_id
        GROUP BY c.external_campaign_id
    ),
    platform_insights AS (
        SELECT
            platform,
            external_campaign_id,
            SUM(spend) AS total_spend,
            SUM(clicks) AS total_clicks,
            SUM(impressions) AS total_impressions,
            MAX(currency) AS spend_currency
        FROM ad_insights_daily
        WHERE client_id = :client_id
          AND insight_date BETWEEN :start_date AND :end_date
        GROUP BY platform, external_campaign_id
    ),
    server_confirmed_events AS (
        SELECT
            c.id AS campaign_pk,
            COALESCE(SUM(el.event_count), 0) AS purchases,
            COALESCE(SUM(el.value), 0.0) AS revenue,
            MAX(el.currency) AS revenue_currency
        FROM event_logs el
        JOIN ad_campaigns c ON el.ad_campaign_id = c.external_campaign_id
        JOIN ad_accounts a ON c.ad_account_id = a.id
        JOIN campaign_identity ci ON c.external_campaign_id = ci.external_campaign_id
        WHERE el.client_id = :client_id
          AND a.client_id = :client_id
          AND (el.ad_platform = c.platform OR (el.ad_platform IS NULL AND ci.platform_count = 1))
          AND el.event_name = 'Purchase'
          AND el.status = 'success'
          AND {tz_log_expr} BETWEEN :start_date AND :end_date
        GROUP BY c.id
    ),
    server_held_events AS (
        SELECT
            c.id AS campaign_pk,
            COUNT(pe.id) AS purchases,
            COALESCE(SUM({json_val_expr}), 0.0) AS revenue,
            MAX({json_currency_expr}) AS revenue_currency
        FROM pending_events pe
        JOIN ad_campaigns c ON ({json_camp_id_expr}) = c.external_campaign_id
        JOIN ad_accounts a ON c.ad_account_id = a.id
        JOIN campaign_identity ci ON c.external_campaign_id = ci.external_campaign_id
        WHERE pe.client_id = :client_id
          AND a.client_id = :client_id
          AND ({json_platform_expr} = c.platform OR ({json_platform_expr} IS NULL AND ci.platform_count = 1))
          AND pe.status IN ('pending', 'cancelled', 'expired', 'courier_booked', 'courier_booking_queued')
          AND {tz_pending_expr} BETWEEN :start_date AND :end_date
        GROUP BY c.id
    ),
    browser_vs_server_discrepancy AS (
        SELECT
            c.id AS campaign_pk,
            SUM(CASE WHEN el.event_name LIKE 'Browser%%:PageView' THEN 1 ELSE 0 END) AS browser_views,
            SUM(CASE WHEN el.event_name = 'PageView' AND el.status = 'success' THEN 1 ELSE 0 END) AS server_views
        FROM event_logs el
        JOIN ad_campaigns c ON el.ad_campaign_id = c.external_campaign_id
        JOIN ad_accounts a ON c.ad_account_id = a.id
        JOIN campaign_identity ci ON c.external_campaign_id = ci.external_campaign_id
        WHERE el.client_id = :client_id
          AND a.client_id = :client_id
          AND (el.ad_platform = c.platform OR (el.ad_platform IS NULL AND ci.platform_count = 1))
          AND {tz_log_expr} BETWEEN :start_date AND :end_date
        GROUP BY c.id
    )
    SELECT
        c.external_campaign_id AS campaign_id,
        c.name AS campaign_name,
        c.platform,
        COALESCE(p.total_spend, 0.0) AS spend,
        COALESCE(p.spend_currency, a.account_currency, '') AS spend_currency,
        COALESCE(p.total_clicks, 0) AS clicks,
        COALESCE(p.total_impressions, 0) AS impressions,

        -- Confirmed metrics
        COALESCE(sc.purchases, 0) AS confirmed_purchases,
        COALESCE(sc.revenue, 0.0) AS confirmed_revenue,
        COALESCE(sc.revenue_currency, sh.revenue_currency, '') AS revenue_currency,

        -- Placed metrics = Confirmed + Held
        (COALESCE(sc.purchases, 0) + COALESCE(sh.purchases, 0)) AS placed_purchases,
        (COALESCE(sc.revenue, 0.0) + COALESCE(sh.revenue, 0.0)) AS placed_revenue,

        -- Discrepancy metrics
        COALESCE(d.browser_views, 0) AS browser_page_views,
        COALESCE(d.server_views, 0) AS server_page_views
    FROM ad_campaigns c
    JOIN ad_accounts a ON c.ad_account_id = a.id
    LEFT JOIN platform_insights p ON c.external_campaign_id = p.external_campaign_id AND c.platform = p.platform
    LEFT JOIN server_confirmed_events sc ON c.id = sc.campaign_pk
    LEFT JOIN server_held_events sh ON c.id = sh.campaign_pk
    LEFT JOIN browser_vs_server_discrepancy d ON c.id = d.campaign_pk
    WHERE a.client_id = :client_id;
    """

    try:
        result = await db.execute(
            text(query_str),
            {
                "client_id": client.id,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        rows = result.fetchall()
    except Exception as exc:
        logger.error(f"Error executing ad-performance query: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Error retrieving ad performance analytics from the database."
        )

    data = []
    for r in rows:
        spend = float(r.spend or 0.0)
        spend_currency = getattr(r, "spend_currency", "") or ""
        clicks = int(r.clicks or 0)
        impressions = int(r.impressions or 0)

        ctr = round((clicks / impressions * 100) if impressions > 0 else 0.0, 2)
        cpc = round((spend / clicks) if clicks > 0 else 0.0, 2)

        placed_purchases = int(r.placed_purchases or 0)
        placed_revenue = float(r.placed_revenue or 0.0)
        placed_roas = round((placed_revenue / spend) if spend > 0 else 0.0, 2)
        placed_cpa = round((spend / placed_purchases) if placed_purchases > 0 else 0.0, 2)

        confirmed_purchases = int(r.confirmed_purchases or 0)
        confirmed_revenue = float(r.confirmed_revenue or 0.0)
        revenue_currency = getattr(r, "revenue_currency", "") or ""
        confirmed_roas = round((confirmed_revenue / spend) if spend > 0 else 0.0, 2)
        confirmed_cpa = round((spend / confirmed_purchases) if confirmed_purchases > 0 else 0.0, 2)

        browser_page_views = int(r.browser_page_views or 0)
        server_page_views = int(r.server_page_views or 0)

        if server_page_views > 0:
            tracking_bypass_rate = round(max(0.0, ((server_page_views - browser_page_views) / server_page_views) * 100), 1)
        else:
            tracking_bypass_rate = 0.0

        data.append(
            AdPerformanceRow(
                campaign_id=r.campaign_id,
                campaign_name=r.campaign_name,
                platform=r.platform,
                spend=spend,
                spend_currency=spend_currency,
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                cpc=cpc,
                placed_purchases=placed_purchases,
                placed_revenue=placed_revenue,
                placed_roas=placed_roas,
                placed_cpa=placed_cpa,
                confirmed_purchases=confirmed_purchases,
                confirmed_revenue=confirmed_revenue,
                revenue_currency=revenue_currency,
                confirmed_roas=confirmed_roas,
                confirmed_cpa=confirmed_cpa,
                browser_page_views=browser_page_views,
                server_page_views=server_page_views,
                tracking_bypass_rate=tracking_bypass_rate,
            )
        )

    return AdPerformanceResponse(
        status="success",
        period_days=days,
        sync_enabled=os.getenv("ENABLE_AD_SYNC", "").lower() in ("true", "1", "yes"),
        data=data
    )
