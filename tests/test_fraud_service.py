import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from types import SimpleNamespace

from app.schemas.event import EventData, UserData, CustomData
from app.routers.events import _strip_internal_custom_data
from app.services.fraud_service import (
    is_disposable_email,
    is_gibberish,
    check_ip_location_mismatch,
    check_velocity,
    calculate_fraud_score,
    should_auto_hold_for_fraud,
)


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _Scalars(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return _Result(self.rows)

def test_disposable_email_detection():
    # Valid domains
    assert is_disposable_email("gmail.com") is False
    assert is_disposable_email("yahoo.com") is False
    assert is_disposable_email("outlook.com") is False
    
    # Disposable domains
    assert is_disposable_email("tempmail.com") is True
    assert is_disposable_email("temp-mail.org") is True
    assert is_disposable_email("mailinator.com") is True
    assert is_disposable_email("yopmail.com") is True


def test_gibberish_name_detection():
    # Real names (should not be flagged)
    assert is_gibberish("Hridoy Hossain") is False
    assert is_gibberish("Abul Kalam") is False
    assert is_gibberish("Karim") is False
    
    # Empty or None
    assert is_gibberish("") is False
    assert is_gibberish(None) is False
    
    # Gibberish/Spam patterns (should be flagged)
    assert is_gibberish("asdfgh") is True
    assert is_gibberish("qwerty") is True
    assert is_gibberish("1234567") is True
    assert is_gibberish("aaaaaa") is True
    assert is_gibberish("bcdfgh") is True  # Consonants only


def test_fraud_auto_hold_policy(monkeypatch):
    from app.services import fraud_service

    monkeypatch.setattr(fraud_service, "FRAUD_AUTO_HOLD_THRESHOLD", 90)

    assert should_auto_hold_for_fraud(89) is False
    assert should_auto_hold_for_fraud(90) is True
    assert should_auto_hold_for_fraud(100) is True

    monkeypatch.setattr(fraud_service, "FRAUD_AUTO_HOLD_THRESHOLD", 0)
    assert should_auto_hold_for_fraud(100) is False


def test_events_route_auto_holds_high_risk_purchase():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "events.py").read_text(encoding="utf-8")

    assert "should_auto_hold_for_fraud(fraud_result[0])" in source
    assert 'fraud_portal_state = "fraud_review"' in source


@patch("app.services.fraud_service.get_location_data")
def test_ip_location_mismatch(mock_geoip):
    # Mock GeoIP response
    mock_geoip.return_value = {"country": "bd", "ct": "Dhaka"}
    
    # Case 1: Match (Country BD)
    # The Pydantic model hashes parameters automatically, so we pass raw
    ud_match = UserData(country=["bd"])
    assert check_ip_location_mismatch("103.100.100.1", ud_match) is False
    
    # Case 2: Mismatch (Country US)
    ud_mismatch = UserData(country=["us"])
    assert check_ip_location_mismatch("103.100.100.1", ud_mismatch) is True
    
    # Case 3: Empty/Missing country in event
    ud_empty = UserData(country=[])
    assert check_ip_location_mismatch("103.100.100.1", ud_empty) is False


@pytest.mark.anyio
async def test_velocity_flags_third_matching_order_in_window():
    rows = [
        SimpleNamespace(
            event_data={
                "user_data": {
                    "client_ip_address": "103.100.100.1",
                    "ph": ["phone-hash"],
                }
            },
            created_at=datetime.now(timezone.utc),
        ),
        SimpleNamespace(
            event_data={
                "user_data": {
                    "client_ip_address": "103.100.100.1",
                    "ph": ["phone-hash"],
                }
            },
            created_at=datetime.now(timezone.utc),
        ),
    ]

    assert await check_velocity(_Db(rows), 1, "103.100.100.1", ["phone-hash"]) is True


def test_strip_internal_custom_data_removes_fraud_only_fields():
    event_dict = {
        "custom_data": {
            "value": 100,
            "currency": "BDT",
            "billing_email_domain": "tempmail.com",
            "billing_first_name_raw": "qwerty",
        }
    }

    cleaned = _strip_internal_custom_data(event_dict)

    assert cleaned["custom_data"] == {"value": 100, "currency": "BDT"}


@pytest.mark.anyio
@patch("app.services.fraud_service.get_location_data")
@patch("app.services.fraud_service.check_velocity")
async def test_calculate_fraud_score(mock_velocity, mock_geoip):
    # Mocking external calls
    mock_geoip.return_value = {"country": "bd", "ct": "Dhaka"}
    mock_velocity.return_value = False  # No velocity limit triggered
    
    # ─── Mock DB Session ───
    db = MagicMock()
    
    # Case 1: Ideal clean order (Score = 0)
    event_clean = EventData(
        event_name="Purchase",
        event_time=1700000000,
        user_data=UserData(country=["bd"]),
        custom_data=CustomData(
            raw_first_name="Hridoy",
            email_domain="gmail.com"
        )
    )
    score, details = await calculate_fraud_score(db, 1, event_clean, "103.100.100.1")
    assert score == 0
    assert not any(details.values())

    # Case 2: Multi-heuristic trigger (Score = 25 + 30 + 20 = 75)
    # Mismatched Country (US), Disposable Email, Gibberish Name
    event_fraud = EventData(
        event_name="Purchase",
        event_time=1700000000,
        user_data=UserData(country=["us"]),
        custom_data=CustomData(
            raw_first_name="qwerty",
            email_domain="tempmail.com"
        )
    )
    score, details = await calculate_fraud_score(db, 1, event_fraud, "103.100.100.1")
    assert score == 75
    assert details["ip_mismatch"] is True
    assert details["disposable_email"] is True
    assert details["gibberish_name"] is True
    assert details["velocity_limit"] is False
