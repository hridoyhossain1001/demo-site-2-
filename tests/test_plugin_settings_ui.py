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
