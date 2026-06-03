"""WordPress plugin download and update-check endpoints."""

import hashlib
import hmac
import io
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.plugin_connect_session import PluginConnectSession
from app.services.plan_service import (
    cancel_to_free,
    find_trial_identity_conflict,
    record_trial_identity,
    trial_active,
)
from app.services.site_binding_service import get_active_site_binding, upsert_active_site_binding
from app.utils.plugin_package import plugin_protection_enabled, protect_plugin_package_content
from app.utils.plugin_connect import (
    gateway_url_from_request,
    normalize_site_url,
    pkce_challenge,
    sha256_hex,
    validate_token,
)

router = APIRouter(tags=["Plugin"])


class PluginConnectExchangeRequest(BaseModel):
    code: str
    codeVerifier: str
    state: str
    siteUrl: str
    installationId: str | None = None


class PluginDisconnectRequest(BaseModel):
    siteUrl: str
    installationId: str | None = None

# Plugin version এই ফাইলে hardcoded — PLUGIN_VERSION env var দিয়ে override করা যায়।
# Update করার সময় এখানে version change করুন এবং WP plugin-এও update করুন।
PLUGIN_VERSION = "1.2.32"
PLUGIN_SOURCE_DIR = Path(__file__).resolve().parents[2] / "wordpress-plugin" / "buykori-adsync"
PLUGIN_ZIP_PATH = Path(
    os.getenv(
        "PLUGIN_ZIP_PATH",
        str(Path(__file__).resolve().parents[2] / "wordpress-plugin" / "buykori-adsync.zip"),
    )
)
PLUGIN_DOWNLOAD_URL = os.getenv("PLUGIN_DOWNLOAD_URL", "")
PLUGIN_PROTECTED_PACKAGE = plugin_protection_enabled()
PLUGIN_PRECONFIGURED_DOWNLOADS = os.getenv("PLUGIN_PRECONFIGURED_DOWNLOADS", "false").lower() in {"1", "true", "yes"}


# Cache for plugin zip SHA256 and size to prevent high Disk I/O and CPU overhead
_PLUGIN_ZIP_CACHE = {
    "sha256": None,
    "size": None,
    "mtime": None,
}

_PRECONFIGURED_ZIP_CACHE = {}


def _get_cached_zip_info():
    if not PLUGIN_ZIP_PATH.is_file():
        return "", 0, False

    try:
        stat = PLUGIN_ZIP_PATH.stat()
        mtime = stat.st_mtime
    except Exception:
        return "", 0, False

    if _PLUGIN_ZIP_CACHE["mtime"] == mtime and _PLUGIN_ZIP_CACHE["sha256"] is not None:
        return _PLUGIN_ZIP_CACHE["sha256"], _PLUGIN_ZIP_CACHE["size"], True

    try:
        package_bytes = PLUGIN_ZIP_PATH.read_bytes()
        sha256 = hashlib.sha256(package_bytes).hexdigest()
        _PLUGIN_ZIP_CACHE["sha256"] = sha256
        _PLUGIN_ZIP_CACHE["size"] = len(package_bytes)
        _PLUGIN_ZIP_CACHE["mtime"] = mtime
        return sha256, _PLUGIN_ZIP_CACHE["size"], True
    except Exception:
        return "", 0, False


@router.get(
    "/plugin/info",
    summary="Get plugin release status",
    description="Return public WordPress plugin release metadata for client portal setup/status screens.",
)
async def plugin_info(request: Request):
    """Return public plugin release metadata without per-client signing."""
    download_url = PLUGIN_DOWNLOAD_URL or _plugin_download_url(request)
    package_sha256 = ""
    package_size = 0
    package_sha256, package_size, package_available = _get_cached_zip_info()

    return JSONResponse(content={
        "version": PLUGIN_VERSION,
        "download_url": download_url,
        "package_sha256": package_sha256,
        "package_size": package_size,
        "package_available": package_available,
        "homepage": "https://buykori.app/",
        "requires": "5.8",
        "tested": "6.7",
        "requires_php": "7.4",
        "last_updated": "2026-06-03",
    })


@router.get(
    "/plugin/update-check",
    summary="Check for plugin updates",
    description="Return WordPress plugin update metadata for the built-in auto-updater.",
)
async def plugin_update_check(
    request: Request,
    x_api_key: str = Header("", alias="X-API-Key"),
):
    """Return current plugin version info for WordPress auto-updater."""
    download_url = PLUGIN_DOWNLOAD_URL or _plugin_download_url(request)
    package_sha256 = ""
    package_sha256, _, _ = _get_cached_zip_info()

    signature = ""
    if x_api_key and package_sha256:
        payload = f"{PLUGIN_VERSION}|{download_url}|{package_sha256}"
        signature = hmac.new(
            x_api_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    return _plugin_update_response(download_url, package_sha256, signature)


def _plugin_download_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}/api/v1/plugin/download"


def _plugin_update_response(download_url: str, package_sha256: str, signature: str) -> JSONResponse:
    return JSONResponse(content={
        "version": PLUGIN_VERSION,
        "download_url": download_url,
        "package_sha256": package_sha256,
        "signature": signature,
        "homepage": "https://buykori.app/",
        "requires": "5.8",
        "tested": "6.7",
        "requires_php": "7.4",
        "last_updated": "2026-06-03",
        "description": "Official Buykori AdSync WordPress plugin for server-side Facebook CAPI, TikTok, and GA4 tracking with one-page landing support and deferred purchase control.",
        "changelog": (
            "<h4>v1.2.32</h4><ul>"
            "<li>Simplified the WordPress settings UI for client-facing setup</li>"
            "<li>Added a compact connection status summary with account, website, and tracking state</li>"
            "<li>Collapsed optional browser pixel backup and diagnostics behind support-oriented details</li>"
            "</ul>"
            "<h4>v1.2.31</h4><ul>"
            "<li>Added WordPress installation fingerprinting for connected-site validation</li>"
            "<li>Added server-side active binding checks during event ingestion to block copied API key/plugin misuse</li>"
            "<li>Added admin API tools to list, release, and transfer site bindings with audit logs</li>"
            "<li>Added per-site event throttling when Redis is available</li>"
            "</ul>"
            "<h4>v1.2.30</h4><ul>"
            "<li>Added an active website binding lock so the same root domain or subdomain cannot be connected to multiple Buykori workspaces at the same time</li>"
            "<li>Blocks second-account plugin connection attempts with a transfer/support message</li>"
            "<li>Keeps the trial reuse downgrade guard for sites that already used a Growth trial</li>"
            "</ul>"
            "<h4>v1.2.29</h4><ul>"
            "<li>Added a clear plugin warning when a reconnected site has already used a Growth trial and the account is moved to Free</li>"
            "<li>Improved reconnect safeguards for root domains and subdomains to reduce trial reuse abuse</li>"
            "<li>Continued simplifying the WordPress settings experience for client-facing setup</li>"
            "</ul>"
            "<h4>v1.2.28</h4><ul>"
            "<li>Simplified the WordPress settings screen by hiding low-resource mode, landing mode, and variation toggles from the main UI</li>"
            "<li>Added a disconnect action for account-connected sites</li>"
            "<li>Made smart landing-page detection and variation tracking automatic by default</li>"
            "<li>Moved catalog matching into Advanced controls for support-led troubleshooting</li>"
            "</ul>"
            "<h4>v1.2.27</h4><ul>"
            "<li>Prevented AddPaymentInfo from firing on checkout page load when WooCommerce preselects a default payment method</li>"
            "<li>Kept AddPaymentInfo tied to trusted customer payment-method interaction with browser-side deduplication</li>"
            "</ul>"
            "<h4>v1.2.26</h4><ul>"
            "<li>Queued WooCommerce Purchase relay through Action Scheduler so checkout responses are not delayed by gateway calls</li>"
            "<li>Dispatched the server-side InitiateCheckout fallback without blocking checkout</li>"
            "<li>Dispatched incomplete-checkout recovery conversion without blocking order creation</li>"
            "</ul>"
            "<h4>v1.2.25</h4><ul>"
            "<li>Fixed incomplete checkout draft capture when no UTM campaign parameters are present</li>"
            "</ul>"
            "<h4>v1.2.24</h4><ul>"
            "<li>Added incomplete checkout recovery capture after a valid Bangladesh phone number is entered</li>"
            "<li>Automatically marks the matching recovery draft as recovered when WooCommerce creates the order</li>"
            "</ul>"
            "<h4>v1.2.23</h4><ul>"
            "<li>Changed COD landing InitiateCheckout intent to valid phone input or Place Order click instead of email-only activity</li>"
            "</ul>"
            "<h4>v1.2.22</h4><ul>"
            "<li>Recognized CartFlows and embedded checkout widgets as one-page landing contexts so ViewContent can fire for the loaded product</li>"
            "<li>Generated a fresh InitiateCheckout event ID for each new customer intent while preserving the latest ID for order fallback deduplication</li>"
            "</ul>"
            "<h4>v1.2.21</h4><ul>"
            "<li>Prevented automatic landing-page cart hydration from firing AddToCart conversion events</li>"
            "<li>Restricted InitiateCheckout to trusted customer input, clicks, and submits instead of synthetic checkout refresh events</li>"
            "</ul>"
            "<h4>v1.2.20</h4><ul>"
            "<li>Published a Linux-safe plugin ZIP with forward-slash archive paths</li>"
            "<li>Removed the redundant post-install folder move that could break activation after an update</li>"
            "</ul>"
            "<h4>v1.2.19</h4><ul>"
            "<li>Added server-side WooCommerce AddToCart CAPI tracking with a session receipt queue</li>"
            "<li>Added browser Pixel receipt synchronization for classic AJAX, WooCommerce Blocks, and redirect add-to-cart flows</li>"
            "<li>Added shared AddToCart event IDs for Pixel and CAPI deduplication</li>"
            "<li>Tightened one-page ViewContent visibility and InitiateCheckout field intent rules</li>"
            "</ul>"
            "<h4>v1.2.18</h4><ul>"
            "<li>Added smart auto-detection for native WooCommerce, embedded checkout, Elementor, and CartFlows landing pages</li>"
            "<li>Restored PageView tracking across checkout and thank-you pages</li>"
            "<li>Added cached-page REST retry handling and WooCommerce Blocks cart reconciliation</li>"
            "</ul>"
            "<h4>v1.2.17</h4><ul>"
            "<li>Rebuilt and republished the plugin package so stores on 1.2.16 can update cleanly</li>"
            "</ul>"
            "<h4>v1.2.16</h4><ul>"
            "<li>Plugin package refresh with current WordPress admin and tracking files</li>"
            "</ul>"
            "<h4>v1.2.15</h4><ul>"
            "<li>Rebuilt and republished the plugin package so WordPress update checks detect the latest release</li>"
            "</ul>"
            "<h4>v1.2.14</h4><ul>"
            "<li>Reduced noisy PageView tracking on checkout and thank-you funnel pages</li>"
            "<li>Tightened PageView deduplication with normalized page paths</li>"
            "<li>Kept fallback event_source_url aligned with the captured page_location</li>"
            "</ul>"
            "<h4>v1.2.13</h4><ul>"
            "<li>Ensured checkout-created WooCommerce orders send Purchase telemetry even when the WordPress-side COD toggle is not synced yet</li>"
            "<li>Lets the gateway-side COD Protection setting hold new orders reliably in Order Verification</li>"
            "</ul>"
            "<h4>v1.2.12</h4><ul>"
            "<li>Improved Meta event match quality with stronger fbp/fbc, GA, and visitor ID fallback handling</li>"
            "<li>Normalized event contents payloads across REST and AJAX fallback tracking paths</li>"
            "<li>Added richer browser and server event body fields for AddToCart, InitiateCheckout, and cart-style events</li>"
            "</ul>"
            "<h4>v1.2.11</h4><ul>"
            "<li>Improved frontend event-quality payload normalization and matching data capture</li>"
            "</ul>"
            "<h4>v1.2.10</h4><ul>"
            "<li>Prevented CartFlows thank-you pages from firing a second empty InitiateCheckout event</li>"
            "</ul>"
            "<h4>v1.2.9</h4><ul>"
            "<li>Added cache-busted update checks and versioned update transients so WordPress can find newly published plugin releases faster</li>"
            "</ul>"
            "<h4>v1.2.8</h4><ul>"
            "<li>Shortened InitiateCheckout marker cookies to 20 minutes and clears them after Purchase, thank-you pages, and new AddToCart actions</li>"
            "<li>Uses product data as checkout payload fallback on one-page funnels when cart data is not available yet</li>"
            "<li>Treats product fallback data as checkout-ready so checkout button clicks can fire InitiateCheckout</li>"
            "</ul>"
            "<h4>v1.2.7</h4><ul>"
            "<li>Hardened InitiateCheckout fallback so order-created telemetry is sent even when the browser marker exists but the browser request was interrupted by redirect</li>"
            "<li>Added checkout button intent selectors for CartFlows and custom checkout CTAs</li>"
            "</ul>"
            "<h4>v1.2.6</h4><ul>"
            "<li>Added deferred Purchase tracking on WooCommerce checkout completion hooks so COD orders enter Order Verification even when the thank-you page is skipped by CartFlows, blocks, or redirects</li>"
            "</ul>"
            "<h4>v1.2.5</h4><ul>"
            "<li>Preserved deduplication keys on secondary TikTok delivery logs so the client portal no longer shows fallback did_* keys</li>"
            "<li>Tightened order-backed InitiateCheckout fallback guard to skip when a browser marker or event ID already exists</li>"
            "<li>Rebuilt the plugin ZIP with the validated canonical packaging script</li>"
            "</ul>"
            "<h4>v1.2.4</h4><ul>"
            "<li>Tightened one-page landing mode so InitiateCheckout no longer fires from checkout surface/page-load checks</li>"
            "<li>ViewContent now waits for 50% product visibility plus a short dwell delay in one-page mode</li>"
            "<li>Added multi-product landing card ViewContent support for WooCommerce product grids and data-product blocks</li>"
            "<li>Added duplicate guards for AddToCart click/AJAX events and optional CTA intent selectors</li>"
            "</ul>"
            "<h4>v1.2.3</h4><ul>"
            "<li>Ensured TikTok Events API always receives a singular content_id for catalog matching diagnostics</li>"
            "<li>Allowed REST tracking requests to accept custom_data directly as well as event_data</li>"
            "<li>Improved content_id/content_ids normalization for checkout and cart events</li>"
            "</ul>"
            "<h4>v1.1.9</h4><ul>"
            "<li>Dynamic pre-configured plugin download — API key and gateway URL pre-filled on install</li>"
            "<li>Auto one-page landing detection — InitiateCheckout no longer fires on page load for single-page sites</li>"
            "<li>Human-interaction gate for checkout tracking — browser autofill no longer triggers false events</li>"
            "<li>Updated default gateway domain to buykori.app</li>"
            "</ul>"
            "<h4>v1.1.8</h4><ul>"
            "<li>Added one-page landing tracking mode so InitiateCheckout waits for customer intent instead of page load</li>"
            "<li>Added session duplicate guards for PageView, ViewContent, and InitiateCheckout browser events</li>"
            "<li>Normalized campaign source and campaign names to keep UTM reports cleaner</li>"
            "</ul>"
            "<h4>v1.1.7</h4><ul>"
            "<li>Updated default AdSync API URL to use the new custom domain</li>"
            "<li>Improved compatibility with gateway redirects</li>"
            "</ul>"
            "<h4>v1.1.6</h4><ul>"
            "<li>Added UTM campaign capture and persistence for attribution reporting</li>"
            "<li>Added campaign source detection for TikTok and Facebook click IDs</li>"
            "<li>Added platform delivery controls support from the gateway</li>"
            "</ul>"
            "<h4>v1.1.5</h4><ul>"
            "<li>Added a Check Update Now tool to clear plugin update cache from the settings page</li>"
            "<li>Added manual update-cache reset so admins do not need to run database queries</li>"
            "</ul>"
            "<h4>v1.1.4</h4><ul>"
            "<li>Improved TikTok event payloads with richer product contents, content IDs, and content type</li>"
            "<li>Added checkout/customer field capture for better TikTok and Facebook event matching</li>"
            "<li>Rebuilt plugin update package so WordPress can detect the latest update</li>"
            "</ul>"
            "<h4>v1.1.3</h4><ul>"
            "<li>Added customer PII fields capture (email, phone, name, address, etc.) for AJAX tracking events</li>"
            "<li>Added nested contents array support to browser events (AddToCart, ViewContent, InitiateCheckout, etc.)</li>"
            "<li>Improved TikTok payload content mapping to follow Events API specifications</li>"
            "</ul>"
            "<h4>v1.1.2</h4><ul>"
            "<li>Durable outbox-friendly tracking improvements</li>"
            "<li>TikTok _ttp and ttclid capture for standard and custom events</li>"
            "<li>Lightweight AJAX rate limiting for frontend tracking</li>"
            "<li>Improved checkout/cart payloads and custom event stability</li>"
            "</ul>"
            "<h4>v1.1.0</h4><ul>"
            "<li>Purchase event blocking request response verification</li>"
            "<li>Phone hash normalization fix</li>"
            "<li>WooCommerce webhook HMAC signature verification</li>"
            "<li>Atomic rate limiting and production database safety</li>"
            "</ul>"
            "<h4>v1.0.0</h4><ul>"
            "<li>Initial release</li>"
            "<li>PageView, ViewContent, AddToCart, InitiateCheckout, Purchase tracking</li>"
            "<li>Deferred Purchase with auto-confirm</li>"
            "<li>Action Scheduler retry queue</li>"
            "</ul>"
        ),
    })


def _build_gateway_url(request: Request) -> str:
    """Determine the canonical gateway API URL for plugin pre-configuration."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}/api/v1"


def _generate_preconfigured_zip(api_key: str, gateway_url: str) -> io.BytesIO:
    """
    ডাইনামিকালি প্রিকনফিগার্ড জিপ তৈরি করে।
    ক্লায়েন্টের API Key এবং Gateway URL আগে থেকেই embed করে দেয়
    যাতে ইনস্টল করার পর কোনো ম্যানুয়াল কনফিগারেশন দরকার না হয়।
    """
    cache_key = (api_key, gateway_url)
    if cache_key in _PRECONFIGURED_ZIP_CACHE:
        return io.BytesIO(_PRECONFIGURED_ZIP_CACHE[cache_key])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(PLUGIN_SOURCE_DIR):
            for fname in files:
                file_path = Path(root) / fname
                rel_path = file_path.relative_to(PLUGIN_SOURCE_DIR.parent)
                # Use forward slashes for cross-platform compatibility
                arc_name = str(rel_path).replace(os.sep, "/")

                content = file_path.read_bytes()

                # Patch buykori-adsync.php defaults with client's credentials
                if fname == "buykori-adsync.php":
                    text = content.decode("utf-8")
                    # Replace default api_key in activation defaults and other blocks
                    text = re.sub(
                        r"(['\"]api_key['\"]\s*=>\s*)(['\"]{2}|['\"].*?['\"])\s*,",
                        lambda m: f"{m.group(1)}'{api_key}',",
                        text,
                    )
                    # Replace default gateway_url in activation defaults and other blocks
                    text = re.sub(
                        r"(['\"]gateway_url['\"]\s*=>\s*)(BUYKORIGW_DEFAULT_GATEWAY_URL|['\"].*?['\"])\s*,",
                        lambda m: f"{m.group(1)}'{gateway_url}',",
                        text,
                    )
                    content = text.encode("utf-8")

                content = protect_plugin_package_content(
                    arc_name,
                    content,
                    enabled=PLUGIN_PROTECTED_PACKAGE,
                )
                zf.writestr(arc_name, content)

    payload = buf.getvalue()
    if len(_PRECONFIGURED_ZIP_CACHE) >= 128:
        _PRECONFIGURED_ZIP_CACHE.pop(next(iter(_PRECONFIGURED_ZIP_CACHE)))
    _PRECONFIGURED_ZIP_CACHE[cache_key] = payload
    return io.BytesIO(payload)


@router.post(
    "/plugin/connect/exchange",
    summary="Exchange a one-time plugin connect code for WordPress configuration",
    include_in_schema=False,
)
async def plugin_connect_exchange(
    request: Request,
    payload: PluginConnectExchangeRequest,
    db: AsyncSession = Depends(get_db),
):
    code = validate_token(payload.code, "code")
    code_verifier = validate_token(payload.codeVerifier, "code verifier")
    state = validate_token(payload.state, "state")
    _, site_host = normalize_site_url(payload.siteUrl)

    result = await db.execute(
        select(PluginConnectSession).where(PluginConnectSession.code_hash == sha256_hex(code))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid connect code.")
    if session.used_at is not None:
        raise HTTPException(status_code=409, detail="Connect code has already been used.")

    now = datetime.now(timezone.utc)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise HTTPException(status_code=410, detail="Connect code has expired.")
    if not hmac.compare_digest(session.state, state):
        raise HTTPException(status_code=403, detail="Invalid connect state.")
    if session.site_host != site_host:
        raise HTTPException(status_code=403, detail="Connect site mismatch.")
    if not hmac.compare_digest(session.code_challenge, pkce_challenge(code_verifier)):
        raise HTTPException(status_code=403, detail="Invalid code verifier.")

    client_r = await db.execute(select(Client).where(Client.id == session.client_id))
    client = client_r.scalar_one_or_none()
    if not client or not client.is_active:
        raise HTTPException(status_code=403, detail="Client account is inactive.")

    plan_warning = ""
    if trial_active(client, now):
        conflict = await find_trial_identity_conflict(
            db,
            domain=session.site_host,
            exclude_client_id=client.id,
        )
        if conflict:
            cancel_to_free(client, now)
            plan_warning = (
                "This website has already used a Growth trial. "
                "Your account was moved to the Free plan. Contact Buykori support to upgrade."
            )
            db.add(AuditLog(
                actor="system",
                action="plugin_connect_trial_reuse_downgraded",
                client_id=client.id,
                ip_address=request.client.host if request.client else None,
                details=f"site={session.site_host}; conflict_id={conflict.id}",
            ))
        else:
            await record_trial_identity(db, client, source="plugin_connect")

    await upsert_active_site_binding(
        db,
        site_host=session.site_host,
        client_id=client.id,
        installation_id=(payload.installationId or "").strip()[:128] or None,
        source="plugin_connect",
        now=now,
    )
    session.used_at = now
    client.updated_at = now
    db.add(AuditLog(
        actor="wordpress_plugin",
        action="plugin_connect_exchanged",
        client_id=client.id,
        ip_address=request.client.host if request.client else None,
        details=f"site={session.site_host}",
    ))
    await db.commit()

    return {
        "success": True,
        "client_name": client.name,
        "site_host": session.site_host,
        "api_key": client.api_key,
        "public_key": client.public_key or "",
        "gateway_url": gateway_url_from_request(request),
        "plan_warning": plan_warning,
        "installation_id": (payload.installationId or "").strip()[:128],
    }


@router.post(
    "/plugin/connect/disconnect",
    summary="Record a WordPress-side disconnect notification",
    include_in_schema=False,
)
async def plugin_connect_disconnect(
    request: Request,
    payload: PluginDisconnectRequest,
    x_api_key: str = Header("", alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required.")
    _, site_host = normalize_site_url(payload.siteUrl)
    result = await db.execute(select(Client).where(Client.api_key == x_api_key, Client.is_active == True))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=401, detail="Invalid API key.")

    now = datetime.now(timezone.utc)
    binding = await get_active_site_binding(db, site_host)
    if binding and int(binding.client_id) == int(client.id):
        binding.last_seen_at = now
        if payload.installationId and not binding.installation_id:
            binding.installation_id = payload.installationId.strip()[:128]

    db.add(AuditLog(
        actor="wordpress_plugin",
        action="plugin_disconnect_notified",
        client_id=client.id,
        ip_address=request.client.host if request.client else None,
        details=f"site={site_host}; binding_kept_active=true",
    ))
    await db.commit()
    return {"success": True, "binding_kept_active": True}


@router.get(
    "/plugin/download",
    summary="Download WordPress plugin ZIP",
    include_in_schema=False,
)
async def plugin_download(
    request: Request,
    api_key: Optional[str] = Query(None, alias="api_key"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    """
    প্লাগইন ডাউনলোড এন্ডপয়েন্ট।

    api_key query param বা X-API-Key header দিলে ডাইনামিকালি প্রিকনফিগার্ড জিপ তৈরি করে সার্ভ করবে।
    না দিলে স্ট্যান্ডার্ড জিপ ফাইলটি সার্ভ করবে (auto-updater compatibility)।
    """
    resolved_api_key = api_key or x_api_key

    # If api_key query parameter is not present, check client session cookie
    if not resolved_api_key:
        try:
            from app.routers.client_portal import get_client_from_portal_session
            client = await get_client_from_portal_session(request, db)
            if client and client.is_active:
                resolved_api_key = client.api_key
        except Exception:
            pass

    # ── Dynamic pre-configured download ─────────────────────────────────
    if PLUGIN_PRECONFIGURED_DOWNLOADS and resolved_api_key:
        result = await db.execute(
            select(Client).where(Client.api_key == resolved_api_key)
        )
        client = result.scalar_one_or_none()

        if not client:
            raise HTTPException(status_code=401, detail="Invalid API key")
        if not client.is_active:
            raise HTTPException(status_code=403, detail="Inactive client account")

        if PLUGIN_SOURCE_DIR.is_dir():
            gateway_url = _build_gateway_url(request)
            buf = _generate_preconfigured_zip(resolved_api_key, gateway_url)
            return StreamingResponse(
                buf,
                media_type="application/zip",
                headers={
                    "Content-Disposition": "attachment; filename=buykori-adsync.zip",
                },
            )

    # ── Static download (fallback / auto-updater) ───────────────────────
    if not PLUGIN_ZIP_PATH.is_file():
        raise HTTPException(status_code=404, detail="Plugin ZIP not found")

    return FileResponse(
        path=PLUGIN_ZIP_PATH,
        media_type="application/zip",
        filename="buykori-adsync.zip",
    )
