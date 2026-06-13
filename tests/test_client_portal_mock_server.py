from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]


def test_client_portal_mock_server_is_guarded_from_production_startup():
    server_ts = (WORKSPACE / "client-portal" / "server.ts").read_text(encoding="utf-8")

    assert "BUYKORI_ALLOW_MOCK_SERVER_PRODUCTION" in server_ts
    assert "assertMockServerMayStart();" in server_ts
    assert "must not run in production" in server_ts
    assert 'app.listen(PORT, HOST' in server_ts
    assert 'isProductionRuntime ? "127.0.0.1" : "0.0.0.0"' in server_ts


def test_client_portal_mock_credentials_are_non_provider_placeholders():
    server_ts = (WORKSPACE / "client-portal" / "server.ts").read_text(encoding="utf-8")

    assert "mock_meta_access_token" in server_ts
    assert "mock_tiktok_access_token" in server_ts
    assert "mock_ga4_api_secret" in server_ts
    assert "EAAD" not in server_ts
    assert "tt_ac_tkn" not in server_ts
    assert "secret_mp_token" not in server_ts


def test_client_portal_mock_covers_frontend_called_contract_routes():
    server_ts = (WORKSPACE / "client-portal" / "server.ts").read_text(encoding="utf-8")

    for method, route in (
        ("get", "/api/v1/ad-campaigns"),
        ("get", "/api/v1/ad-accounts"),
        ("post", "/api/v1/ad-accounts"),
        ("delete", "/api/v1/ad-accounts/:id"),
        ("get", "/api/v1/analytics/ad-performance"),
        ("get", "/api/events/recovery-summary"),
        ("post", "/api/guide/dismiss"),
        ("post", "/api/v1/auth/client/logout"),
    ):
        assert f'app.{method}("{route}"' in server_ts


def test_print_windows_use_sanitized_cloned_markup():
    courier_modal = (
        WORKSPACE / "client-portal" / "src" / "components" / "CourierLabelModal.tsx"
    ).read_text(encoding="utf-8")
    invoice_modal = (
        WORKSPACE / "client-portal" / "src" / "components" / "InvoiceModal.tsx"
    ).read_text(encoding="utf-8")
    print_helper = (WORKSPACE / "client-portal" / "src" / "lib" / "print.ts").read_text(encoding="utf-8")

    assert "clonePrintMarkup('.print-courier-label-area')" in courier_modal
    assert "clonePrintMarkup('.print-invoice-area')" in invoice_modal
    assert ".innerHTML" not in courier_modal
    assert ".innerHTML" not in invoice_modal
    assert "name.startsWith('on')" in print_helper
    assert "value.startsWith('javascript:')" in print_helper
