import hashlib
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault(
    "ADMIN_PASSWORD",
    "pbkdf2_sha256$210000$dGVzdC1hZG1pbi1zYWx0LTE=$9gwSQUsI_uzxaNpdvx_cOcpF4opgO7Ma_Hcmq3z4kSU=",
)
os.environ.setdefault("ADMIN_API_KEY", "test-admin-api-key")
os.environ.setdefault("ADMIN_JWT_SECRET", "test-admin-jwt-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("ENCRYPTION_KEY", "ZFhnf1szwemka8kBbH9jPTC7oKBRTEv0EqWt1J8AD0M=")

from app.routers.admin import create_admin_csrf_token, mask_secret, verify_admin_csrf_token
from app.routers.events import _is_domain_allowed, _verify_capi_signature
from app.routers.webhook import _client_api_key_from_request
from app.main import _is_tracker_path, RequestSizeLimitMiddleware, SplitCORSMiddleware
from app.schemas.event import UserData, _clean_and_hash
from app.services.auth_service import verify_admin_password
from app.security import encrypt_token, encrypted_credential_is_configured, meta_credentials_configured
from app import limiter as limiter_module
from fastapi import HTTPException
from starlette.requests import Request
from fastapi.testclient import TestClient
from app.main import app


def test_admin_csrf_token_round_trip():
    token = create_admin_csrf_token("admin")
    verify_admin_csrf_token(token, "admin")


def test_admin_csrf_rejects_tampering():
    token = create_admin_csrf_token("admin")
    bad_token = token[:-1] + ("0" if token[-1] != "0" else "1")

    try:
        verify_admin_csrf_token(bad_token, "admin")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("Tampered CSRF token was accepted")


def test_domain_matching_is_exact_or_real_subdomain():
    assert _is_domain_allowed("example.com", "example.com")
    assert _is_domain_allowed("shop.example.com", "example.com")
    assert not _is_domain_allowed("badexample.com", "example.com")
    assert not _is_domain_allowed("example.com.attacker.test", "example.com")


def test_tracker_path_matching_does_not_include_client_routes():
    assert _is_tracker_path("/c")
    assert _is_tracker_path("/c/batch")
    assert _is_tracker_path("/t.js")
    assert not _is_tracker_path("/client")
    assert not _is_tracker_path("/custom")


def test_blank_pii_values_are_not_preserved_before_hashing():
    assert _clean_and_hash("   ", "em") == ""
    assert UserData(em=["   "], ph=[""]).em == [""]
    assert UserData(em=["   "], ph=[""]).ph == [""]


def test_tracking_body_limit_rejects_large_request():
    client = TestClient(app)
    response = client.post(
        "/c",
        content=b"x" * (262144 + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


@pytest.mark.anyio
async def test_tracking_body_limit_counts_streaming_body_without_content_length():
    async def app_that_reads_body(scope, receive, send):
        while True:
            message = await receive()
            if message.get("type") != "http.request" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestSizeLimitMiddleware(app_that_reads_body)
    messages = [
        {"type": "http.request", "body": b"x" * 200_000, "more_body": True},
        {"type": "http.request", "body": b"x" * 70_000, "more_body": False},
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/c",
            "headers": [],
        },
        receive,
        send,
    )

    response_start = next(message for message in sent if message["type"] == "http.response.start")
    assert response_start["status"] == 413


def test_security_headers_are_added_to_html_responses():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "object-src 'none'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")


def test_admin_preflight_allows_csrf_header():
    client = TestClient(app)
    response = client.options(
        "/api/v1/admin/api/clients",
        headers={
            "Origin": "https://admin.buykori.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Admin-CSRF-Token, Content-Type",
        },
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://admin.buykori.app"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "X-Admin-CSRF-Token" in response.headers["access-control-allow-headers"]


@pytest.mark.anyio
async def test_tracker_cors_replaces_route_level_acao_header():
    async def app_with_existing_cors(_scope, _receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"access-control-allow-origin", b"*")],
        })
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = SplitCORSMiddleware(app_with_existing_cors)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/t.js",
            "headers": [(b"origin", b"https://shop.example")],
        },
        receive,
        send,
    )

    response_start = next(message for message in sent if message["type"] == "http.response.start")
    acao = [
        value.decode("latin1")
        for name, value in response_start["headers"]
        if name.lower() == b"access-control-allow-origin"
    ]
    assert acao == ["https://shop.example"]


def test_legacy_admin_password_is_blocked_in_production(monkeypatch):
    monkeypatch.setenv("PRIMARY_DOMAIN", "api.buykori.app")
    monkeypatch.setenv("ALLOW_LEGACY_ADMIN_PASSWORD", "true")
    assert not verify_admin_password("test-admin-password", "test-admin-password")


def _security_request(*, headers=None, query_string=b"", method="POST"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "headers": headers or [],
            "query_string": query_string,
            "client": ("127.0.0.1", 1234),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


def test_client_cookie_auth_post_requires_origin_header():
    from app.routers.client_auth import CLIENT_SESSION_COOKIE, require_allowed_origin

    request = _security_request(headers=[(b"cookie", f"{CLIENT_SESSION_COOKIE}=encrypted".encode())])

    with pytest.raises(HTTPException) as exc:
        require_allowed_origin(request)

    assert exc.value.status_code == 403
    assert "Origin is required" in exc.value.detail


def test_client_cookie_auth_post_requires_csrf_token():
    from app.routers.client_auth import CLIENT_CSRF_COOKIE, CLIENT_CSRF_HEADER, CLIENT_SESSION_COOKIE, require_allowed_origin

    request = _security_request(headers=[
        (b"origin", b"https://client.buykori.app"),
        (b"host", b"client.buykori.app"),
        (b"cookie", f"{CLIENT_SESSION_COOKIE}=encrypted; {CLIENT_CSRF_COOKIE}=csrf-token".encode()),
    ])

    with pytest.raises(HTTPException) as exc:
        require_allowed_origin(request)

    assert exc.value.status_code == 403
    assert "CSRF" in exc.value.detail

    request = _security_request(headers=[
        (b"origin", b"https://client.buykori.app"),
        (b"host", b"client.buykori.app"),
        (b"cookie", f"{CLIENT_SESSION_COOKIE}=encrypted; {CLIENT_CSRF_COOKIE}=csrf-token".encode()),
        (CLIENT_CSRF_HEADER.lower().encode(), b"csrf-token"),
    ])
    require_allowed_origin(request)


def test_client_non_cookie_post_can_omit_origin_for_api_clients():
    from app.routers.client_auth import require_allowed_origin

    request = _security_request()

    require_allowed_origin(request)


def test_product_rules_document_global_owner_email_uniqueness():
    root = Path(__file__).resolve().parents[1]
    rules = (root / "docs" / "PRODUCT_RULES.md").read_text(encoding="utf-8")
    client_auth = (root / "app" / "routers" / "client_auth.py").read_text(encoding="utf-8")

    assert "globally unique owner email addresses" in rules
    assert "store-switching flow" in rules
    assert "select(ClientUser).where(ClientUser.email == email)" in client_auth


def test_legacy_failed_event_retry_does_not_increment_usage_without_reservation_metadata():
    retry_service = (Path(__file__).resolve().parents[1] / "app" / "services" / "retry_service.py").read_text(encoding="utf-8")

    assert "increment_usage_counters_db" not in retry_service


def test_webhook_api_key_prefers_header_over_query():
    request = _security_request(
        headers=[(b"x-api-key", b"header-key")],
        query_string=b"key=query-key",
    )
    assert _client_api_key_from_request(request, provider="Shopify") == "header-key"


def test_webhook_query_api_key_logs_legacy_warning(caplog):
    caplog.set_level("WARNING", logger="app.routers.webhook")
    request = _security_request(query_string=b"key=query-key")
    assert _client_api_key_from_request(request, provider="Shopify") == "query-key"
    assert "legacy query key" in caplog.text


def test_woocommerce_webhook_api_key_uses_shared_extractor():
    request = _security_request(headers=[(b"x-buykori-api-key", b"buykori-header-key")])

    assert _client_api_key_from_request(request, provider="WooCommerce") == "buykori-header-key"


def test_admin_jwt_secret_is_separate_from_direct_api_key():
    from app.routers import admin_api

    token = admin_api.create_jwt({"sub": "admin"}, "test-admin-jwt-secret")

    assert admin_api.decode_jwt(token, "test-admin-jwt-secret")["sub"] == "admin"
    with pytest.raises(ValueError):
        admin_api.decode_jwt(token, "test-admin-api-key")


def test_production_setup_sets_app_env_and_strict_csp():
    root = Path(__file__).resolve().parents[1]
    setup = (root / "deploy" / "setup.sh").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")

    assert 'APP_ENV="production"' in setup
    assert "CSP_STRICT_NONCE=true" in setup
    assert "APP_ENV=development" in env_example


def test_admin_client_detail_masks_keys_in_normal_detail_response():
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "admin_api.py").read_text(encoding="utf-8")
    detail_block = source.split("async def admin_api_get_client", 1)[1].split(
        "@router.get(\"/admin/api/clients/{client_id}/support-notes\")",
        1,
    )[0]

    assert "client_to_api_dict(client, mask_keys=True, mask_portal_key=True)" in detail_block


@pytest.mark.anyio
async def test_webhook_replay_uses_local_fallback_when_redis_is_unavailable(monkeypatch):
    from app.routers import webhook
    from app.services import redis_pool

    webhook._local_webhook_replays.clear()
    monkeypatch.setattr(redis_pool, "get_redis", lambda: None)

    assert await webhook._webhook_is_duplicate("local-fallback-test") is False
    assert await webhook._webhook_is_duplicate("local-fallback-test") is True


def test_woocommerce_webhook_secret_prefers_per_client_secret(monkeypatch):
    from types import SimpleNamespace

    from app.routers import webhook

    client = SimpleNamespace(name="Demo Client", woocommerce_webhook_secret="encrypted-secret")

    monkeypatch.setenv("WC_WEBHOOK_SECRET", "global-secret")
    monkeypatch.setattr(webhook, "decrypt_token", lambda value: "client-secret")

    assert webhook._woocommerce_webhook_secret_for_client(client) == "client-secret"


def test_courier_global_webhook_fallbacks_are_disabled_by_default():
    from app.routers import courier_webhook

    assert courier_webhook.ALLOW_GLOBAL_COURIER_WEBHOOK_SECRET_FALLBACK is False
    assert courier_webhook.ALLOW_GLOBAL_STEADFAST_WEBHOOK_TOKEN_FALLBACK is False

    request = _security_request(headers=[(b"x-courier-webhook-secret", b"global-secret")])
    with pytest.raises(HTTPException) as exc:
        courier_webhook._verify_courier_webhook_secret(request)
    assert exc.value.status_code == 401
    assert "Client courier webhook secret" in exc.value.detail

    request = _security_request(headers=[(b"authorization", b"Bearer global-token")])
    with pytest.raises(HTTPException) as exc:
        courier_webhook._verify_steadfast_bearer_token(request)
    assert exc.value.status_code == 401


def test_capi_signature_uses_dedicated_signing_secret():
    from types import SimpleNamespace

    from app.routers import events
    from app.services.client_secrets import ensure_capi_signing_secret

    raw_body = b'{"data":[]}'
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    client = SimpleNamespace(name="Demo", api_key="api-key", capi_signing_secret=None)
    signing_secret = ensure_capi_signing_secret(client)

    assert signing_secret != client.api_key
    assert events._capi_signing_secret_for_client(client) == signing_secret

    good_signature = hmac.new(
        signing_secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    api_key_signature = hmac.new(
        client.api_key.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()

    assert _verify_capi_signature(raw_body, signing_secret, timestamp, good_signature)
    assert not _verify_capi_signature(raw_body, signing_secret, timestamp, api_key_signature)


def test_connection_routes_do_not_read_expired_client_after_commit():
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "client_api.py").read_text(encoding="utf-8")

    get_connection = source.split("async def get_connection", 1)[1].split("@router.post(\"/connection/test\")", 1)[0]
    test_connection = source.split("async def test_wp_connection", 1)[1].split("@router.post(\"/connection/revoke\")", 1)[0]
    revoke_connection = source.split("async def revoke_wp_token", 1)[1].split(
        "@router.post(\"/plugin-connect/authorize\")",
        1,
    )[0]

    assert "return response" in get_connection.split("await db.commit()", 1)[1]
    assert "client." not in get_connection.split("await db.commit()", 1)[1]
    assert "client." not in test_connection.split("await db.commit()", 1)[1]
    assert "client." not in revoke_connection.split("await db.commit()", 1)[1]


def test_shopify_dynamic_purchase_checks_any_existing_order_before_insert():
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "webhook.py").read_text(encoding="utf-8")
    dynamic_block = source.split(
        "confirmed_check = await db.execute",
        1,
    )[1].split("if pending:", 1)[0]

    assert "PendingEvent.order_id.in_(possible_ids)" in dynamic_block
    assert "PendingEvent.status.in_" not in dynamic_block.split("existing_closed = confirmed_check.scalar_one_or_none()", 1)[0]
    assert "Purchase event was already processed with status" in dynamic_block


def test_woocommerce_webhook_secret_can_fallback_to_global_env(monkeypatch, caplog):
    from types import SimpleNamespace

    from app.routers import webhook

    client = SimpleNamespace(name="Demo Client", woocommerce_webhook_secret=None)

    caplog.set_level("WARNING", logger="app.routers.webhook")
    monkeypatch.setenv("WC_WEBHOOK_SECRET", "global-secret")
    monkeypatch.setattr(webhook, "ALLOW_WC_WEBHOOK_GLOBAL_SECRET_FALLBACK", True)

    assert webhook._woocommerce_webhook_secret_for_client(client) == "global-secret"
    assert "global WooCommerce webhook secret fallback" in caplog.text


def test_woocommerce_global_webhook_secret_fallback_is_disabled_by_default(monkeypatch):
    from types import SimpleNamespace

    from app.routers import webhook

    client = SimpleNamespace(name="Demo Client", woocommerce_webhook_secret=None)

    monkeypatch.setenv("WC_WEBHOOK_SECRET", "global-secret")
    monkeypatch.setattr(webhook, "ALLOW_WC_WEBHOOK_GLOBAL_SECRET_FALLBACK", False)

    assert webhook._woocommerce_webhook_secret_for_client(client) == ""


def test_woocommerce_webhook_uses_per_client_secret_and_shared_api_key_extractor():
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "webhook.py").read_text(encoding="utf-8")
    wc_auth_block = source.split("async def woocommerce_webhook", 1)[1].split(
        "try:\n        body = json.loads(raw_body)",
        1,
    )[0]

    assert '_client_api_key_from_request(request, provider="WooCommerce")' in wc_auth_block
    assert 'request.headers.get("x-api-key", "")' not in wc_auth_block
    assert "_woocommerce_webhook_secret_for_client(client)" in wc_auth_block
    assert "ALLOW_WC_WEBHOOK_GLOBAL_SECRET_FALLBACK" in source


def test_woocommerce_webhook_secret_is_modeled_and_migrated():
    root = Path(__file__).resolve().parents[1]
    model = (root / "app" / "models" / "client.py").read_text(encoding="utf-8")
    dependencies = (root / "app" / "dependencies.py").read_text(encoding="utf-8")
    migration = (
        root
        / "migrations"
        / "versions"
        / "aa1b2c3d4e5f_add_woocommerce_webhook_secret.py"
    ).read_text(encoding="utf-8")

    assert "woocommerce_webhook_secret = Column(String, nullable=True)" in model
    assert "woocommerce_webhook_secret: str | None = None" in dependencies
    assert "woocommerce_webhook_secret=getattr(client, 'woocommerce_webhook_secret', None)" in dependencies
    assert 'down_revision: Union[str, None] = "z9a8b7c6d5e4"' in migration
    assert 'op.add_column("clients", sa.Column("woocommerce_webhook_secret"' in migration


def test_proxy_ip_headers_are_ignored_unless_trusted(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.10")],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )

    monkeypatch.setattr(limiter_module, "TRUST_PROXY_HEADERS", False)
    assert limiter_module._get_real_ip(request) == "127.0.0.1"

    monkeypatch.setattr(limiter_module, "TRUST_PROXY_HEADERS", True)
    assert limiter_module._get_real_ip(request) == "203.0.113.10"


def test_production_proxy_headers_are_enabled_only_with_overwriting_proxy_config():
    root = Path(__file__).resolve().parents[1]
    setup_sh = (root / "deploy" / "setup.sh").read_text(encoding="utf-8")
    nginx_conf = (root / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")

    assert "TRUST_PROXY_HEADERS=true" in setup_sh
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx_conf
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" not in nginx_conf
    assert "overwrites X-Forwarded-For" in env_example
    assert "TRUST_PROXY_HEADERS=true" in env_example


def test_capi_signature_contract():
    body = b'{"data":[]}'
    api_key = "client-secret"
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signature = hmac.new(
        api_key.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()

    assert _verify_capi_signature(body, api_key, timestamp, signature)


def test_mask_secret_keeps_edges_only():
    masked = mask_secret("abcdef1234567890")
    assert masked.startswith("abcdef")
    assert masked.endswith("7890")
    assert "123456" not in masked


def test_pending_meta_credentials_are_not_configured():
    client = type("Client", (), {
        "pixel_id": "0",
        "access_token": encrypt_token("pending_setup"),
    })()

    assert not encrypted_credential_is_configured(client.access_token)
    assert not meta_credentials_configured(client)


def test_real_meta_credentials_are_configured():
    client = type("Client", (), {
        "pixel_id": "123456789",
        "access_token": encrypt_token("real-meta-token"),
    })()

    assert encrypted_credential_is_configured(client.access_token)
    assert meta_credentials_configured(client)


def test_plugin_update_signature_contract():
    version = "1.1.1"
    download_url = "https://example.com/api/v1/plugin/download"
    package_sha256 = hashlib.sha256(b"zip-bytes").hexdigest()
    api_key = "client-secret"
    payload = f"{version}|{download_url}|{package_sha256}"

    signature = hmac.new(api_key.encode(), payload.encode(), hashlib.sha256).hexdigest()

    assert hmac.compare_digest(
        signature,
        hmac.new(api_key.encode(), payload.encode(), hashlib.sha256).hexdigest(),
    )


def test_gateway_url_rejects_untrusted_host(monkeypatch):
    from app.routers import plugin

    monkeypatch.setattr(plugin, "PUBLIC_GATEWAY_BASE_URL", "")
    monkeypatch.setattr(plugin, "ALLOWED_GATEWAY_HOSTS", {"api.buykori.app"})
    request = _security_request(headers=[(b"host", b"evil.test")])

    try:
        plugin._build_gateway_url(request)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("Untrusted Host header was accepted")


def test_gateway_url_prefers_configured_public_base(monkeypatch):
    from app.routers import plugin

    monkeypatch.setattr(plugin, "PUBLIC_GATEWAY_BASE_URL", "https://gw.buykori.com")
    request = _security_request(headers=[(b"host", b"evil.test")])

    assert plugin._build_gateway_url(request) == "https://gw.buykori.com/api/v1"


def test_capi_scrub_removes_internal_fields_from_root_and_custom_data():
    from app.services.capi_service import scrub_internal_event_fields

    payload = {
        "event_name": "Purchase",
        "emq_score": 8.2,
        "raw_order_data": {"customer": "private"},
        "custom_data": {
            "value": 1200,
            "raw_order_data": {"line_items": []},
            "fraud_score": 90,
        },
    }

    scrub_internal_event_fields(payload)

    assert "emq_score" not in payload
    assert "raw_order_data" not in payload
    assert "raw_order_data" not in payload["custom_data"]
    assert "fraud_score" not in payload["custom_data"]


def test_capi_scrub_removes_empty_custom_data_after_internal_fields():
    from app.services.capi_service import scrub_internal_event_fields

    payload = {"event_name": "PageView", "custom_data": {"_enriched": True}}

    scrub_internal_event_fields(payload)

    assert "custom_data" not in payload


def test_auto_event_id_is_stable_without_time_bucket():
    from app.schemas.event import CustomData, EventData, UserData
    from app.services.event_quality import boost_event_quality

    first = EventData(
        event_name="InitiateCheckout",
        event_time=1710000000,
        event_source_url="https://shop.example/checkout",
        user_data=UserData(external_id=["visitor-1"]),
        custom_data=CustomData(content_ids=["sku-1"], value=500),
    )
    second = first.model_copy(deep=True)
    second.event_time = first.event_time + 31

    boost_event_quality(first)
    boost_event_quality(second)

    assert first.event_id == second.event_id
    assert first.event_id.startswith("bk_initiatecheckout_")


def test_emq_score_excludes_server_injected_ip_and_user_agent_by_default():
    from app.schemas.event import EventData, UserData
    from app.services.event_quality import calculate_emq_score

    event = EventData(
        event_name="PageView",
        event_time=1710000000,
        user_data=UserData(
            client_ip_address="203.0.113.10",
            client_user_agent="Mozilla/5.0",
        ),
    )

    assert calculate_emq_score(event) == 0.0
    assert calculate_emq_score(event, customer_provided_only=False) == 1.0


def test_bot_classifier_marks_short_user_agent_with_cookie_as_suspicious():
    from app.services.bot_detector import classify_traffic, is_bot

    assert classify_traffic("", has_cookie=True) == "suspicious"
    assert classify_traffic("", has_cookie=False) == "bot"
    assert is_bot("Googlebot/2.1") is True


def test_tracker_ingest_token_round_trip_and_tamper_reject():
    from types import SimpleNamespace

    from app.routers.tracker import _issue_tracker_ingest_token, _verify_tracker_ingest_token

    client = SimpleNamespace(id=1, api_key="private-api-key")
    token = _issue_tracker_ingest_token(client, "public-key")

    assert _verify_tracker_ingest_token(client, "public-key", token)
    assert not _verify_tracker_ingest_token(client, "other-public-key", token)
    assert not _verify_tracker_ingest_token(client, "public-key", token + "x")


def test_identity_phone_hash_matches_event_schema_phone_hash():
    from app.schemas.event import UserData
    from app.services.identity import hash_phone

    hashed = hash_phone("01837-224409")

    assert UserData(ph=["+8801837224409"]).ph == [hashed]


def test_shopify_plaintext_secret_fallback_is_disabled_by_default(monkeypatch):
    from app.routers import webhook

    monkeypatch.setattr(webhook, "ALLOW_SHOPIFY_PLAINTEXT_SHARED_SECRET", False)

    assert not webhook._verify_shopify_signature(b"{}", "bad", "plaintext-secret")


def test_dynamic_shopify_confirmed_pending_event_sets_portal_state():
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "webhook.py").read_text(encoding="utf-8")
    dynamic_block = source.split("# Save a confirmed PendingEvent to prevent duplicate queuing", 1)[1].split(
        "db.add(new_pending)",
        1,
    )[0]

    assert 'status="confirmed"' in dynamic_block
    assert 'portal_state="confirmed"' in dynamic_block
    assert "is_confirmed=True" in dynamic_block


def test_check_clients_masks_secrets_by_default():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "db" / "check_clients.py").read_text(encoding="utf-8")

    assert "def _mask_secret" in source
    assert "--show-secrets" in source
    assert "WARNING: showing full client secrets" in source
    assert "api_key = r[1] if show_secrets else _mask_secret(r[1])" in source
    assert "portal_key = r[2] if show_secrets else _mask_secret(r[2])" in source


def test_reset_stuck_outbox_preserves_attempts_by_default():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "db" / "reset_stuck_outbox.py").read_text(encoding="utf-8")

    assert "--reset-attempts" in source
    assert 'values = {"status": "queued", "locked_at": None, "locked_by": None}' in source
    assert 'values["attempts"] = 0' in source
    assert ".values(status='queued', locked_at=None, locked_by=None, attempts=0)" not in source


def test_portal_user_migration_does_not_print_passwords_to_stdout_by_default():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "keys"
        / "create_portal_users_for_existing_clients.py"
    ).read_text(encoding="utf-8")

    assert "os.open(csv_path, flags, 0o600)" in source
    assert "--csv is required with --apply" in source
    assert 'summary_fields = ["client_id", "client_name", "email"]' in source
    assert 'csv.DictWriter(sys.stdout, fieldnames=["client_id", "client_name", "email", "password"])' not in source


def test_soak_events_defaults_to_localhost_and_guards_production():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "testing" / "soak_events.py").read_text(encoding="utf-8")

    assert 'DEFAULT_URL = os.getenv("TEST_URL", "http://localhost:8000/api/v1/events")' in source
    assert 'PRODUCTION_HOSTS = {"api.buykori.app", "www.api.buykori.app"}' in source
    assert "--unsafe-production-ok" in source
    assert "Production URL detected" in source


def test_init_db_refuses_non_empty_database_without_force():
    source = (Path(__file__).resolve().parents[1] / "deploy" / "init_db.py").read_text(encoding="utf-8")

    assert "def existing_tables()" in source
    assert "Refusing to run create_all + alembic stamp on a non-empty database" in source
    assert "--force-non-empty" in source
    assert "Prefer alembic upgrade head instead" in source
    assert "create_tables(force_non_empty=args.force_non_empty)" in source


def test_alembic_migration_graph_has_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))

    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f"Expected one Alembic head, found: {heads}"


def test_old_api_key_auth_is_not_cached_past_rotation_grace():
    source = (Path(__file__).resolve().parents[1] / "app" / "dependencies.py").read_text(encoding="utf-8")

    assert "authenticated_with_old_key = False" in source
    assert "authenticated_with_old_key = True" in source
    assert "if cached.api_key != x_api_key:" in source
    assert "if not authenticated_with_old_key:" in source
    assert "set_in_client_cache(x_api_key, cached)" in source


@pytest.mark.anyio
async def test_client_cache_invalidation_uses_shared_redis_timestamp(monkeypatch):
    from app import dependencies

    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def set(self, key, value, ex=None):
            self.values[key] = value
            return True

        async def get(self, key):
            return self.values.get(key)

    redis = FakeRedis()
    monkeypatch.setattr(dependencies, "get_redis", lambda: redis)
    monkeypatch.setattr(dependencies.time, "time", lambda: 200.0)

    await dependencies.invalidate_client_cache("client-key")

    assert await dependencies._client_cache_invalidated("client-key", 199.0)
    assert not await dependencies._client_cache_invalidated("client-key", 201.0)


@pytest.mark.anyio
async def test_client_cache_invalidation_falls_back_to_local_clear_without_redis(monkeypatch):
    from types import SimpleNamespace

    from app import dependencies

    cached = SimpleNamespace(api_key="client-key", public_key="public-key")
    dependencies._client_cache["client-key"] = (cached, 100.0)
    dependencies._client_cache["public:public-key"] = (cached, 100.0)
    monkeypatch.setattr(dependencies, "get_redis", lambda: None)

    await dependencies.invalidate_client_cache("client-key")

    assert "client-key" not in dependencies._client_cache
    assert "public:public-key" not in dependencies._client_cache


def test_client_cache_mutation_paths_use_cross_worker_invalidation():
    root = Path(__file__).resolve().parents[1]
    router_files = [
        root / "app" / "routers" / name
        for name in (
            "admin_api.py",
            "admin_views.py",
            "client_api.py",
            "client_health.py",
            "client_portal.py",
            "courier_api.py",
        )
    ]

    for router_file in router_files:
        source = router_file.read_text(encoding="utf-8")
        assert "clear_client_cache" not in source
        assert "await invalidate_client_cache(" in source

    tracker_source = (root / "app" / "routers" / "tracker.py").read_text(encoding="utf-8")
    assert "await _client_cache_invalidated(cached.api_key, cached_at)" in tracker_source


def test_legacy_portal_cookie_does_not_fallback_to_api_key():
    source = (Path(__file__).resolve().parents[1] / "app" / "dependencies.py").read_text(encoding="utf-8")

    legacy_block = source.split('decrypted.startswith("client:")', 1)[1].split("else:", 1)[0]
    assert 'getattr(portal_client, "portal_key", None)' in legacy_block
    assert "elif not expected_secret" not in legacy_block
    assert "secrets.compare_digest(session_secret, portal_client.api_key)" not in legacy_block


@pytest.mark.anyio
async def test_geoip_missing_db_does_not_use_fallback_mirror_without_opt_in(tmp_path, monkeypatch):
    from app.services import geoip_service

    called = False

    async def fake_fallback(_tmp_path):
        nonlocal called
        called = True

    monkeypatch.setattr(geoip_service, "DB_PATH", str(tmp_path / "missing.mmdb"))
    monkeypatch.setattr(geoip_service, "MAXMIND_ACCOUNT_ID", "")
    monkeypatch.setattr(geoip_service, "MAXMIND_LICENSE_KEY", "")
    monkeypatch.setattr(geoip_service, "ALLOW_GEOIP_FALLBACK_MIRROR", False)
    monkeypatch.setattr(geoip_service, "_download_fallback_geoip_db", fake_fallback)

    await geoip_service.download_geoip_db_if_missing()

    assert called is False


@pytest.mark.anyio
async def test_geoip_fallback_mirror_requires_pinned_sha256(tmp_path, monkeypatch):
    from app.services import geoip_service

    monkeypatch.setattr(geoip_service, "GEOIP_FALLBACK_SHA256", "")

    with pytest.raises(RuntimeError, match="GEOIP_FALLBACK_SHA256"):
        await geoip_service._download_fallback_geoip_db(str(tmp_path / "fallback.mmdb"))


def test_domain_normalization():
    from app.utils.display import normalize_domain_input, display_domain_url

    assert normalize_domain_input("example.com") == "example.com"
    assert normalize_domain_input("https://www.example.com") == "example.com"
    assert normalize_domain_input("  www.example.com.  ") == "example.com"

    assert normalize_domain_input("example.com, google.com") == "example.com,google.com"
    assert normalize_domain_input("https://www.example.com, http://google.com") == "example.com,google.com"
    assert normalize_domain_input("  www.example.com. ,  ") == "example.com"
    assert normalize_domain_input(None) is None
    assert normalize_domain_input("   ") is None

    assert display_domain_url("example.com, google.com") == "https://www.example.com"
    assert display_domain_url("https://www.example.com") == "https://www.example.com"
    assert display_domain_url("") == ""
    assert display_domain_url(None) == ""
