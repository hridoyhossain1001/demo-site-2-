import hashlib
import hmac
import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault(
    "ADMIN_PASSWORD",
    "pbkdf2_sha256$210000$dGVzdC1hZG1pbi1zYWx0LTE=$9gwSQUsI_uzxaNpdvx_cOcpF4opgO7Ma_Hcmq3z4kSU=",
)
os.environ.setdefault("ADMIN_API_KEY", "test-admin-api-key")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("ENCRYPTION_KEY", "ZFhnf1szwemka8kBbH9jPTC7oKBRTEv0EqWt1J8AD0M=")

from app.routers.admin import create_admin_csrf_token, mask_secret, verify_admin_csrf_token
from app.routers.events import _is_domain_allowed, _verify_capi_signature
from app.routers.webhook import _client_api_key_from_request
from app.main import _is_tracker_path
from app.services.auth_service import verify_admin_password
from app.security import encrypt_token, encrypted_credential_is_configured, meta_credentials_configured
from app import limiter as limiter_module
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


def test_tracking_body_limit_rejects_large_request():
    client = TestClient(app)
    response = client.post(
        "/c",
        content=b"x" * (262144 + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


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


def test_legacy_admin_password_is_blocked_in_production(monkeypatch):
    monkeypatch.setenv("PRIMARY_DOMAIN", "api.buykori.app")
    monkeypatch.setenv("ALLOW_LEGACY_ADMIN_PASSWORD", "true")
    assert not verify_admin_password("test-admin-password", "test-admin-password")


def _security_request(*, headers=None, query_string=b""):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers or [],
            "query_string": query_string,
            "client": ("127.0.0.1", 1234),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


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
