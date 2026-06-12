import os
import html
import secrets
import logging
import hashlib
import hmac
import time
from urllib.parse import urlencode, urlparse
from fastapi import APIRouter, Depends, HTTPException, Form, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete as sql_delete, select, update, func
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.models.client import Client
from app.models.audit_log import AuditLog
from app.models.event_dedup import EventDedup
from app.models.event_log import EventLog
from app.models.event_outbox import EventOutbox
from app.models.failed_event import FailedEvent
from app.models.pending_event import PendingEvent
from app.models.site_binding import SiteBinding
from app.models.usage_counter import UsageCounter
from app.models.client_session import ClientSession
from app.models.client_user import ClientUser
from app.models.plugin_connect_session import PluginConnectSession
from app.models.trial_identity import TrialIdentity
from app.models.notification_job import NotificationJob
from app.models.incomplete_checkout import IncompleteCheckout
from app.models.courier_booking_job import CourierBookingJob
from app.models.courier_order import CourierOrder
from app.models.ad_insight_daily import AdInsightDaily
from app.models.ad_account import AdAccount
from app.models.ad_campaign import AdCampaign
from app.models.client_support_note import ClientSupportNote
from app.security import encrypt_token
from app.services.auth_service import verify_admin_password
from app.services.webhook_service import _webhook_url_allowed
from app.services.plan_service import (
    assign_paid_plan,
    normalize_billing_status,
    normalize_plan_tier,
    plan_summary,
    record_trial_identity,
    require_trial_available,
    start_growth_trial,
)
from app.limiter import limiter, _get_real_ip
from app.utils.display import normalize_domain_input, display_domain_url, mask_secret

logger = logging.getLogger(__name__)
router = APIRouter()
security = HTTPBasic()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    logger.warning("⚠️ ADMIN_PASSWORD environment variable is not set! Admin views will be disabled.")

CSRF_MAX_AGE_SECONDS = 60 * 60

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="Admin configuration error: ADMIN_PASSWORD is not set."
        )
    is_user_ok = secrets.compare_digest(credentials.username, ADMIN_USERNAME)

    is_pass_ok = verify_admin_password(credentials.password, ADMIN_PASSWORD)

    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def create_admin_csrf_token(username: str) -> str:
    nonce = secrets.token_urlsafe(24)
    issued_at = str(int(time.time()))
    payload = f"{username}:{issued_at}:{nonce}"
    signature = hmac.new(
        ADMIN_PASSWORD.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{issued_at}:{nonce}:{signature}"

def verify_admin_csrf_token(token: str, username: str) -> None:
    try:
        issued_at, nonce, signature = token.split(":", 2)
        issued_ts = int(issued_at)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    if time.time() - issued_ts > CSRF_MAX_AGE_SECONDS:
        raise HTTPException(status_code=403, detail="Expired CSRF token")

    payload = f"{username}:{issued_at}:{nonce}"
    expected = hmac.new(
        ADMIN_PASSWORD.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

templates = Jinja2Templates(directory="app/templates")

def admin_redirect(msg: str, msg_type: str = "success") -> RedirectResponse:
    query = urlencode({"msg": msg, "msg_type": msg_type})
    return RedirectResponse(url=f"/api/v1/admin?{query}", status_code=303)


def admin_clients_redirect(msg: str, msg_type: str = "success") -> RedirectResponse:
    query = urlencode({"msg": msg, "msg_type": msg_type})
    return RedirectResponse(url=f"/api/v1/admin/clients?{query}", status_code=303)

templates.env.globals["mask_secret"] = mask_secret
templates.env.globals["display_domain_url"] = display_domain_url
templates.env.globals["plan_summary"] = plan_summary

def request_ip(request: Request) -> str | None:
    return _get_real_ip(request)

async def log_admin_action(
    db: AsyncSession,
    request: Request,
    actor: str,
    action: str,
    client_id: int | None = None,
    details: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            client_id=client_id,
            ip_address=request_ip(request),
            details=details,
        )
    )

@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("10/minute")
async def admin_dashboard(
    request: Request,
    username: str = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
    msg: str = "",
    msg_type: str = "success",
):
    csrf_token = create_admin_csrf_token(username)
    result = await db.execute(select(Client).order_by(Client.created_at.desc()))
    clients = result.scalars().all()

    audit_r = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(12))

    from datetime import datetime, timezone
    from sqlalchemy import func as sql_func, and_

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    success_r = await db.execute(
        select(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0)).where(
            and_(EventLog.status == "success", EventLog.created_at >= today)
        )
    )
    events_today = success_r.scalar() or 0

    client_events_r = await db.execute(
        select(EventLog.client_id, sql_func.coalesce(sql_func.sum(EventLog.event_count), 0))
        .where(and_(EventLog.status == "success", EventLog.created_at >= today))
        .group_by(EventLog.client_id)
    )
    client_events_map = {row[0]: row[1] for row in client_events_r}

    fail_r = await db.execute(
        select(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0)).where(
            and_(EventLog.status == "failed", EventLog.created_at >= today)
        )
    )
    failed_today = fail_r.scalar() or 0

    retry_r = await db.execute(
        select(sql_func.count(FailedEvent.id)).where(
            FailedEvent.status.in_(["pending", "retrying"])
        )
    )
    retries = retry_r.scalar() or 0

    outbox_r = await db.execute(
        select(sql_func.count(EventOutbox.id)).where(
            EventOutbox.status.in_(["queued", "processing"])
        )
    )
    queued_events = outbox_r.scalar() or 0

    dead_outbox_r = await db.execute(
        select(sql_func.count(EventOutbox.id)).where(EventOutbox.status == "dead")
    )
    dead_outbox = dead_outbox_r.scalar() or 0

    oldest_outbox_r = await db.execute(
        select(sql_func.min(EventOutbox.created_at)).where(
            EventOutbox.status.in_(["queued", "processing"])
        )
    )
    oldest_outbox_at = oldest_outbox_r.scalar()

    last_outbox_error_r = await db.execute(
        select(EventOutbox.last_error)
        .where(and_(EventOutbox.status == "dead", EventOutbox.last_error.is_not(None)))
        .order_by(EventOutbox.created_at.desc())
        .limit(1)
    )
    last_outbox_error = last_outbox_error_r.scalar()

    if oldest_outbox_at:
        if oldest_outbox_at.tzinfo is None:
            oldest_outbox_at = oldest_outbox_at.replace(tzinfo=timezone.utc)
        oldest_seconds = max(0, int((datetime.now(timezone.utc) - oldest_outbox_at).total_seconds()))
        if oldest_seconds >= 3600:
            oldest_outbox_age = f"{oldest_seconds // 3600}h"
        elif oldest_seconds >= 60:
            oldest_outbox_age = f"{oldest_seconds // 60}m"
        else:
            oldest_outbox_age = f"{oldest_seconds}s"
    else:
        oldest_outbox_age = "none"

    outbox_error_title = html.escape(last_outbox_error or "")

    total_calls = events_today + failed_today
    success_rate = round(events_today / total_calls * 100, 1) if total_calls > 0 else 100.0

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "title": "Dashboard",
            "active_page": "dashboard",
            "csrf_token": csrf_token,
            "clients": clients,
            "client_events_map": client_events_map,
            "events_today": events_today,
            "failed_today": failed_today,
            "retries": retries,
            "queued_events": queued_events,
            "dead_outbox": dead_outbox,
            "oldest_outbox_age": oldest_outbox_age,
            "outbox_error_title": outbox_error_title,
            "success_rate": success_rate,
            "msg": msg,
            "msg_type": msg_type,
        }
    )

@router.post("/admin/add-client", include_in_schema=False)
@limiter.limit("10/minute")
async def add_client(
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    name: str = Form(...),
    pixel_id: str = Form(...),
    access_token: str = Form(...),
    test_event_code: str = Form(None),
    domain: str = Form(None),
    tiktok_pixel_id: str = Form(None),
    tiktok_access_token: str = Form(None),
    tiktok_test_event_code: str = Form(None),
    ga4_measurement_id: str = Form(None),
    ga4_api_secret: str = Form(None),
    enable_facebook: str = Form(None),
    enable_tiktok: str = Form(None),
    enable_ga4: str = Form(None),
    deferred_purchase: str = Form(None),
    webhook_url: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)

    name = name.strip()
    pixel_id = pixel_id.strip()
    access_token = access_token.strip()

    errors = []
    if not name or len(name) > 100:
        errors.append("নাম ১-১০০ অক্ষরের মধ্যে হতে হবে।")
    if not pixel_id.isdigit():
        errors.append("Pixel ID শুধু সংখ্যা হতে হবে।")
    if len(access_token) < 10:
        errors.append("Access Token কমপক্ষে ১০ অক্ষরের হতে হবে।")

    if errors:
        error_msg = " | ".join(errors)
        return admin_redirect(error_msg, "error")

    clean_webhook_url = webhook_url.strip() if webhook_url and webhook_url.strip() else None
    if clean_webhook_url:
        parsed_webhook = urlparse(clean_webhook_url)
        if parsed_webhook.scheme not in ("https", "http") or not parsed_webhook.netloc:
            return admin_redirect("Webhook URL must be a valid http(s) URL.", "error")
        if not await _webhook_url_allowed(clean_webhook_url):
            return admin_redirect("Webhook URL is not allowed. Use a public http(s) endpoint.", "error")

    clean_domain = normalize_domain_input(domain)

    new_client = Client(
        name=name,
        pixel_id=pixel_id,
        access_token=encrypt_token(access_token),
        test_event_code=test_event_code.strip() if test_event_code else None,
        domain=clean_domain,
        api_key=secrets.token_urlsafe(32),
        public_key=secrets.token_urlsafe(24),
        portal_key=secrets.token_urlsafe(24),
        enable_facebook=enable_facebook == "1",
        enable_tiktok=enable_tiktok == "1",
        enable_ga4=enable_ga4 == "1",
        tiktok_pixel_id=tiktok_pixel_id.strip() if tiktok_pixel_id and tiktok_pixel_id.strip() else None,
        tiktok_access_token=encrypt_token(tiktok_access_token.strip()) if tiktok_access_token and tiktok_access_token.strip() else None,
        tiktok_test_event_code=tiktok_test_event_code.strip() if tiktok_test_event_code and tiktok_test_event_code.strip() else None,
        ga4_measurement_id=ga4_measurement_id.strip() if ga4_measurement_id and ga4_measurement_id.strip() else None,
        ga4_api_secret=encrypt_token(ga4_api_secret.strip()) if ga4_api_secret and ga4_api_secret.strip() else None,
        deferred_purchase=deferred_purchase == "1",
        webhook_url=clean_webhook_url,
    )
    db.add(new_client)
    await db.commit()
    await db.refresh(new_client)
    await log_admin_action(db, request, username, "client.added", new_client.id, f"Client {name} added")
    await db.commit()
    logger.info(f"New client added: {name}")

    return admin_redirect(f"✅ {name} সফলভাবে যোগ হয়েছে!")

@router.get("/admin/client/{client_id}/instructions", response_class=HTMLResponse, include_in_schema=False)
async def client_instructions(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    base_url = str(request.base_url).rstrip("/")
    endpoint = f"{base_url}/api/v1/events"
    tracker_key = getattr(client, "public_key", None) or client.api_key
    tracker_url = f"{base_url}/t.js?key={tracker_key}"

    return templates.TemplateResponse(
        request,
        "admin/instructions.html",
        {
            "title": f"Instructions — {client.name}",
            "active_page": "clients",
            "client": client,
            "portal_key": getattr(client, "portal_key", None) or client.api_key,
            "masked_api_key": mask_secret(client.api_key),
            "masked_portal_key": mask_secret(getattr(client, "portal_key", None) or client.api_key),
            "masked_public_key": mask_secret(getattr(client, "public_key", None) or ""),
            "endpoint": endpoint,
            "tracker_url": tracker_url,
            "capi_origin": display_domain_url(client.domain) or "https://www.your-domain.com",
        }
    )

async def rotate_client_key(
    db: AsyncSession,
    request: Request,
    username: str,
    client_id: int,
    key_type: str,
) -> RedirectResponse:
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    old_api_key = client.api_key
    if key_type == "api":
        client.api_key = secrets.token_urlsafe(32)
        message = "API key rotated. Update WordPress plugin/server integrations."
        action = "client.api_key_rotated"
    elif key_type == "public":
        client.public_key = secrets.token_urlsafe(24)
        message = "Public tracker key rotated. Update t.js script URLs."
        action = "client.public_key_rotated"
    elif key_type == "portal":
        client.portal_key = secrets.token_urlsafe(24)
        message = "Portal login key rotated."
        action = "client.portal_key_rotated"
    else:
        raise HTTPException(status_code=400, detail="Invalid key type")

    await log_admin_action(db, request, username, action, client_id)
    await db.commit()

    from app.dependencies import clear_client_cache
    clear_client_cache(old_api_key)

    return admin_redirect(message)

@router.post("/admin/client/{client_id}/rotate-api-key", include_in_schema=False)
async def rotate_api_key(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)
    return await rotate_client_key(db, request, username, client_id, "api")

@router.post("/admin/client/{client_id}/rotate-public-key", include_in_schema=False)
async def rotate_public_key(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)
    return await rotate_client_key(db, request, username, client_id, "public")

@router.post("/admin/client/{client_id}/rotate-portal-key", include_in_schema=False)
async def rotate_portal_key(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)
    return await rotate_client_key(db, request, username, client_id, "portal")

@router.post("/admin/client/{client_id}/deactivate", include_in_schema=False)
async def deactivate_client(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)

    result = await db.execute(update(Client).where(Client.id == client_id).values(is_active=False).returning(Client.api_key))
    api_key = result.scalar()
    await log_admin_action(db, request, username, "client.deactivated", client_id)
    await db.commit()

    if api_key:
        from app.dependencies import clear_client_cache
        clear_client_cache(api_key)

    return admin_redirect("ক্লায়েন্ট Deactivate করা হয়েছে")

@router.post("/admin/client/{client_id}/activate", include_in_schema=False)
async def activate_client(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)

    result = await db.execute(update(Client).where(Client.id == client_id).values(is_active=True).returning(Client.api_key))
    api_key = result.scalar()
    await log_admin_action(db, request, username, "client.activated", client_id)
    await db.commit()

    if api_key:
        from app.dependencies import clear_client_cache
        clear_client_cache(api_key)

    return admin_redirect("ক্লায়েন্ট Activate করা হয়েছে")

@router.post("/admin/client/{client_id}/delete", include_in_schema=False)
async def delete_client(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)

    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        return admin_redirect("Client not found", "error")

    client_name = client.name
    api_key = client.api_key

    # Delete client dependencies in order to avoid foreign key violations
    await db.execute(sql_delete(CourierBookingJob).where(CourierBookingJob.client_id == client_id))
    await db.execute(sql_delete(CourierOrder).where(CourierOrder.client_id == client_id))
    await db.execute(sql_delete(AdCampaign).where(AdCampaign.ad_account_id.in_(select(AdAccount.id).where(AdAccount.client_id == client_id))))
    await db.execute(sql_delete(AdAccount).where(AdAccount.client_id == client_id))
    await db.execute(sql_delete(AdInsightDaily).where(AdInsightDaily.client_id == client_id))
    await db.execute(sql_delete(NotificationJob).where(NotificationJob.client_id == client_id))
    await db.execute(sql_delete(IncompleteCheckout).where(IncompleteCheckout.client_id == client_id))
    await db.execute(sql_delete(PluginConnectSession).where(PluginConnectSession.client_id == client_id))
    await db.execute(sql_delete(TrialIdentity).where(TrialIdentity.client_id == client_id))
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
    await log_admin_action(db, request, username, "client.deleted", client_id, f"Deleted client: {client_name}")
    await db.commit()

    if api_key:
        from app.dependencies import clear_client_cache
        clear_client_cache(api_key)

    return admin_redirect(f"Client deleted: {client_name}")

@router.get("/admin/clients", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("10/minute")
async def admin_clients(
    request: Request,
    username: str = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
    msg: str = "",
    msg_type: str = "success",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    csrf_token = create_admin_csrf_token(username)
    from sqlalchemy import func as sql_func

    # Compute counts using count queries to avoid loading all objects
    total_clients_r = await db.execute(select(sql_func.count(Client.id)))
    total_clients = total_clients_r.scalar() or 0

    active_count_r = await db.execute(select(sql_func.count(Client.id)).where(Client.is_active == True))
    active_count = active_count_r.scalar() or 0
    inactive_count = total_clients - active_count

    total_pages = (total_clients + page_size - 1) // page_size if total_clients > 0 else 1

    # Fetch paginated clients
    result = await db.execute(
        select(Client)
        .order_by(Client.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    clients = result.scalars().all()

    from datetime import datetime, timezone
    from sqlalchemy import func as sql_func, and_
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now(timezone.utc)
    monthly_key_prefix = f"monthly:{now.strftime('%Y-%m')}"

    client_events_r = await db.execute(
        select(EventLog.client_id, sql_func.coalesce(sql_func.sum(EventLog.event_count), 0))
        .where(and_(EventLog.status == "success", EventLog.created_at >= today))
        .group_by(EventLog.client_id)
    )
    client_events_map = {row[0]: row[1] for row in client_events_r}

    monthly_usage_r = await db.execute(
        select(UsageCounter.client_id, UsageCounter.count)
        .where(UsageCounter.window_key == monthly_key_prefix)
    )
    monthly_usage_map = {row[0]: row[1] for row in monthly_usage_r}

    return templates.TemplateResponse(
        request,
        "admin/clients.html",
        {
            "title": "Clients",
            "active_page": "clients",
            "csrf_token": csrf_token,
            "clients": clients,
            "active_count": active_count,
            "inactive_count": inactive_count,
            "client_events_map": client_events_map,
            "monthly_usage_map": monthly_usage_map,
            "msg": msg,
            "msg_type": msg_type,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_clients": total_clients,
        }
    )

@router.get("/admin/client/{client_id}/edit", response_class=HTMLResponse, include_in_schema=False)
async def edit_client_form(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
    msg: str = "",
    msg_type: str = "success",
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    csrf_token = create_admin_csrf_token(username)

    return templates.TemplateResponse(
        request,
        "admin/edit.html",
        {
            "title": f"Edit — {client.name}",
            "active_page": "clients",
            "client": client,
            "csrf_token": csrf_token,
            "display_domain": display_domain_url(client.domain),
            "has_access_token": bool(client.access_token),
            "has_tiktok_token": bool(client.tiktok_access_token),
            "has_ga4_secret": bool(client.ga4_api_secret),
            "msg": msg,
            "msg_type": msg_type,
        }
    )

@router.post("/admin/client/{client_id}/edit", include_in_schema=False)
async def edit_client_submit(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    name: str = Form(...),
    pixel_id: str = Form(...),
    access_token: str = Form(""),
    test_event_code: str = Form(""),
    domain: str = Form(""),
    tiktok_pixel_id: str = Form(""),
    tiktok_access_token: str = Form(""),
    tiktok_test_event_code: str = Form(""),
    ga4_measurement_id: str = Form(""),
    ga4_api_secret: str = Form(""),
    enable_facebook: str = Form(None),
    enable_tiktok: str = Form(None),
    enable_ga4: str = Form(None),
    deferred_purchase: str = Form(None),
    webhook_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)

    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    name = name.strip()
    pixel_id = pixel_id.strip()
    if not name or len(name) > 100:
        q = urlencode({"msg": "নাম ১-১০০ অক্ষরের মধ্যে হতে হবে।", "msg_type": "error"})
        return RedirectResponse(url=f"/api/v1/admin/client/{client_id}/edit?{q}", status_code=303)
    if not pixel_id.isdigit():
        q = urlencode({"msg": "Pixel ID শুধু সংখ্যা হতে হবে।", "msg_type": "error"})
        return RedirectResponse(url=f"/api/v1/admin/client/{client_id}/edit?{q}", status_code=303)

    clean_domain = normalize_domain_input(domain)

    clean_webhook = webhook_url.strip() if webhook_url and webhook_url.strip() else None
    if clean_webhook:
        parsed = urlparse(clean_webhook)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            q = urlencode({"msg": "Webhook URL must be a valid http(s) URL.", "msg_type": "error"})
            return RedirectResponse(url=f"/api/v1/admin/client/{client_id}/edit?{q}", status_code=303)
        if not await _webhook_url_allowed(clean_webhook):
            q = urlencode({"msg": "Webhook URL is not allowed.", "msg_type": "error"})
            return RedirectResponse(url=f"/api/v1/admin/client/{client_id}/edit?{q}", status_code=303)

    client.name = name
    client.pixel_id = pixel_id
    client.domain = clean_domain
    client.test_event_code = test_event_code.strip() if test_event_code and test_event_code.strip() else None
    client.enable_facebook = (enable_facebook == "1")
    client.enable_tiktok = (enable_tiktok == "1")
    client.enable_ga4 = (enable_ga4 == "1")
    client.deferred_purchase = (deferred_purchase == "1")
    client.webhook_url = clean_webhook
    client.tiktok_pixel_id = tiktok_pixel_id.strip() if tiktok_pixel_id and tiktok_pixel_id.strip() else None
    client.tiktok_test_event_code = tiktok_test_event_code.strip() if tiktok_test_event_code and tiktok_test_event_code.strip() else None
    client.ga4_measurement_id = ga4_measurement_id.strip() if ga4_measurement_id and ga4_measurement_id.strip() else None

    if access_token and access_token.strip():
        client.access_token = encrypt_token(access_token.strip())
    if tiktok_access_token and tiktok_access_token.strip():
        client.tiktok_access_token = encrypt_token(tiktok_access_token.strip())
    if ga4_api_secret and ga4_api_secret.strip():
        client.ga4_api_secret = encrypt_token(ga4_api_secret.strip())

    await log_admin_action(db, request, username, "client.updated", client_id, f"Client {name} updated")
    await db.commit()

    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)

    q = urlencode({"msg": f"✅ {name} সফলভাবে আপডেট হয়েছে!", "msg_type": "success"})
    return RedirectResponse(url=f"/api/v1/admin/clients?{q}", status_code=303)

@router.post("/admin/client/{client_id}/update-monthly-limit", include_in_schema=False)
async def update_monthly_limit(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    monthly_limit: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)

    if monthly_limit < 0:
        query = urlencode({"msg": "Monthly limit must be >= 0", "msg_type": "error"})
        return RedirectResponse(url=f"/api/v1/admin/clients?{query}", status_code=303)

    await db.execute(
        update(Client).where(Client.id == client_id).values(monthly_limit=monthly_limit)
    )
    await log_admin_action(db, request, username, "client.monthly_limit_updated", client_id, f"New limit: {monthly_limit:,}")
    await db.commit()

    result = await db.execute(select(Client.api_key).where(Client.id == client_id))
    api_key = result.scalar()
    if api_key:
        from app.dependencies import clear_client_cache
        clear_client_cache(api_key)

    query = urlencode({"msg": f"Monthly limit updated to {monthly_limit:,} events", "msg_type": "success"})
    return RedirectResponse(url=f"/api/v1/admin/clients?{query}", status_code=303)

@router.post("/admin/client/{client_id}/plan", include_in_schema=False)
async def update_client_plan(
    client_id: int,
    request: Request,
    username: str = Depends(verify_admin),
    csrf_token: str = Form(...),
    action: str = Form(...),
    plan_tier: str = Form("growth"),
    monthly_limit: int = Form(-1),
    billing_status: str = Form("paid"),
    db: AsyncSession = Depends(get_db),
):
    verify_admin_csrf_token(csrf_token, username)

    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        return admin_clients_redirect("Client not found", "error")

    action = action.strip().lower()
    try:
        if action == "start_trial":
            await require_trial_available(db, domain=client.domain, pixel_id=client.pixel_id, exclude_client_id=client.id)
            start_growth_trial(client)
            await record_trial_identity(db, client, source="admin")
            msg = f"Growth trial started for {client.name}."
            audit_action = "client.plan_trial_started"
        elif action == "confirm":
            clean_tier = plan_tier.strip().lower()
            normalized_tier = normalize_plan_tier(clean_tier)
            if clean_tier != normalized_tier or normalized_tier == "free":
                return admin_clients_redirect("Confirmed plan must be Growth, Scale, or Agency.", "error")
            assign_paid_plan(
                client,
                clean_tier,
                monthly_limit if monthly_limit >= 0 else None,
                normalize_billing_status(billing_status),
            )
            msg = f"{clean_tier.title()} plan confirmed for {client.name}."
            audit_action = "client.plan_confirmed"
        elif action == "cancel":
            assign_paid_plan(client, "free")
            msg = f"{client.name} moved to Free plan."
            audit_action = "client.plan_cancelled"
        else:
            return admin_clients_redirect("Invalid plan action.", "error")
    except HTTPException as exc:
        return admin_clients_redirect(str(exc.detail), "error")

    await log_admin_action(db, request, username, audit_action, client.id, msg)
    await db.commit()

    from app.dependencies import clear_client_cache
    clear_client_cache(client.api_key)

    return admin_clients_redirect(msg)

@router.get("/admin/logs", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("10/minute")
async def admin_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    username: str = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    from sqlalchemy import func as sql_func, and_

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    success_r = await db.execute(
        select(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0)).where(
            and_(EventLog.status == "success", EventLog.created_at >= today)
        )
    )
    events_today = success_r.scalar() or 0

    fail_r = await db.execute(
        select(sql_func.coalesce(sql_func.sum(EventLog.event_count), 0)).where(
            and_(EventLog.status == "failed", EventLog.created_at >= today)
        )
    )
    failed_today = fail_r.scalar() or 0

    retry_r = await db.execute(
        select(sql_func.count(FailedEvent.id)).where(
            FailedEvent.status.in_(["pending", "retrying"])
        )
    )
    retries = retry_r.scalar() or 0

    total = events_today + failed_today

    # Paginate EventLog queries to prevent database connection starvation and resource exhaustion
    total_logs_r = await db.execute(select(sql_func.count(EventLog.id)))
    total_logs = total_logs_r.scalar() or 0
    total_pages = (total_logs + page_size - 1) // page_size if total_logs > 0 else 1

    logs_r = await db.execute(
        select(EventLog)
        .order_by(EventLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    event_logs = logs_r.scalars().all()

    clients_r = await db.execute(select(Client.id, Client.name))
    client_map = {row[0]: row[1] for row in clients_r}

    failed_r = await db.execute(
        select(FailedEvent).order_by(FailedEvent.created_at.desc()).limit(50)
    )
    failed_events = failed_r.scalars().all()

    return templates.TemplateResponse(
        request,
        "admin/logs.html",
        {
            "title": "API Logs",
            "active_page": "logs",
            "events_today": events_today,
            "failed_today": failed_today,
            "total": total,
            "retries": retries,
            "event_logs": event_logs,
            "client_map": client_map,
            "failed_events": failed_events,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_logs": total_logs,
        }
    )

@router.get("/admin/settings", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("10/minute")
async def admin_settings(
    request: Request,
    username: str = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
):
    import sys

    env_checks = {
        "ADMIN_PASSWORD": bool(os.getenv("ADMIN_PASSWORD")),
        "ENCRYPTION_KEY": bool(os.getenv("ENCRYPTION_KEY")),
        "ADMIN_API_KEY": bool(os.getenv("ADMIN_API_KEY")),
        "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
    }

    python_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    admin_user = ADMIN_USERNAME

    audit_r = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(50))
    audit_logs = audit_r.scalars().all()

    return templates.TemplateResponse(
        request,
        "admin/settings.html",
        {
            "title": "Settings",
            "active_page": "settings",
            "python_ver": python_ver,
            "admin_user": admin_user,
            "env_checks": env_checks,
            "audit_logs": audit_logs,
        }
    )
