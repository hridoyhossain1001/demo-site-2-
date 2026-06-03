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
    assert "Debug & Logging" not in settings_php
    assert "name=\"<?php echo BUYKORIGW_OPTION_KEY; ?>[tracking_mode]\" value=\"auto\"" in settings_php
    assert "name=\"<?php echo BUYKORIGW_OPTION_KEY; ?>[enable_variations]\" value=\"1\"" in settings_php
