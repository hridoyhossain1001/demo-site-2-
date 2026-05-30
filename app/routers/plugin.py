"""WordPress plugin download and update-check endpoints."""

import hashlib
import hmac
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.client import Client

router = APIRouter(tags=["Plugin"])

# Plugin version এই ফাইলে hardcoded — PLUGIN_VERSION env var দিয়ে override করা যায়।
# Update করার সময় এখানে version change করুন এবং WP plugin-এও update করুন।
PLUGIN_VERSION = "1.2.15"
PLUGIN_SOURCE_DIR = Path(__file__).resolve().parents[2] / "wordpress-plugin" / "buykori-adsync"
PLUGIN_ZIP_PATH = Path(
    os.getenv(
        "PLUGIN_ZIP_PATH",
        str(Path(__file__).resolve().parents[2] / "wordpress-plugin" / "buykori-adsync.zip"),
    )
)
PLUGIN_DOWNLOAD_URL = os.getenv("PLUGIN_DOWNLOAD_URL", "")


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
        "last_updated": "2026-05-30",
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
        "last_updated": "2026-05-30",
        "description": "Official Buykori AdSync WordPress plugin for server-side Facebook CAPI, TikTok, and GA4 tracking with one-page landing support and deferred purchase control.",
        "changelog": (
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

                zf.writestr(arc_name, content)

    buf.seek(0)
    return buf


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
    if resolved_api_key:
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
