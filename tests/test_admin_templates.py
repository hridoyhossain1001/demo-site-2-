import os
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from fastapi.testclient import TestClient

# Set up environment variables required by the app before importing it
os.environ["ADMIN_PASSWORD"] = "pbkdf2_sha256$210000$dGVzdC1hZG1pbi1zYWx0LTE=$9gwSQUsI_uzxaNpdvx_cOcpF4opgO7Ma_Hcmq3z4kSU="
os.environ["ADMIN_API_KEY"] = "test-admin-api-key"
os.environ["ADMIN_JWT_SECRET"] = "test-admin-jwt-secret"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "ZFhnf1szwemka8kBbH9jPTC7oKBRTEv0EqWt1J8AD0M="

from app.main import app
from app.database import get_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models.client import Client
from app.models.client_session import ClientSession
from app.models.client_user import ClientUser
from app.models.courier_booking_job import CourierBookingJob
from app.models.courier_order import CourierOrder
from app.models.pending_event import PendingEvent
from app.security import encrypt_token
from app.services.auth_service import hash_password, hash_session_token
from app.utils.display import mask_secret
from app.utils.plugin_connect import pkce_challenge

# Setup clean async database engine for testing
engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestingSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="module", autouse=True)
async def cleanup_database_engine():
    yield
    await engine.dispose()


# ─── ADMIN PORTAL TESTS ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_admin_dashboard_render():
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Dashboard Store",
            api_key="dashboard-api-key",
            portal_key="dashboard-portal-key",
            pixel_id="99887766",
            access_token=encrypt_token("fb-token"),
            domain="dashboard.example.com",
            is_active=True,
        )
        session.add(test_client)
        await session.commit()
        await session.refresh(test_client)
        client_id = test_client.id

    client = TestClient(app)
    response = client.get(
        "/api/v1/admin",
        auth=("admin", "test-admin-password")
    )
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Buykori" in response.text
    assert "Client Management" not in response.text
    assert "style=" not in response.text
    assert '<script src="/static/js/admin.js" defer></script>' in response.text
    assert f'href="/api/v1/admin/client/{client_id}/edit"' in response.text
    assert "onclick=" not in response.text
    assert "function copyText" not in response.text
    dashboard_template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "dashboard.html").read_text(encoding="utf-8")
    assert "style=" not in dashboard_template


@pytest.mark.anyio
async def test_admin_clients_render():
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Client List Store",
            api_key="client-list-api-key",
            portal_key="client-list-portal-key",
            pixel_id="11223344",
            access_token=encrypt_token("fb-token"),
            is_active=True,
        )
        session.add(test_client)
        await session.commit()

    client = TestClient(app)
    response = client.get(
        "/api/v1/admin/clients",
        auth=("admin", "test-admin-password")
    )
    assert response.status_code == 200
    assert "Client Management" in response.text
    assert "Total Clients" in response.text
    assert 'data-copy-target="ck_' in response.text
    assert 'data-confirm="Rotate server API key?' in response.text
    assert 'data-progress-width="' in response.text
    assert "onclick=" not in response.text
    assert "onsubmit=" not in response.text
    assert "confirm(" not in response.text
    clients_template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "clients.html").read_text(encoding="utf-8")
    assert "style=" not in clients_template


@pytest.mark.anyio
async def test_admin_logs_render():
    client = TestClient(app)
    response = client.get(
        "/api/v1/admin/logs",
        auth=("admin", "test-admin-password")
    )
    assert response.status_code == 200
    assert "API Event Logs" in response.text
    assert "Recent Events" in response.text
    assert "data-reload-page" in response.text
    assert "onclick=" not in response.text
    assert "style=" not in response.text
    base_template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    logs_template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "logs.html").read_text(encoding="utf-8")
    assert "style=" not in base_template
    assert "style=" not in logs_template


@pytest.mark.anyio
async def test_admin_settings_render():
    client = TestClient(app)
    response = client.get(
        "/api/v1/admin/settings",
        auth=("admin", "test-admin-password")
    )
    assert response.status_code == 200
    assert "System Settings" in response.text
    assert "System Information" in response.text
    assert 'data-toast-message="Settings saved"' in response.text
    assert "onclick=" not in response.text
    assert "style=" not in response.text
    settings_template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "settings.html").read_text(encoding="utf-8")
    assert "style=" not in settings_template


@pytest.mark.anyio
async def test_admin_client_instructions_render():
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Test Store Instructions",
            api_key="instr-api-key",
            portal_key="instr-portal-key",
            pixel_id="123456",
            access_token=encrypt_token("fb-token"),
            is_active=True,
        )
        session.add(test_client)
        await session.commit()
        await session.refresh(test_client)
        client_id = test_client.id

    client = TestClient(app)
    response = client.get(
        f"/api/v1/admin/client/{client_id}/instructions",
        auth=("admin", "test-admin-password")
    )
    assert response.status_code == 200
    assert "Setup Guide" in response.text
    assert 'data-secret="instr-api-key"' in response.text
    assert "GTM Server Container" in response.text
    assert '<script src="/static/js/admin-instructions.js" defer></script>' in response.text
    assert 'data-reveal-target="api_key"' in response.text
    assert 'data-copy-target="gtm_settings"' in response.text
    assert 'data-tab-target="tab-generator"' in response.text
    assert "onclick=" not in response.text
    assert "style=" not in response.text
    assert "function openTab" not in response.text
    assert "function generateEventCode" not in response.text
    instructions_template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "instructions.html").read_text(encoding="utf-8")
    assert "style=" not in instructions_template


@pytest.mark.anyio
async def test_admin_client_edit_render():
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Test Store Edit",
            api_key="edit-api-key",
            portal_key="edit-portal-key",
            pixel_id="789012",
            access_token=encrypt_token("fb-token"),
            is_active=True,
        )
        session.add(test_client)
        await session.commit()
        await session.refresh(test_client)
        client_id = test_client.id

    client = TestClient(app)
    response = client.get(
        f"/api/v1/admin/client/{client_id}/edit",
        auth=("admin", "test-admin-password")
    )
    assert response.status_code == 200
    assert "Edit Client" in response.text
    assert "Test Store Edit" in response.text
    assert '<script src="/static/js/admin.js" defer></script>' in response.text
    assert response.text.count("<script") == 1
    assert "onclick=" not in response.text
    assert "onsubmit=" not in response.text
    edit_template = (Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "edit.html").read_text(encoding="utf-8")
    assert "style=" not in edit_template


# ─── CLIENT PORTAL TESTS ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_client_login_page_render():
    client = TestClient(app)
    response = client.get("/client")
    assert response.status_code == 200
    assert "Buykori AdSync" in response.text
    assert "Login" in response.text
    assert "Signup" in response.text
    assert "Portal Login Key" not in response.text


@pytest.mark.anyio
async def test_client_login_failed_page_render():
    client = TestClient(app)
    response = client.post(
        "/client/login",
        data={"email": "missing@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert "Access Denied" in response.text
    assert "Invalid email or password" in response.text


@pytest.mark.anyio
async def test_client_dashboard_unauthorized_redirect():
    client = TestClient(app)
    response = client.get("/client/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/client"


@pytest.mark.anyio
async def test_client_dashboard_render():
    # Insert a test client to verify dashboard rendering
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Test Store",
            api_key="test-api-key",
            portal_key="test-portal-key",
            pixel_id="1234567890",
            access_token=encrypt_token("test-fb-token"),
            is_active=True,
            deferred_purchase=True
        )
        session.add(test_client)
        await session.commit()
        await session.refresh(test_client)
        client_id = test_client.id

    # Generate valid session cookie value
    session_value = f"client:{client_id}:test-portal-key"
    encrypted_session = encrypt_token(session_value)

    client = TestClient(app)
    client.cookies.set("client_session", encrypted_session)

    response = client.get("/client/dashboard")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert '/static/client-portal/assets/' in response.text


@pytest.mark.anyio
async def test_client_dashboard_render_with_email_session():
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Email Session Store",
            api_key="email-session-api-key",
            portal_key=None,
            pixel_id="1234567890",
            access_token=encrypt_token("test-fb-token"),
            is_active=True,
        )
        session.add(test_client)
        await session.flush()
        user = ClientUser(
            client_id=test_client.id,
            email="owner@email-session.test",
            password_hash=hash_password("correct-password"),
            full_name="Owner User",
            role="owner",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        token = "email-session-token"
        session.add(ClientSession(
            user_id=user.id,
            client_id=test_client.id,
            token_hash=hash_session_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ))
        await session.commit()

    client = TestClient(app)
    client.cookies.set("buykori_client_session", encrypt_token(token))

    response = client.get("/client/dashboard")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


@pytest.mark.anyio
async def test_static_mounts_do_not_expose_client_portal_root():
    mount_paths = {getattr(route, "path", None) for route in app.routes}
    assert "/static" not in mount_paths
    assert "/static/css" in mount_paths
    assert "/static/js" in mount_paths
    assert "/static/client-portal/assets" in mount_paths

    client = TestClient(app)
    assert client.get("/static/client-portal/server.cjs").status_code == 404
    assert client.get("/static/client-portal/server.cjs.map").status_code == 404
    assert client.get("/static/client-portal/index.html").status_code == 404
    assert client.get("/static/css/portal.css").status_code == 200
    admin_js = client.get("/static/js/admin.js")
    assert admin_js.status_code == 200
    assert "window.copyText" in admin_js.text
    instructions_js = client.get("/static/js/admin-instructions.js")
    assert instructions_js.status_code == 200
    assert "data-tab-target" in instructions_js.text


@pytest.mark.anyio
async def test_client_signup_form_creates_email_user():
    client = TestClient(app)
    response = client.post(
        "/client/signup",
        data={
            "full_name": "New Owner",
            "business_name": "New Signup Store",
            "email": "new-owner@example.com",
            "phone_number": "01837224409",
            "password": "strong-password-123",
            "confirm_password": "strong-password-123",
            "domain": "example.com",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    async with TestingSessionLocal() as session:
        user_r = await session.execute(
            select(ClientUser).where(ClientUser.email == "new-owner@example.com")
        )
        user = user_r.scalar_one_or_none()
        assert user is not None
        assert user.phone_number == "+8801837224409"


@pytest.mark.anyio
async def test_client_signup_api_requires_phone_number():
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/client/signup",
        json={
            "full_name": "API Owner",
            "business_name": "API Signup Store",
            "email": "api-owner@example.com",
            "password": "strong-password-123",
            "domain": "api-example.com",
        },
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_marketing_home_render():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Buykori AdSync" in response.text
    assert "Grow Smarter" in response.text


# ─── PLUGIN DOWNLOAD TESTS ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_plugin_download_standard_serves_static_zip(tmp_path, monkeypatch):
    test_zip = tmp_path / "buykori-adsync.zip"
    test_zip.write_bytes(b"dummy-zip-content")

    from app.routers import plugin
    monkeypatch.setattr(plugin, "PLUGIN_ZIP_PATH", test_zip)

    client = TestClient(app)
    response = client.get("/api/v1/plugin/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.content == b"dummy-zip-content"


@pytest.mark.anyio
async def test_plugin_download_with_query_param(tmp_path, monkeypatch):
    import io
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Test Store Query",
            api_key="query-api-key",
            portal_key="query-portal-key",
            pixel_id="11111111",
            access_token=encrypt_token("fb-token"),
            is_active=True,
        )
        session.add(test_client)
        await session.commit()

    mock_src = tmp_path / "wordpress-plugin" / "buykori-adsync"
    mock_src.mkdir(parents=True)
    php_file = mock_src / "buykori-adsync.php"
    php_file.write_text("<?php\n// 'api_key' => '',\n// 'gateway_url' => BUYKORIGW_DEFAULT_GATEWAY_URL,")

    from app.routers import plugin
    monkeypatch.setattr(plugin, "PLUGIN_SOURCE_DIR", mock_src)
    monkeypatch.setattr(plugin, "PLUGIN_PRECONFIGURED_DOWNLOADS", True)

    client = TestClient(app)
    response = client.get("/api/v1/plugin/download?api_key=query-api-key")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    import zipfile
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        patched = zf.read("buykori-adsync/buykori-adsync.php").decode("utf-8")
        assert "query-api-key" in patched


@pytest.mark.anyio
async def test_plugin_download_with_session_cookie(tmp_path, monkeypatch):
    import io
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Test Store Cookie",
            api_key="cookie-api-key",
            portal_key="cookie-portal-key",
            pixel_id="22222222",
            access_token=encrypt_token("fb-token"),
            is_active=True,
        )
        session.add(test_client)
        await session.commit()
        await session.refresh(test_client)
        client_id = test_client.id

    mock_src = tmp_path / "wordpress-plugin" / "buykori-adsync"
    mock_src.mkdir(parents=True)
    php_file = mock_src / "buykori-adsync.php"
    php_file.write_text("<?php\n// 'api_key' => '',\n// 'gateway_url' => BUYKORIGW_DEFAULT_GATEWAY_URL,")

    from app.routers import plugin
    monkeypatch.setattr(plugin, "PLUGIN_SOURCE_DIR", mock_src)
    monkeypatch.setattr(plugin, "PLUGIN_PRECONFIGURED_DOWNLOADS", True)

    session_value = f"client:{client_id}:cookie-portal-key"
    encrypted_session = encrypt_token(session_value)

    client = TestClient(app)
    client.cookies.set("client_session", encrypted_session)

    response = client.get("/api/v1/plugin/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    import zipfile
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        patched = zf.read("buykori-adsync/buykori-adsync.php").decode("utf-8")
        assert "cookie-api-key" in patched

# ─── ADMIN API LOGIN TESTS ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_plugin_connect_authorize_and_exchange():
    async with TestingSessionLocal() as session:
        test_client = Client(
            name="Connect Store",
            api_key="connect-api-key",
            public_key="connect-public-key",
            portal_key="connect-portal-key",
            domain="example.com",
            pixel_id="33333333",
            access_token=encrypt_token("fb-token"),
            is_active=True,
        )
        session.add(test_client)
        await session.commit()
        await session.refresh(test_client)
        client_id = test_client.id

    verifier = "A" * 64
    state = "state-1234567890abcdef"
    portal_session = encrypt_token(f"client:{client_id}:connect-portal-key")

    client = TestClient(app)
    client.cookies.set("client_session", portal_session)
    authorize = client.post(
        "/api/plugin-connect/authorize",
        json={
            "siteUrl": "https://example.com",
            "returnUrl": "https://example.com/wp-admin/admin-post.php?action=buykorigw_connect_callback",
            "state": state,
            "codeChallenge": pkce_challenge(verifier),
        },
    )
    assert authorize.status_code == 200
    redirect_url = authorize.json()["redirectUrl"]
    parsed = urlparse(redirect_url)
    code = parse_qs(parsed.query)["code"][0]
    assert parse_qs(parsed.query)["state"][0] == state

    exchange = client.post(
        "/api/v1/plugin/connect/exchange",
        json={
            "code": code,
            "codeVerifier": verifier,
            "state": state,
            "siteUrl": "https://example.com",
        },
    )
    assert exchange.status_code == 200
    body = exchange.json()
    assert body["api_key"] == "connect-api-key"
    assert body["public_key"] == "connect-public-key"

    replay = client.post(
        "/api/v1/plugin/connect/exchange",
        json={
            "code": code,
            "codeVerifier": verifier,
            "state": state,
            "siteUrl": "https://example.com",
        },
    )
    assert replay.status_code == 409


@pytest.mark.anyio
async def test_admin_api_login_success():
    client = TestClient(app)
    response = client.post(
        "/api/v1/admin/api/login",
        json={"username": "admin", "password": "test-admin-password"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["csrf_token"]
    
    # Verify it is a valid JWT
    jwt_token = data["admin_api_key"]
    from app.routers.admin_api import decode_jwt
    payload = decode_jwt(jwt_token, "test-admin-jwt-secret")
    assert payload["sub"] == "admin"
    set_cookie = response.headers.get("set-cookie", "")
    assert "buykori_admin_session=" in set_cookie
    assert "buykori_admin_csrf=" in set_cookie


@pytest.mark.anyio
async def test_admin_api_cookie_session_and_logout():
    from app.routers import admin_api

    admin_api.ADMIN_COOKIE_SECURE = False
    client = TestClient(app)
    try:
        login = client.post(
            "/api/v1/admin/api/login",
            json={"username": "admin", "password": "test-admin-password"},
        )
        assert login.status_code == 200
        csrf_token = login.json()["csrf_token"]

        summary = client.get("/api/v1/admin/api/summary")
        assert summary.status_code == 200

        create_payload = {
            "name": "CSRF Protected Store",
            "pixel_id": "1234567890",
            "access_token": "test-access-token",
            "domain": "csrf.example.com",
        }
        blocked = client.post("/api/v1/admin/api/clients", json=create_payload)
        assert blocked.status_code == 403
        assert "CSRF" in blocked.json()["detail"]

        created = client.post(
            "/api/v1/admin/api/clients",
            json=create_payload,
            headers={"X-Admin-CSRF-Token": csrf_token},
        )
        assert created.status_code == 200

        logout = client.post("/api/v1/admin/api/logout")
        assert logout.status_code == 200
        logout_cookie = logout.headers.get("set-cookie", "")
        assert "buykori_admin_session=" in logout_cookie
        assert "buykori_admin_csrf=" in logout_cookie
    finally:
        admin_api.ADMIN_COOKIE_SECURE = True

@pytest.mark.anyio
async def test_admin_api_login_failed_wrong_credentials():
    client = TestClient(app)
    response = client.post(
        "/api/v1/admin/api/login",
        json={"username": "admin", "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert "Incorrect username or password" in response.json()["detail"]


@pytest.mark.anyio
async def test_admin_api_client_detail_masks_portal_key():
    raw_portal_key = "portal-secret-1234567890"
    async with TestingSessionLocal() as session:
        client_row = Client(
            name="Portal Mask Store",
            api_key="portal-mask-api-key",
            public_key="portal-mask-public-key",
            portal_key=raw_portal_key,
            pixel_id="123456789",
            access_token=encrypt_token("fb-token"),
            domain="portal-mask.example.com",
            is_active=True,
        )
        session.add(client_row)
        await session.commit()
        client_id = client_row.id

    api_client = TestClient(app)
    response = api_client.get(
        f"/api/v1/admin/api/clients/{client_id}",
        headers={"X-Admin-API-Key": "test-admin-api-key"},
    )

    assert response.status_code == 200
    client_payload = response.json()["client"]
    assert client_payload["portal_key"] == mask_secret(raw_portal_key)
    assert client_payload["portal_key"] != raw_portal_key
    assert client_payload["portal_key_masked"] is True
    assert client_payload["public_key"] == "portal-mask-public-key"


@pytest.mark.anyio
async def test_admin_api_client_intelligence_support_notes_and_server_health():
    from app.models.event_log import EventLog

    async with TestingSessionLocal() as session:
        client_row = Client(
            name="Intel Store",
            api_key="intel-api-key",
            portal_key="intel-portal-key",
            pixel_id="123456789",
            access_token=encrypt_token("fb-token"),
            domain="intel.example.com",
            is_active=True,
            billing_status="trial",
            plan_tier="free",
            trial_started_at=datetime.now(timezone.utc) - timedelta(days=12),
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=2),
        )
        session.add(client_row)
        await session.flush()
        session.add(ClientUser(
            client_id=client_row.id,
            email="intel-owner@example.com",
            phone_number="+8801837224409",
            password_hash=hash_password("strong-password-123"),
            full_name="Intel Owner",
            role="owner",
            is_active=True,
        ))
        session.add(EventLog(client_id=client_row.id, event_name="PageView", event_count=5, status="success"))
        session.add(EventLog(client_id=client_row.id, event_name="Purchase", event_count=2, status="success"))
        await session.commit()
        client_id = client_row.id

    api_client = TestClient(app)
    headers = {"X-Admin-API-Key": "test-admin-api-key"}
    note = api_client.post(
        f"/api/v1/admin/api/clients/{client_id}/support-notes",
        headers=headers,
        json={"note": "Called owner; wants setup help."},
    )
    assert note.status_code == 200
    assert note.json()["note"]["note"] == "Called owner; wants setup help."

    notes = api_client.get(f"/api/v1/admin/api/clients/{client_id}/support-notes", headers=headers)
    assert notes.status_code == 200
    assert len(notes.json()["notes"]) == 1

    intel = api_client.get("/api/v1/admin/api/client-intelligence", headers=headers)
    assert intel.status_code == 200
    intel_row = next(row for row in intel.json()["clients"] if row["client"]["id"] == client_id)
    assert intel_row["owner"]["phone_number"] == "+8801837224409"
    assert intel_row["health_score"]["score"] > 0
    assert intel_row["trial_followup"]["priority"] == "high"
    assert any(step["key"] == "first_purchase" and step["done"] for step in intel_row["onboarding_funnel"])
    assert intel_row["support_note_count"] == 1

    server_health = api_client.get("/api/v1/admin/api/server-health", headers=headers)
    assert server_health.status_code == 200
    payload = server_health.json()
    assert "server" in payload
    assert "worker_monitor" in payload
    assert "memory" in payload["server"]
    assert "cpu" in payload["server"]
    assert "used_percent" in payload["server"]["cpu"]


@pytest.mark.anyio
async def test_admin_api_incomplete_checkout_operations_masks_customer_data_and_updates_status():
    from app.models.incomplete_checkout import IncompleteCheckout

    async with TestingSessionLocal() as session:
        client_row = Client(
            name="Recovery Ops Store",
            api_key="recovery-ops-api-key",
            portal_key="recovery-ops-portal-key",
            pixel_id="123456700",
            access_token=encrypt_token("fb-token"),
            domain="recovery.example.com",
            is_active=True,
        )
        session.add(client_row)
        await session.flush()
        checkout = IncompleteCheckout(
            client_id=client_row.id,
            visitor_id="visitor-recovery-ops",
            phone="8801712345678",
            phone_hash="phone-hash-recovery-ops",
            customer_name="Recovery Customer",
            email="customer@example.com",
            address="Secret address",
            products=[{"content_name": "Black Bra", "quantity": 1}],
            amount=950,
            status="incomplete",
        )
        session.add(checkout)
        await session.commit()
        client_id = client_row.id
        checkout_id = checkout.id

    api_client = TestClient(app)
    headers = {"X-Admin-API-Key": "test-admin-api-key"}
    response = api_client.get(
        f"/api/v1/admin/api/incomplete-checkouts?client_id={client_id}",
        headers=headers,
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["phone_masked"] == "***5678"
    assert "phone" not in item
    assert item["address_present"] is True
    assert item["product_summary"] == "Black Bra"

    updated = api_client.patch(
        f"/api/v1/admin/api/incomplete-checkouts/{checkout_id}",
        headers=headers,
        json={"status": "contacted"},
    )
    assert updated.status_code == 200
    assert updated.json()["item"]["status"] == "contacted"


@pytest.mark.anyio
async def test_admin_client_edit_syncs_trial_and_paid_growth_quotas():
    async with TestingSessionLocal() as session:
        client_row = Client(
            name="Plan Sync Store",
            api_key="plan-sync-api-key",
            portal_key="plan-sync-portal-key",
            pixel_id="987654321",
            access_token=encrypt_token("fb-token"),
            domain="plan-sync.example.com",
            is_active=True,
            billing_status="paid",
            plan_tier="growth",
            monthly_limit=500_000,
        )
        session.add(client_row)
        await session.commit()
        await session.refresh(client_row)
        client_id = client_row.id

    api_client = TestClient(app)
    headers = {"X-Admin-API-Key": "test-admin-api-key"}

    trial = api_client.patch(
        f"/api/v1/admin/api/clients/{client_id}",
        headers=headers,
        json={
            "plan_tier": "growth",
            "billing_status": "trial",
            "monthly_limit": 500_000,
        },
    )
    assert trial.status_code == 200
    assert trial.json()["client"]["billing_status"] == "trial"
    assert trial.json()["client"]["monthly_limit"] == 25_000
    assert trial.json()["client"]["orders_quota"] == 300

    paid = api_client.patch(
        f"/api/v1/admin/api/clients/{client_id}",
        headers=headers,
        json={
            "plan_tier": "growth",
            "billing_status": "paid",
            "monthly_limit": 25_000,
        },
    )
    assert paid.status_code == 200
    assert paid.json()["client"]["billing_status"] == "paid"
    assert paid.json()["client"]["monthly_limit"] == 500_000
    assert paid.json()["client"]["orders_quota"] == 2_000

    custom = api_client.patch(
        f"/api/v1/admin/api/clients/{client_id}",
        headers=headers,
        json={
            "plan_tier": "growth",
            "billing_status": "paid",
            "monthly_limit": 750_000,
        },
    )
    assert custom.status_code == 200
    assert custom.json()["client"]["monthly_limit"] == 750_000


@pytest.mark.anyio
async def test_admin_api_events_endpoint():
    from app.models.event_log import EventLog
    
    async with TestingSessionLocal() as session:
        client1 = Client(
            name="Client A",
            api_key="client1-api-key",
            portal_key="client1-portal-key",
            pixel_id="111111",
            access_token=encrypt_token("token-a"),
            is_active=True,
        )
        client2 = Client(
            name="Client B",
            api_key="client2-api-key",
            portal_key="client2-portal-key",
            pixel_id="222222",
            access_token=encrypt_token("token-b"),
            is_active=True,
        )
        session.add(client1)
        session.add(client2)
        await session.commit()
        await session.refresh(client1)
        await session.refresh(client2)
        
        log1 = EventLog(
            client_id=client1.id,
            event_name="GA4:Purchase",
            status="success",
            ip_address="192.168.1.5",
            event_id="evt_001",
            event_count=1,
            value=1200.0,
            currency="BDT",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10)
        )
        log2 = EventLog(
            client_id=client1.id,
            event_name="TikTok:AddToCart",
            status="failed",
            error_message="Invalid token structure",
            ip_address="192.168.1.10",
            event_id="evt_002",
            event_count=1,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        log3 = EventLog(
            client_id=client2.id,
            event_name="Facebook:PageView",
            status="success",
            ip_address="10.0.0.1",
            event_id="evt_003",
            event_count=1,
            created_at=datetime.now(timezone.utc)
        )
        session.add(log1)
        session.add(log2)
        session.add(log3)
        await session.commit()
        
    client = TestClient(app)
    headers = {"X-Admin-API-Key": "test-admin-api-key"}
    
    # 1. Test fetching all events
    response = client.get("/api/v1/admin/api/events", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["totalCount"] == 3
    assert len(data["events"]) == 3
    
    # 2. Test pagination limit
    response = client.get("/api/v1/admin/api/events?limit=2", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["totalCount"] == 3
    assert len(data["events"]) == 2
    
    # 3. Test client ID filtering
    response = client.get(f"/api/v1/admin/api/events?client_id={client1.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["totalCount"] == 2
    assert all(e["client_id"] == client1.id for e in data["events"])
    
    # 4. Test platform filtering
    response = client.get("/api/v1/admin/api/events?platform=GA4", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["totalCount"] == 1
    assert data["events"][0]["platform"] == "GA4"
    
    # 5. Test status filtering
    response = client.get("/api/v1/admin/api/events?status=failed", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["totalCount"] == 1
    assert data["events"][0]["status"] == "Failed"
    
    # 6. Test search filter
    response = client.get("/api/v1/admin/api/events?search=token", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["totalCount"] == 1
    assert "token" in data["events"][0]["responseBody"]["error"]["message"].lower()


@pytest.mark.anyio
async def test_admin_api_delete_client_with_users_and_sessions():
    # Import all models locally for cleanup verification
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
    from app.models.event_outbox import EventOutbox
    from app.models.failed_event import FailedEvent
    from app.models.event_dedup import EventDedup
    from app.models.usage_counter import UsageCounter
    from app.models.event_log import EventLog
    from app.models.site_binding import SiteBinding

    async with TestingSessionLocal() as session:
        # Create a client
        client_to_del = Client(
            name="To Delete Client API",
            api_key="del-api-key",
            portal_key="del-portal-key",
            pixel_id="111111",
            access_token=encrypt_token("token-del"),
            is_active=True,
        )
        session.add(client_to_del)
        await session.flush()
        
        # Create a user
        user = ClientUser(
            client_id=client_to_del.id,
            email="del-user@example.com",
            password_hash=hash_password("password"),
            full_name="User to Delete",
            role="owner",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        
        # Create a session
        sess = ClientSession(
            user_id=user.id,
            client_id=client_to_del.id,
            token_hash=hash_session_token("some-session-token"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(sess)

        # Populate all other dependencies
        session.add(ClientSupportNote(client_id=client_to_del.id, note="some note", created_by="admin"))
        session.add(SiteBinding(client_id=client_to_del.id, site_host="example.com", root_domain="example.com"))
        session.add(PluginConnectSession(
            client_id=client_to_del.id,
            site_url="https://example.com",
            site_host="example.com",
            return_url="https://example.com",
            state="state",
            code_hash="hash_api",
            code_challenge="challenge",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        ))
        session.add(TrialIdentity(client_id=client_to_del.id, domain="example.com", pixel_id="111111"))
        session.add(NotificationJob(client_id=client_to_del.id, event_type="purchase", payload={}, dedupe_key="dedupe-key-api"))
        session.add(IncompleteCheckout(client_id=client_to_del.id, visitor_id="visitor", phone="123", phone_hash="hash"))
        
        pending = PendingEvent(client_id=client_to_del.id, order_id="ORDER-123", event_data={}, raw_order_data={}, status="pending", portal_state="pending", is_confirmed=False)
        session.add(pending)
        await session.flush()
        
        order = CourierOrder(client_id=client_to_del.id, pending_event_id=pending.id, order_id="ORDER-123", courier_provider="steadfast", courier_status="pending")
        session.add(order)
        await session.flush()
        
        session.add(CourierBookingJob(client_id=client_to_del.id, pending_event_id=pending.id, courier_order_id=order.id, provider="steadfast", request_payload={}))
        
        session.add(AdInsightDaily(client_id=client_to_del.id, platform="meta", external_campaign_id="camp-123", insight_date=datetime.now(timezone.utc).date(), spend=10.0, impressions=100, clicks=10, platform_purchases=1))
        
        ad_acc = AdAccount(client_id=client_to_del.id, platform="meta", external_account_id="act-123", access_token_enc="enc")
        session.add(ad_acc)
        await session.flush()
        
        session.add(AdCampaign(ad_account_id=ad_acc.id, platform="meta", external_campaign_id="camp-123", name="Camp 1"))
        
        session.add(EventOutbox(client_id=client_to_del.id, event_payload={}, status="queued"))
        session.add(FailedEvent(client_id=client_to_del.id, event_payload={}, error_message="failed"))
        session.add(EventDedup(client_id=client_to_del.id, event_id="evt-123"))
        session.add(UsageCounter(client_id=client_to_del.id, window_key="key", count=1))
        session.add(EventLog(client_id=client_to_del.id, event_name="Purchase", event_count=1, status="success"))

        await session.commit()
        await session.refresh(client_to_del)
        client_id = client_to_del.id

    client = TestClient(app)
    headers = {"X-Admin-API-Key": "test-admin-api-key"}
    
    # Execute delete request
    response = client.delete(f"/api/v1/admin/api/clients/{client_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify database is clean for all target models
    async with TestingSessionLocal() as session:
        assert (await session.get(Client, client_id)) is None
        assert len((await session.execute(select(ClientUser).where(ClientUser.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(ClientSession).where(ClientSession.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(ClientSupportNote).where(ClientSupportNote.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(SiteBinding).where(SiteBinding.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(PluginConnectSession).where(PluginConnectSession.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(TrialIdentity).where(TrialIdentity.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(NotificationJob).where(NotificationJob.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(IncompleteCheckout).where(IncompleteCheckout.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(PendingEvent).where(PendingEvent.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(CourierOrder).where(CourierOrder.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(CourierBookingJob).where(CourierBookingJob.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(AdInsightDaily).where(AdInsightDaily.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(AdAccount).where(AdAccount.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(AdCampaign).where(AdCampaign.ad_account_id.in_(select(AdAccount.id).where(AdAccount.client_id == client_id))))).scalars().all()) == 0
        assert len((await session.execute(select(EventOutbox).where(EventOutbox.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(FailedEvent).where(FailedEvent.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(EventDedup).where(EventDedup.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(UsageCounter).where(UsageCounter.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(EventLog).where(EventLog.client_id == client_id))).scalars().all()) == 0


@pytest.mark.anyio
async def test_admin_views_delete_client_with_users_and_sessions():
    # Import all models locally for cleanup verification
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
    from app.models.event_outbox import EventOutbox
    from app.models.failed_event import FailedEvent
    from app.models.event_dedup import EventDedup
    from app.models.usage_counter import UsageCounter
    from app.models.event_log import EventLog
    from app.models.site_binding import SiteBinding

    async with TestingSessionLocal() as session:
        # Create a client
        client_to_del = Client(
            name="To Delete Client View",
            api_key="del-view-key",
            portal_key="del-view-portal",
            pixel_id="222222",
            access_token=encrypt_token("token-del-view"),
            is_active=True,
        )
        session.add(client_to_del)
        await session.flush()
        
        # Create a user
        user = ClientUser(
            client_id=client_to_del.id,
            email="del-user-view@example.com",
            password_hash=hash_password("password"),
            full_name="User to Delete View",
            role="owner",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        
        # Create a session
        sess = ClientSession(
            user_id=user.id,
            client_id=client_to_del.id,
            token_hash=hash_session_token("some-view-session-token"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(sess)

        # Populate all other dependencies
        session.add(ClientSupportNote(client_id=client_to_del.id, note="some note view", created_by="admin"))
        session.add(SiteBinding(client_id=client_to_del.id, site_host="example.com", root_domain="example.com"))
        session.add(PluginConnectSession(
            client_id=client_to_del.id,
            site_url="https://example.com",
            site_host="example.com",
            return_url="https://example.com",
            state="state",
            code_hash="hash_view",
            code_challenge="challenge",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        ))
        session.add(TrialIdentity(client_id=client_to_del.id, domain="example.com", pixel_id="222222"))
        session.add(NotificationJob(client_id=client_to_del.id, event_type="purchase", payload={}, dedupe_key="dedupe-key-view"))
        session.add(IncompleteCheckout(client_id=client_to_del.id, visitor_id="visitor", phone="456", phone_hash="hash"))
        
        pending = PendingEvent(client_id=client_to_del.id, order_id="ORDER-456", event_data={}, raw_order_data={}, status="pending", portal_state="pending", is_confirmed=False)
        session.add(pending)
        await session.flush()
        
        order = CourierOrder(client_id=client_to_del.id, pending_event_id=pending.id, order_id="ORDER-456", courier_provider="steadfast", courier_status="pending")
        session.add(order)
        await session.flush()
        
        session.add(CourierBookingJob(client_id=client_to_del.id, pending_event_id=pending.id, courier_order_id=order.id, provider="steadfast", request_payload={}))
        
        session.add(AdInsightDaily(client_id=client_to_del.id, platform="meta", external_campaign_id="camp-456", insight_date=datetime.now(timezone.utc).date(), spend=10.0, impressions=100, clicks=10, platform_purchases=1))
        
        ad_acc = AdAccount(client_id=client_to_del.id, platform="meta", external_account_id="act-456", access_token_enc="enc")
        session.add(ad_acc)
        await session.flush()
        
        session.add(AdCampaign(ad_account_id=ad_acc.id, platform="meta", external_campaign_id="camp-456", name="Camp 2"))
        
        session.add(EventOutbox(client_id=client_to_del.id, event_payload={}, status="queued"))
        session.add(FailedEvent(client_id=client_to_del.id, event_payload={}, error_message="failed"))
        session.add(EventDedup(client_id=client_to_del.id, event_id="evt-456"))
        session.add(UsageCounter(client_id=client_to_del.id, window_key="key", count=1))
        session.add(EventLog(client_id=client_to_del.id, event_name="Purchase", event_count=1, status="success"))

        await session.commit()
        await session.refresh(client_to_del)
        client_id = client_to_del.id

    client = TestClient(app)
    # Generate CSRF token
    from app.routers.admin_views import create_admin_csrf_token
    csrf_token = create_admin_csrf_token("admin")
    
    # Execute delete POST request
    response = client.post(
        f"/api/v1/admin/client/{client_id}/delete",
        auth=("admin", "test-admin-password"),
        data={"csrf_token": csrf_token},
        follow_redirects=False
    )
    assert response.status_code == 303  # redirect status
    assert "deleted" in response.headers["location"].lower()

    # Verify database is clean for all target models
    async with TestingSessionLocal() as session:
        assert (await session.get(Client, client_id)) is None
        assert len((await session.execute(select(ClientUser).where(ClientUser.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(ClientSession).where(ClientSession.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(ClientSupportNote).where(ClientSupportNote.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(SiteBinding).where(SiteBinding.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(PluginConnectSession).where(PluginConnectSession.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(TrialIdentity).where(TrialIdentity.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(NotificationJob).where(NotificationJob.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(IncompleteCheckout).where(IncompleteCheckout.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(PendingEvent).where(PendingEvent.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(CourierOrder).where(CourierOrder.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(CourierBookingJob).where(CourierBookingJob.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(AdInsightDaily).where(AdInsightDaily.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(AdAccount).where(AdAccount.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(AdCampaign).where(AdCampaign.ad_account_id.in_(select(AdAccount.id).where(AdAccount.client_id == client_id))))).scalars().all()) == 0
        assert len((await session.execute(select(EventOutbox).where(EventOutbox.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(FailedEvent).where(FailedEvent.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(EventDedup).where(EventDedup.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(UsageCounter).where(UsageCounter.client_id == client_id))).scalars().all()) == 0
        assert len((await session.execute(select(EventLog).where(EventLog.client_id == client_id))).scalars().all()) == 0


@pytest.mark.anyio
async def test_admin_api_courier_booking_queue_summary_and_retry():
    async with TestingSessionLocal() as session:
        booking_client = Client(
            name="Courier Queue Client",
            api_key="courier-queue-api-key",
            pixel_id="333333",
            access_token=encrypt_token("courier-queue-token"),
            is_active=True,
        )
        session.add(booking_client)
        await session.flush()

        pending = PendingEvent(
            client_id=booking_client.id,
            order_id="QUEUE-1001",
            event_data={},
            raw_order_data={},
            status="pending",
            portal_state="pending",
            is_confirmed=False,
        )
        session.add(pending)
        await session.flush()

        order = CourierOrder(
            client_id=booking_client.id,
            pending_event_id=pending.id,
            order_id=pending.order_id,
            courier_provider="steadfast",
            courier_status="booking_failed",
            status_history=[],
        )
        session.add(order)
        await session.flush()

        job = CourierBookingJob(
            client_id=booking_client.id,
            pending_event_id=pending.id,
            courier_order_id=order.id,
            provider="steadfast",
            request_payload={"recipient_phone": "01837224409"},
            status="dead",
            attempts=8,
            max_attempts=8,
            next_attempt_at=datetime.now(timezone.utc),
            last_error="provider timeout",
        )
        session.add(job)
        await session.commit()
        job_id = job.id
        order_id = order.id
        pending_id = pending.id

    api_client = TestClient(app)
    headers = {"X-Admin-API-Key": "test-admin-api-key"}

    summary = api_client.get("/api/v1/admin/api/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["courier_booking_queue"]["dead"] == 1
    assert summary.json()["courier_booking_queue"]["alert_status"] == "critical"
    assert summary.json()["courier_booking_queue"]["alerts"][0]["code"] == "dead_letter_jobs"

    queue = api_client.get("/api/v1/admin/api/courier-booking-queue", headers=headers)
    assert queue.status_code == 200
    queue_data = queue.json()
    assert queue_data["counts"]["dead"] == 1
    assert queue_data["jobs"][0]["order_id"] == "QUEUE-1001"
    assert queue_data["jobs"][0]["last_error"] == "provider timeout"
    assert "request_payload" not in queue_data["jobs"][0]

    retry = api_client.post(f"/api/v1/admin/api/courier-booking-queue/{job_id}/retry", headers=headers)
    assert retry.status_code == 200
    assert retry.json() == {"status": "success", "job_id": job_id, "job_status": "queued"}

    async with TestingSessionLocal() as session:
        retried_job = await session.get(CourierBookingJob, job_id)
        retried_order = await session.get(CourierOrder, order_id)
        retried_pending = await session.get(PendingEvent, pending_id)
        assert retried_job.status == "queued"
        assert retried_job.attempts == 0
        assert retried_job.last_error is None
        assert retried_order.courier_status == "booking_queued"
        assert retried_pending.status == "courier_booking_queued"
        assert retried_pending.portal_state == "processing"
        assert retried_pending.is_confirmed is True


@pytest.mark.anyio
async def test_admin_api_courier_booking_queue_warns_for_delayed_job(monkeypatch):
    monkeypatch.setenv("COURIER_BOOKING_QUEUE_WARN_SECONDS", "30")
    monkeypatch.setenv("COURIER_BOOKING_PROCESSING_WARN_SECONDS", "invalid")
    async with TestingSessionLocal() as session:
        booking_client = Client(
            name="Delayed Courier Queue Client",
            api_key="delayed-courier-queue-api-key",
            pixel_id="444444",
            access_token=encrypt_token("delayed-courier-queue-token"),
            is_active=True,
        )
        session.add(booking_client)
        await session.flush()

        pending = PendingEvent(
            client_id=booking_client.id,
            order_id="QUEUE-DELAYED-1001",
            event_data={},
            raw_order_data={},
            status="courier_booking_queued",
            portal_state="processing",
            is_confirmed=True,
        )
        session.add(pending)
        await session.flush()

        order = CourierOrder(
            client_id=booking_client.id,
            pending_event_id=pending.id,
            order_id=pending.order_id,
            courier_provider="steadfast",
            courier_status="booking_queued",
            status_history=[],
        )
        session.add(order)
        await session.flush()

        session.add(CourierBookingJob(
            client_id=booking_client.id,
            pending_event_id=pending.id,
            courier_order_id=order.id,
            provider="steadfast",
            request_payload={},
            status="queued",
            attempts=0,
            max_attempts=8,
            next_attempt_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ))
        await session.commit()

    api_client = TestClient(app)
    response = api_client.get(
        "/api/v1/admin/api/courier-booking-queue",
        headers={"X-Admin-API-Key": "test-admin-api-key"},
    )

    assert response.status_code == 200
    counts = response.json()["counts"]
    assert counts["queued"] == 1
    assert counts["oldest_queued_age_seconds"] >= 299
    assert counts["queued_warn_seconds"] == 30
    assert counts["processing_warn_seconds"] == 600
    assert counts["alert_status"] == "warning"
    assert counts["alerts"] == [{
        "code": "queued_delayed",
        "severity": "warning",
        "age_seconds": counts["oldest_queued_age_seconds"],
    }]
