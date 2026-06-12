import os
from datetime import date
from unittest.mock import MagicMock, AsyncMock
import pytest
import httpx

# Ensure environment variables are set before importing app components
os.environ.setdefault("ENCRYPTION_KEY", "ZFhnf1szwemka8kBbH9jPTC7oKBRTEv0EqWt1J8AD0M=")

from app.security import encrypt_token
from app.services.ads.meta_ads_service import fetch_meta_daily_insights
from app.services.ads.tiktok_ads_service import fetch_tiktok_daily_insights

@pytest.mark.asyncio
async def test_fetch_meta_daily_insights_success(monkeypatch):
    # Prepare encrypted token
    raw_token = "mock-meta-access-token"
    enc_token = encrypt_token(raw_token)

    # Mock Response Data from Meta Graph API
    meta_response_data = {
        "data": [
            {
                "campaign_id": "camp123",
                "campaign_name": "Meta Campaign 1",
                "date_start": "2026-06-01",
                "spend": "150.50",
                "impressions": "10000",
                "clicks": "250",
                "actions": [
                    {"action_type": "purchase", "value": "5"}
                ],
                "action_values": [
                    {"action_type": "purchase", "value": "600.00"}
                ]
            }
        ]
    }

    # Create mock response object
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = meta_response_data

    # Mock AsyncClient.get
    mock_get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_meta_daily_insights(
        access_token_enc=enc_token,
        act_id="act_123456789",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2)
    )

    # Assert get was called with correct parameters
    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert "insights" in args[0]
    assert kwargs["params"]["access_token"] == raw_token
    assert kwargs["params"]["level"] == "campaign"

    # Assert parsed insights structure
    assert len(results) == 1
    insight = results[0]
    assert insight["campaign_id"] == "camp123"
    assert insight["campaign_name"] == "Meta Campaign 1"
    assert insight["date"] == date(2026, 6, 1)
    assert insight["spend"] == 150.50
    assert insight["impressions"] == 10000
    assert insight["clicks"] == 250
    assert insight["purchases"] == 5
    assert insight["revenue"] == 600.00


@pytest.mark.asyncio
async def test_fetch_meta_daily_insights_rate_limit(monkeypatch):
    raw_token = "mock-meta-access-token"
    enc_token = encrypt_token(raw_token)

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "request limit reached"

    mock_get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_meta_daily_insights(
        access_token_enc=enc_token,
        act_id="act_123456789",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2)
    )

    assert results == []


@pytest.mark.asyncio
async def test_fetch_meta_daily_insights_paginates(monkeypatch):
    raw_token = "mock-meta-access-token"
    enc_token = encrypt_token(raw_token)

    first_response = MagicMock()
    first_response.status_code = 200
    first_response.json.return_value = {
        "data": [
            {
                "campaign_id": "camp123",
                "campaign_name": "Meta Campaign 1",
                "date_start": "2026-06-01",
                "spend": "100.00",
                "impressions": "1000",
                "clicks": "50",
            }
        ],
        "paging": {"next": "https://graph.facebook.com/v20.0/next-page"}
    }

    second_response = MagicMock()
    second_response.status_code = 200
    second_response.json.return_value = {
        "data": [
            {
                "campaign_id": "camp456",
                "campaign_name": "Meta Campaign 2",
                "date_start": "2026-06-02",
                "spend": "200.00",
                "impressions": "2000",
                "clicks": "80",
            }
        ]
    }

    mock_get = AsyncMock(side_effect=[first_response, second_response])
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_meta_daily_insights(
        access_token_enc=enc_token,
        act_id="act_123456789",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2)
    )

    assert len(results) == 2
    assert results[0]["campaign_id"] == "camp123"
    assert results[1]["campaign_id"] == "camp456"
    assert mock_get.call_count == 2
    second_args, second_kwargs = mock_get.call_args_list[1]
    assert second_args[0] == "https://graph.facebook.com/v20.0/next-page"
    assert second_kwargs["params"] is None


@pytest.mark.asyncio
async def test_fetch_tiktok_daily_insights_success(monkeypatch):
    raw_token = "mock-tiktok-access-token"
    enc_token = encrypt_token(raw_token)

    # Mock Response Data from TikTok Business API
    tiktok_response_data = {
        "code": 0,
        "message": "OK",
        "data": {
            "list": [
                {
                    "metrics": {
                        "spend": "85.20",
                        "clicks": "120",
                        "impressions": "5000",
                        "conversion": "3",
                        "conversion_value": "240.00"
                    },
                    "dimensions": {
                        "campaign_id": "camp999",
                        "campaign_name": "TikTok Campaign 1",
                        "stat_time_day": "2026-06-01 00:00:00"
                    }
                }
            ]
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = tiktok_response_data

    mock_get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_tiktok_daily_insights(
        access_token_enc=enc_token,
        advertiser_id="adv_77777",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2)
    )

    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert kwargs["headers"]["Access-Token"] == raw_token
    assert kwargs["params"]["advertiser_id"] == "adv_77777"

    assert len(results) == 1
    insight = results[0]
    assert insight["campaign_id"] == "camp999"
    assert insight["campaign_name"] == "TikTok Campaign 1"
    assert insight["date"] == date(2026, 6, 1)
    assert insight["spend"] == 85.20
    assert insight["impressions"] == 5000
    assert insight["clicks"] == 120
    assert insight["purchases"] == 3
    assert insight["revenue"] == 240.00


@pytest.mark.asyncio
async def test_fetch_tiktok_daily_insights_error(monkeypatch):
    raw_token = "mock-tiktok-access-token"
    enc_token = encrypt_token(raw_token)

    tiktok_response_data = {
        "code": 40001,
        "message": "Invalid access token"
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = tiktok_response_data

    mock_get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_tiktok_daily_insights(
        access_token_enc=enc_token,
        advertiser_id="adv_77777",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2)
    )

    assert results == []


@pytest.mark.asyncio
async def test_fetch_tiktok_daily_insights_paginates(monkeypatch):
    raw_token = "mock-tiktok-access-token"
    enc_token = encrypt_token(raw_token)

    first_response = MagicMock()
    first_response.status_code = 200
    first_response.json.return_value = {
        "code": 0,
        "message": "OK",
        "data": {
            "page_info": {"total_page": 2},
            "list": [
                {
                    "metrics": {
                        "spend": "50.00",
                        "clicks": "20",
                        "impressions": "1000",
                        "conversion": "1",
                        "conversion_value": "120.00"
                    },
                    "dimensions": {
                        "campaign_id": "camp111",
                        "campaign_name": "TikTok Campaign 1",
                        "stat_time_day": "2026-06-01 00:00:00"
                    }
                }
            ]
        }
    }

    second_response = MagicMock()
    second_response.status_code = 200
    second_response.json.return_value = {
        "code": 0,
        "message": "OK",
        "data": {
            "page_info": {"total_page": 2},
            "list": [
                {
                    "metrics": {
                        "spend": "75.00",
                        "clicks": "30",
                        "impressions": "1500",
                        "conversion": "2",
                        "conversion_value": "260.00"
                    },
                    "dimensions": {
                        "campaign_id": "camp222",
                        "campaign_name": "TikTok Campaign 2",
                        "stat_time_day": "2026-06-02 00:00:00"
                    }
                }
            ]
        }
    }

    mock_get = AsyncMock(side_effect=[first_response, second_response])
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_tiktok_daily_insights(
        access_token_enc=enc_token,
        advertiser_id="adv_77777",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2)
    )

    assert len(results) == 2
    assert results[0]["campaign_id"] == "camp111"
    assert results[1]["campaign_id"] == "camp222"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].kwargs["params"]["page"] == 1
    assert mock_get.call_args_list[1].kwargs["params"]["page"] == 2
