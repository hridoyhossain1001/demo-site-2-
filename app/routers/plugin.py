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
PLUGIN_VERSION = "1.2.6"
PLUGIN_SOURCE_DIR = Path(__file__).resolve().parents[2] / "wordpress-plugin" / "buykori-adsync"
PLUGIN_ZIP_PATH = Path(
    os.getenv(
        "PLUGIN_ZIP_PATH",
        str(Path(__file__).resolve().parents[2] / "wordpress-plugin" / "buykori-adsync.zip"),
    )
)
PLUGIN_DOWNLOAD_URL = os.getenv("PLUGIN_DOWNLOAD_URL", "")


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
    package_available = PLUGIN_ZIP_PATH.is_file()
    if package_available:
        package_bytes = PLUGIN_ZIP_PATH.read_bytes()
        package_sha256 = hashlib.sha256(package_bytes).hexdigest()
        package_size = len(package_bytes)

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
        "last_updated": "2026-05-29",
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
    if PLUGIN_ZIP_PATH.is_file():
        package_sha256 = hashlib.sha256(PLUGIN_ZIP_PATH.read_bytes()).hexdigest()

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
        "last_updated": "2026-05-29",
        "description": "Official Buykori AdSync WordPress plugin for server-side Facebook CAPI, TikTok, and GA4 tracking with one-page landing support and deferred purchase control.",
        "changelog": (
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
    ডাইনামিকালি প্রিকনফিগার্ড প্লাগইন জিপ তৈরি করে।
    ক্লায়েন্টের API Key এবং Gateway URL আগে থেকেই embed করে দেয়
    যাতে ইনস্টল করার পর কোনো ম্যানুয়াল কনফিগারেশন দরকার না হয়।
    """
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
                    # Replace default api_key in activation defaults
                    text = re.sub(
                        r"('api_key'\s*=>\s*)'',",
                        rf"\g<1>'{api_key}',",
                        text,
                        count=1,
                    )
                    # Replace default gateway_url in activation defaults
                    text = re.sub(
                        r"('gateway_url'\s*=>\s*)BUYKORIGW_DEFAULT_GATEWAY_URL,",
                        rf"\g<1>'{gateway_url}',",
                        text,
                        count=1,
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
    db: AsyncSession = Depends(get_db),
):
    """
    প্লাগইন ডাউনলোড এন্ডপয়েন্ট।

    api_key query param দিলে ডাইনামিকালি প্রিকনফিগার্ড জিপ তৈরি করে সার্ভ করবে।
    না দিলে স্ট্যান্ডার্ড জিপ ফাইলটি সার্ভ করবে (auto-updater compatibility)।
    """
    resolved_api_key = api_key

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
            select(Client).where(Client.api_key == resolved_api_key, Client.is_active == True)
        )
        client = result.scalar_one_or_none()

        if client and PLUGIN_SOURCE_DIR.is_dir():
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
