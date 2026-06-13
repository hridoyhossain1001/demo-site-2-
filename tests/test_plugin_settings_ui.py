from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]


def test_plugin_settings_ui_is_client_focused():
    settings_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "admin-settings.php"
    ).read_text(encoding="utf-8")

    assert "Connected tracking for WooCommerce stores" in settings_php
    assert "Switch Buykori Account" in settings_php
    assert "Run Health Check" in settings_php
    assert "Essential Events" in settings_php
    assert "Browser Pixel Backup" in settings_php
    assert "<summary>Pixel backup settings</summary>" in settings_php
    assert "Support Tools" in settings_php
    assert "Write extra troubleshooting logs" in settings_php
    assert "Plugin Update Status" in settings_php
    assert "Refresh Update Status" in settings_php
    assert "Use this only when the latest Buykori AdSync version is not appearing" in settings_php
    assert "Debug & Logging" not in settings_php
    assert "name=\"<?php echo BUYKORIGW_OPTION_KEY; ?>[tracking_mode]\" value=\"auto\"" in settings_php
    assert "name=\"<?php echo BUYKORIGW_OPTION_KEY; ?>[enable_variations]\" value=\"0\"" in settings_php


def test_optional_event_defaults_policy_keeps_only_recommended_events_on():
    main_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "buykori-adsync.php"
    ).read_text(encoding="utf-8")

    assert "BUYKORIGW_OPTIONAL_EVENTS_POLICY_VERSION" in main_php
    for event_key in (
        "enable_lead",
        "enable_search",
        "enable_viewcart",
        "enable_removefromcart",
        "enable_addpaymentinfo",
    ):
        assert f"'{event_key}'," in main_php
    for event_key in (
        "'enable_pageview' => 1",
        "'enable_viewcontent' => 1",
        "'enable_addtocart' => 1",
        "'enable_checkout' => 1",
        "'enable_purchase' => 1",
    ):
        assert event_key in main_php


def test_purchase_events_are_retryable_after_gateway_send_failure():
    main_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "buykori-adsync.php"
    ).read_text(encoding="utf-8")

    retryable_block = main_php.split("function buykorigw_retryable_event_name", 1)[1].split(
        "function buykorigw_retry_event_key", 1
    )[0]

    assert "'Purchase'," in retryable_block


def test_deferred_purchase_send_uses_retryable_hold_path():
    main_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "buykori-adsync.php"
    ).read_text(encoding="utf-8")
    frontend_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "frontend-tracking.php"
    ).read_text(encoding="utf-8")

    assert "function buykorigw_schedule_event_retry($event_data, $attempt = 1, $hold = false)" in main_php
    assert "'hold'       => (bool) $hold," in main_php
    assert "buykorigw_send_event($item['event_data'], true, false, !empty($item['hold']))" in main_php
    assert "$url = rtrim($settings['gateway_url'], '/') . '/events' . ($hold ? '?hold=true' : '');" in main_php
    assert "$sent = buykorigw_send_event( $event_payload, true, true, true );" in frontend_php


def test_critical_ajax_tracking_events_have_finite_rate_limits():
    frontend_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "frontend-tracking.php"
    ).read_text(encoding="utf-8")

    rate_limit_block = frontend_php.split("function buykorigw_ajax_rate_limited", 1)[1].split(
        "function buykorigw_purchase_lock_key", 1
    )[0]

    assert "'Purchase', 'InitiateCheckout', 'AddPaymentInfo', 'Refund'" in rate_limit_block
    assert "return false;" not in rate_limit_block.split("$visitor_id", 1)[0]
    assert "$is_critical_event" in rate_limit_block
    assert "$event_key  = sanitize_key( $event_name ?: 'event' );" in rate_limit_block
    assert "'buykorigw_ajax_rate_' . $scope . '_' . $event_key" in rate_limit_block


def test_forwarded_client_ip_headers_require_trusted_proxy_gate():
    frontend_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "frontend-tracking.php"
    ).read_text(encoding="utf-8")

    assert "function buykorigw_ip_in_cidr" in frontend_php
    assert "function buykorigw_get_trusted_proxy_cidrs" in frontend_php
    assert "buykorigw_trusted_proxy_cidrs" in frontend_php
    assert "function buykorigw_remote_addr_is_trusted_proxy" in frontend_php

    real_ip_block = frontend_php.split("function buykorigw_get_real_ip", 1)[1]
    forwarded_header_block = real_ip_block.split("$headers = array(", 1)[0]

    assert "buykorigw_remote_addr_is_trusted_proxy( $remote_addr )" in real_ip_block
    assert "HTTP_X_FORWARDED_FOR" not in forwarded_header_block
    assert "return $remote_addr;" in real_ip_block


def test_identity_hash_cookies_are_disabled_by_default():
    main_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "buykori-adsync.php"
    ).read_text(encoding="utf-8")

    identity_block = main_php.split("function buykorigw_identity_cookie_cache_enabled", 1)[1].split(
        "function buykorigw_hash_identity_field",
        1,
    )[0]

    assert "buykorigw_enable_identity_hash_cookies" in identity_block
    assert "return (bool) apply_filters('buykorigw_enable_identity_hash_cookies', false);" in identity_block
    assert "function buykorigw_clear_identity_cookie" in identity_block
    assert "if (!buykorigw_identity_cookie_cache_enabled())" in identity_block
    assert "buykorigw_clear_identity_cookie($field);" in identity_block
    assert "buykorigw_identity_hash_cookie_days" in identity_block
    assert "max(1, min(30, $retention_days))" in identity_block
    assert "180 * DAY_IN_SECONDS" not in identity_block


def test_gateway_response_bodies_are_not_exposed_in_plugin_diagnostics():
    main_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "buykori-adsync.php"
    ).read_text(encoding="utf-8")
    order_hooks_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "woo-order-hooks.php"
    ).read_text(encoding="utf-8")

    last_error_block = main_php.split("function buykorigw_set_last_event_error", 1)[1].split(
        "function buykorigw_get_last_event_error",
        1,
    )[0]
    rest_error_block = main_php.split("$response['gateway_error']", 1)[1].split(
        "return new WP_REST_Response($response, 502);",
        1,
    )[0]

    assert "'body'" not in last_error_block
    assert "'body'" not in rest_error_block
    assert "Send event HTTP ' . $code . ': ' . wp_remote_retrieve_body" not in main_php
    assert 'Confirm HTTP $code for order #$order_id: " . wp_json_encode( $body )' not in order_hooks_php
    assert 'Cancel HTTP $code for order #$order_id: " . wp_json_encode( $body )' not in order_hooks_php


def test_order_status_hooks_claim_one_confirm_attempt_per_request():
    order_hooks_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "woo-order-hooks.php"
    ).read_text(encoding="utf-8")

    claim_block = order_hooks_php.split("function buykorigw_claim_confirm_attempt", 1)[1].split(
        "function buykorigw_on_order_status_change",
        1,
    )[0]
    status_change_block = order_hooks_php.split("function buykorigw_on_order_status_change", 1)[1].split(
        "function buykorigw_on_order_cancelled",
        1,
    )[0]

    assert "static $attempted = array();" in claim_block
    assert "buykorigw_normalize_order_status_slug( $status )" in claim_block
    assert "if ( isset( $attempted[ $key ] ) )" in claim_block
    assert "! buykorigw_claim_confirm_attempt( $order_id, $current_status )" in status_change_block
    assert status_change_block.index("buykorigw_claim_confirm_attempt") < status_change_block.index(
        "buykorigw_confirm_order"
    )


def test_deferred_purchase_403_is_terminal_and_not_retried():
    main_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "buykori-adsync.php"
    ).read_text(encoding="utf-8")
    frontend_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "frontend-tracking.php"
    ).read_text(encoding="utf-8")

    send_block = main_php.split("function buykorigw_send_event", 1)[1].split(
        "function buykorigw_hash",
        1,
    )[0]
    retry_block = main_php.split("function buykorigw_retry_event_handler", 1)[1].split(
        "function buykorigw_send_event",
        1,
    )[0]

    assert "if ($hold && $code === 403)" in send_block
    assert "Deferred Purchase configuration mismatch (HTTP 403); retry skipped." in send_block
    assert "true" in send_block.split("if ($hold && $code === 403)", 1)[1].split("return false;", 1)[0]
    assert "if (!empty($last_error['terminal']))" in retry_block
    assert "delete_option($retry_key);" in retry_block
    assert "$failure_status = 'deferred_configuration_mismatch';" in frontend_php


def test_public_rest_tracking_requires_signed_browser_token():
    main_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "buykori-adsync.php"
    ).read_text(encoding="utf-8")
    frontend_php = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "includes"
        / "frontend-tracking.php"
    ).read_text(encoding="utf-8")
    tracker_js = (
        WORKSPACE
        / "wordpress-plugin"
        / "buykori-adsync"
        / "assets"
        / "js"
        / "tracker.js"
    ).read_text(encoding="utf-8")

    assert "function buykorigw_create_rest_browser_token" in main_php
    assert "function buykorigw_verify_rest_browser_token" in main_php
    assert "function buykorigw_rest_browser_token_valid(WP_REST_Request $request)" in main_php
    assert "$token = $request->get_header('x-buykori-browser-token');" in main_php
    assert "$request->get_param('_buykori_browser_token')" in main_php
    assert "'browser_token' => function_exists( 'buykorigw_create_rest_browser_token' )" in frontend_php
    assert "X-Buykori-Browser-Token" in tracker_js
    assert "_buykori_browser_token" in tracker_js

    track_block = main_php.split("function buykorigw_rest_track_event", 1)[1].split(
        "$params     = $request->get_json_params();",
        1,
    )[0]
    assert "$browser_token_valid = buykorigw_rest_browser_token_valid($request);" in track_block
    assert "if (!$nonce_valid && !$browser_token_valid)" in track_block
    assert "Missing or invalid browser token" in track_block

    for function_name in (
        "buykorigw_rest_browser_pixel_audit",
        "buykorigw_rest_capture_incomplete_checkout",
        "buykorigw_rest_get_atc_receipts",
        "buykorigw_rest_ack_atc_receipts",
    ):
        block = main_php.split(f"function {function_name}", 1)[1].split("\n}", 1)[0]
        assert "buykorigw_rest_same_origin_allowed()" in block
        assert "buykorigw_rest_browser_token_valid($request)" in block
