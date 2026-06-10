from app.services.tracker_sdk import generate_tracker_js


def test_tracker_supports_explicit_event_ids_and_pageview_deduplication():
    script = generate_tracker_js("public_key_123", "https://api.example.com")

    assert "event_id:eventId||uid()" in script
    assert "var T=\"\";" in script
    assert "if(lastPageViewUrl===location.href){" in script
    assert "send(q[0],q[1],q[2],q[3]);" in script
    assert "var D=qp('buykori_debug')==='1';" in script
    assert "Suppressed duplicate PageView" in script


def test_tracker_keeps_legacy_fourth_argument_as_user_data():
    script = generate_tracker_js("public_key_123", "https://api.example.com")

    assert "var ud=opts?(opts.userData||opts.user_data||null):fourth;" in script


def test_tracker_includes_short_lived_ingest_token_when_supplied():
    script = generate_tracker_js("public_key_123", "https://api.example.com", ingest_token="token.123")

    assert "var T=\"token.123\";" in script
    assert "token='+encodeURIComponent(T)" in script
