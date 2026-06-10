import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from pythonjsonlogger import jsonlogger

from app.database import engine, Base
from app.routers.events import router as events_router
from app.routers.admin import router as admin_router
from app.routers.monitoring import router as monitoring_router
from app.routers.client_portal import router as client_portal_router
from app.routers.tracker import router as tracker_router
from app.routers.deferred_events import router as deferred_events_router
from app.routers.analytics import router as analytics_router
from app.routers.debug import router as debug_router
from app.routers.client_auth import router as client_auth_router
from app.routers.ad_accounts import router as ad_accounts_router
from app.limiter import limiter
import os
import asyncio
import secrets

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
if not ADMIN_API_KEY:
    raise RuntimeError("ADMIN_API_KEY environment variable is required.")

ENABLE_DOCS = os.getenv("ENABLE_DOCS", "").lower() in ("true", "1", "yes")
STATUS_CACHE_SECONDS = float(os.getenv("STATUS_CACHE_SECONDS", "5"))
_status_cache: tuple[float, dict] | None = None
_site_html_cache: str | None = None  # marketing site HTML — startup-তে মেমোরিতে পড়ে রাখা হবে


def _csv_env(name: str, default: str) -> list[str]:
    values = os.getenv(name, default)
    return [value.strip() for value in values.split(",") if value.strip()]


ALLOWED_HOSTS = _csv_env(
    "ALLOWED_HOSTS",
    "localhost,127.0.0.1,testserver,buykori.app,www.buykori.app,client.buykori.app,admin.buykori.app,api.buykori.app,track.buykori.app",
)


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


TRACKING_BODY_LIMIT_BYTES = _positive_int_env("TRACKING_BODY_LIMIT_BYTES", 256 * 1024)
WEBHOOK_BODY_LIMIT_BYTES = _positive_int_env("WEBHOOK_BODY_LIMIT_BYTES", 2 * 1024 * 1024)
BODY_LIMIT_PATHS = (
    ("/c", TRACKING_BODY_LIMIT_BYTES),
    ("/api/v1/events", TRACKING_BODY_LIMIT_BYTES),
    ("/api/v1/incomplete-checkouts", TRACKING_BODY_LIMIT_BYTES),
    ("/api/v1/webhook", WEBHOOK_BODY_LIMIT_BYTES),
    ("/api/webhooks", WEBHOOK_BODY_LIMIT_BYTES),
)


def _body_limit_for_path(path: str) -> int | None:
    for prefix, limit in BODY_LIMIT_PATHS:
        if path == prefix or path.startswith(prefix + "/"):
            return limit
    return None


SECURITY_HEADERS_ENABLED = os.getenv("SECURITY_HEADERS_ENABLED", "true").lower() in ("true", "1", "yes")
SECURITY_HSTS_ENABLED = os.getenv("SECURITY_HSTS_ENABLED", "true").lower() in ("true", "1", "yes")
CSP_CONNECT_SRC_EXTRA = _csv_env("CSP_CONNECT_SRC_EXTRA", "")
DEFAULT_CSP_CONNECT_SRC = [
    "'self'",
    "https://api.buykori.app",
    "https://buykori.app",
    "https://www.buykori.app",
    "https://client.buykori.app",
    "https://admin.buykori.app",
    "https://track.buykori.app",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]


def _default_content_security_policy() -> str:
    connect_src = " ".join(DEFAULT_CSP_CONNECT_SRC + CSP_CONNECT_SRC_EXTRA)
    return "; ".join([
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        "img-src 'self' data: blob: https:",
        f"connect-src {connect_src}",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
        "frame-src 'none'",
    ])


CONTENT_SECURITY_POLICY = os.getenv("CONTENT_SECURITY_POLICY") or _default_content_security_policy()
CSP_STRICT_NONCE = os.getenv("CSP_STRICT_NONCE", "false").lower() in ("1", "true", "yes")
SECURITY_HEADERS = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "referrer-policy": os.getenv("REFERRER_POLICY", "strict-origin-when-cross-origin"),
    "permissions-policy": os.getenv(
        "PERMISSIONS_POLICY",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    ),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": os.getenv("CROSS_ORIGIN_OPENER_POLICY", "same-origin"),
}
if SECURITY_HSTS_ENABLED:
    SECURITY_HEADERS["strict-transport-security"] = os.getenv(
        "STRICT_TRANSPORT_SECURITY",
        "max-age=31536000; includeSubDomains",
    )

# ─── Logging Setup (Structured JSON — systemd/Supervisor/Datadog-friendly) ────
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
)
logging.root.setLevel(logging.INFO)
logging.root.addHandler(_log_handler)
logger = logging.getLogger(__name__)


# ─── Lifespan: DB Table তৈরি হবে অ্যাপ স্টার্টে ─────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Buykori AdSync স্টার্ট হচ্ছে...")

    # ─── Database Schema ──────────────────────────────────────────────────
    # Production-এ Alembic migration ব্যবহার করুন। create_all শুধু explicit
    # dev/initial setup-এর জন্য: ENABLE_CREATE_ALL=true.
    enable_create_all = os.getenv("ENABLE_CREATE_ALL", "").lower() in ("true", "1", "yes")
    if enable_create_all:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ ডাটাবেস টেবিল তৈরি/যাচাই সফল।")
    else:
        logger.info("ℹ️  create_all স্কিপ — Alembic migration ব্যবহার করুন।")

    # মার্কেটিং সাইট HTML startup-তে মেমোরিতে লোড — async event loop block এড়াতে
    global _site_html_cache
    _site_html_path = os.path.join(os.path.dirname(__file__), "templates", "site.html")
    try:
        with open(_site_html_path, "r", encoding="utf-8") as f:
            _site_html_cache = f.read()
        logger.info("✅ Marketing site HTML লোড সফল।")
    except FileNotFoundError:
        logger.warning("⚠️  site.html পাওয়া যায়নি — marketing home দেখা যাবে না।")

    # ─── Background Task Management ────────────────────────────────────
    # Store references so tasks aren't garbage collected and add error callbacks
    _background_tasks: set[asyncio.Task] = set()

    def _task_done_callback(task: asyncio.Task) -> None:
        _background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.critical(f"🔥 Background task {task.get_name()} died: {exc!r}")

    def _launch(coro, *, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        _background_tasks.add(task)
        task.add_done_callback(_task_done_callback)
        return task

    # 🔄 Outbox Worker — শুধুমাত্র ENABLE_OUTBOX_IN_WEB=true হলে এই process-এ চলবে।
    # Supervisor-এ আলাদা worker process থাকলে এটি off রাখুন (duplicate এড়াতে)।
    if os.getenv("ENABLE_OUTBOX_IN_WEB", "").lower() in ("true", "1", "yes"):
        from app.services.event_worker import process_event_outbox_forever
        _launch(process_event_outbox_forever(), name="outbox-worker")
        logger.info("Outbox worker started in Web Process.")
    else:
        logger.info("Outbox worker disabled in Web Process (ENABLE_OUTBOX_IN_WEB not set).")

    if os.getenv("ENABLE_RETRY_IN_WEB", "").lower() in ("true", "1", "yes"):
        from app.services.retry_service import retry_failed_events
        _launch(retry_failed_events(), name="retry-worker")
        logger.info("⚙️  Background Retry Service স্টার্ট হয়েছে (Web Process)।")
    else:
        logger.info("ℹ️  Retry Service এই process-এ নিষ্ক্রিয় (ENABLE_RETRY_IN_WEB সেট নেই)।")

    if os.getenv("ENABLE_MAINTENANCE_IN_WEB", "").lower() in ("true", "1", "yes"):
        # 🧹 Auto-Cleanup Service
        from app.services.cleanup_service import auto_cleanup_database
        _launch(auto_cleanup_database(), name="cleanup-worker")
        logger.info("🧹 Background Auto-Cleanup Service স্টার্ট হয়েছে (Web Process)।")

        # ⏰ Pending Events Auto-Expiry Service
        from app.services.expiry_service import expire_old_pending_events
        _launch(expire_old_pending_events(), name="expiry-worker")
        logger.info("⏰ Pending Events Expiry Service স্টার্ট হয়েছে (Web Process)।")
    else:
        logger.info("ℹ️  Maintenance loops web process-এ নিষ্ক্রিয়; worker process ব্যবহার করুন।")

    # 🔄 Ad Sync Worker
    if os.getenv("ENABLE_AD_SYNC", "").lower() in ("true", "1", "yes"):
        from app.services.ad_sync_worker import run_ad_sync_forever
        _launch(run_ad_sync_forever(), name="ad-sync-worker")
        logger.info("Ad spend sync worker started.")
    else:
        logger.info("Ad spend sync worker disabled (ENABLE_AD_SYNC not set).")

    # 🔄 WhatsApp Owner Notification Worker
    if os.getenv("ENABLE_NOTIFICATION_WORKER", "").lower() in ("true", "1", "yes"):
        from app.services.notification_worker import run_notification_worker_forever
        _launch(run_notification_worker_forever(), name="notification-worker")
        logger.info("WhatsApp owner notification worker started.")
    else:
        logger.info("WhatsApp owner notification worker disabled (ENABLE_NOTIFICATION_WORKER not set).")

    # 🌍 GeoIP Database Load
    from app.services.geoip_service import download_geoip_db_if_missing, close_geoip_db
    await download_geoip_db_if_missing()

    yield

    # Shutdown — cleanup
    # 🛑 Cancel background workers gracefully
    logger.info("🛑 Buykori AdSync বন্ধ হচ্ছে — background tasks cancel করা হচ্ছে...")
    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

    # 🔒 HTTP client বন্ধ করো
    from app.services.capi_service import close_http_client
    from app.services.redis_pool import close_redis
    await close_http_client()
    await close_redis()
    close_geoip_db()

    logger.info("🛑 Buykori AdSync বন্ধ হয়েছে।")
    await engine.dispose()


# ─── FastAPI App ───────────────────────────────────────────────────────────
app = FastAPI(
    title="Buykori AdSync",
    description="Multi-tenant ad tracking and conversion sync platform",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

from fastapi import Request
from fastapi.responses import JSONResponse

async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded!", "error_code": "RATE_LIMIT_EXCEEDED"}
    )

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, custom_rate_limit_handler)
app.mount("/static/css", StaticFiles(directory="app/static/css"), name="static-css")
app.mount("/static/js", StaticFiles(directory="app/static/js"), name="static-js")
app.mount(
    "/static/client-portal/assets",
    StaticFiles(directory="app/static/client-portal/assets"),
    name="client-portal-assets",
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=ALLOWED_HOSTS,
)


class RequestSizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = _body_limit_for_path(scope.get("path", ""))
        if not limit:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length:
            from starlette.responses import PlainTextResponse
            try:
                body_size = int(content_length.decode("latin1"))
            except ValueError:
                response = PlainTextResponse("Invalid Content-Length", status_code=400)
                await response(scope, receive, send)
                return
            if body_size > limit:
                response = PlainTextResponse("Request body too large", status_code=413)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


app.add_middleware(RequestSizeLimitMiddleware)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not SECURITY_HEADERS_ENABLED:
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16) if CSP_STRICT_NONCE and not os.getenv("CONTENT_SECURITY_POLICY") else ""
        if nonce:
            scope.setdefault("state", {})
            scope["state"]["csp_nonce"] = nonce

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in response_headers}
                for name, value in SECURITY_HEADERS.items():
                    if name == "content-security-policy" and nonce:
                        value = value.replace(
                            "script-src 'self' 'unsafe-inline'",
                            f"script-src 'self' 'nonce-{nonce}'",
                        )
                    header_name = name.encode("latin1")
                    if header_name not in existing:
                        response_headers.append((header_name, value.encode("latin1")))
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


app.add_middleware(SecurityHeadersMiddleware)

# ─── Domain Redirect Middleware ───────────────────────────────────────────────
# buykori.app / www.buykori.app এ /client রিকোয়েস্ট হলে client.buykori.app-এ redirect করো
class DomainRedirectMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/client"):
            headers = dict(scope.get("headers") or [])
            host = headers.get(b"host", b"").decode("latin1").split(":", 1)[0].lower()
            if host in {"buykori.app", "www.buykori.app"}:
                response = RedirectResponse(url="https://client.buykori.app", status_code=308)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

app.add_middleware(DomainRedirectMiddleware)

# ─── CORS ─────────────────────────────────────────────────────────────────────
#
# দুটো আলাদা CORS নীতি প্রয়োজন:
#   1. Client Portal / Admin API  → শুধু নির্দিষ্ট buykori.app ডোমেইন, credentials সহ
#   2. Public Tracker (t.js, /c)  → যেকোনো HTTPS ডোমেইন, কিন্তু credentials ছাড়া
#
# FastAPI-তে দুটো CORSMiddleware যোগ করা যায় না (পরেরটা আগেরটা override করে)।
# তাই একটি মিডলওয়্যারে route-level logic দিয়ে split করা হয়েছে।

PORTAL_ALLOWED_ORIGINS = {
    "https://buykori.app",
    "https://www.buykori.app",
    "https://client.buykori.app",
    "https://admin.buykori.app",
    "https://api.buykori.app",
    "https://track.buykori.app",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
}

# Tracker endpoints — এগুলোতে credentials পাঠানো হয় না
TRACKER_PATHS = {"/t.js", "/c", "/pixel"}


def _is_tracker_path(path: str) -> bool:
    return path in TRACKER_PATHS or any(path.startswith(prefix + "/") for prefix in TRACKER_PATHS)


class SplitCORSMiddleware:
    """
    Tracker endpoints-এ open CORS (credentials=False),
    Portal/Admin/API endpoints-এ strict CORS (credentials=True, allowlist only).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin", b"").decode("latin1")
        path = scope.get("path", "")

        if not origin:
            await self.app(scope, receive, send)
            return

        is_tracker = _is_tracker_path(path)

        if is_tracker:
            # Tracker: যেকোনো HTTPS অরিজিন allow, credentials নেই
            if origin.startswith("https://") or origin.startswith("http://localhost") or origin.startswith("http://127."):
                allow_origin = origin
                allow_credentials = "false"
            else:
                await self.app(scope, receive, send)
                return
        else:
            # Portal/API: শুধু whitelist করা ডোমেইন allow
            if origin in PORTAL_ALLOWED_ORIGINS:
                allow_origin = origin
                allow_credentials = "true"
            else:
                await self.app(scope, receive, send)
                return

        # Handle preflight
        method = headers.get(b":method", b"").decode("latin1") or scope.get("method", "")
        if method.upper() == "OPTIONS":
            from starlette.responses import Response
            preflight = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": allow_origin,
                    "Access-Control-Allow-Credentials": allow_credentials,
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "X-API-Key, X-Admin-API-Key, X-Admin-CSRF-Token, X-CAPI-Origin, X-CAPI-Timestamp, X-CAPI-Signature, Content-Type, Authorization",
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )
            await preflight(scope, receive, send)
            return

        # Inject CORS headers into actual response
        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append((b"access-control-allow-origin", allow_origin.encode()))
                response_headers.append((b"access-control-allow-credentials", allow_credentials.encode()))
                response_headers.append((b"vary", b"Origin"))
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)


app.add_middleware(SplitCORSMiddleware)

# ─── Routers ─────────────────────────────────────────────────────────────────
app.include_router(events_router, prefix="/api/v1", tags=["Events"])
app.include_router(admin_router,  prefix="/api/v1", tags=["Admin"])
app.include_router(monitoring_router, prefix="/api/v1", tags=["Monitoring"])
app.include_router(client_portal_router, tags=["Client Portal"])
app.include_router(tracker_router, tags=["Tracker"])  # /t.js, /c — root level, no prefix
app.include_router(deferred_events_router, prefix="/api/v1", tags=["Deferred Events"])
app.include_router(analytics_router, prefix="/api/v1", tags=["Analytics"])
app.include_router(ad_accounts_router, prefix="/api/v1", tags=["Ad Accounts"])
# Debug endpoints শুধু ENABLE_DEBUG=true হলে expose হবে — প্রোডাকশনে false রাখুন
if os.getenv("ENABLE_DEBUG", "").lower() in ("true", "1", "yes"):
    app.include_router(debug_router, prefix="/api/v1", tags=["Debug & Testing"])
    logger.warning("⚠️  Debug endpoints সক্রিয় — প্রোডাকশনে ENABLE_DEBUG=false রাখুন!")
app.include_router(client_auth_router, prefix="/api/v1", tags=["Client Auth"])

from app.routers.client_api import router as client_api_router
app.include_router(client_api_router, prefix="/api", tags=["Client Portal JSON API"])

from app.routers.courier_api import router as courier_api_router
app.include_router(courier_api_router, prefix="/api", tags=["Courier Management API"])

from app.routers.plugin import router as plugin_router
app.include_router(plugin_router, prefix="/api/v1", tags=["Plugin"])

from app.routers.webhook import router as webhook_router
app.include_router(webhook_router, prefix="/api/v1", tags=["Webhook"])

from app.routers.courier_webhook import router as courier_webhook_router
app.include_router(courier_webhook_router, prefix="/api", tags=["Courier Webhook API"])

from app.routers.client_health import router as client_health_router
app.include_router(client_health_router, prefix="/api/v1", tags=["Client Health"])

from app.routers.incomplete_checkouts import router as incomplete_checkouts_router
app.include_router(incomplete_checkouts_router, prefix="/api/v1", tags=["Incomplete Checkout Recovery"])


# ─── Health Check ─────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def marketing_home():
    if _site_html_cache:
        return HTMLResponse(_site_html_cache)
    # Fallback: startup-তে লোড না হলে একবার পড়ে দিন
    site_path = os.path.join(os.path.dirname(__file__), "templates", "site.html")
    try:
        with open(site_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Buykori AdSync</h1>", status_code=200)


@app.get("/status", tags=["Health"])
async def health_check():
    """Real health check — DB ও Redis connectivity যাচাই করে।"""
    from sqlalchemy import text
    from app.services.redis_pool import get_redis
    global _status_cache

    now = time.monotonic()
    if _status_cache and now - _status_cache[0] < STATUS_CACHE_SECONDS:
        return _status_cache[1]

    db_ok = False
    redis_ok = False

    # DB check
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error(f"Health check — DB error: {e}")

    # Redis check
    try:
        redis = get_redis()
        if redis:
            await redis.ping()
            redis_ok = True
    except Exception as e:
        logger.error(f"Health check — Redis error: {e}")

    overall = "ok" if (db_ok and redis_ok) else "degraded"
    payload = {
        "status": overall,
        "service": "Buykori AdSync",
        "version": "1.1.0",
        "db": db_ok,
        "redis": redis_ok,
        "message": "🔥 Buykori AdSync চলছে!" if overall == "ok" else "⚠️ সার্ভিস degraded!",
    }
    _status_cache = (now, payload)
    return payload
