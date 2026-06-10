import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from app.models.ad_account import AdAccount
from app.models.ad_campaign import AdCampaign
from app.models.ad_insight_daily import AdInsightDaily
from app.services.ad_sync_worker import sync_ad_account_insights

class _Result:
    def __init__(self, scalar=None):
        self.scalar = scalar

    def scalar_one_or_none(self):
        return self.scalar

class _Db:
    def __init__(self, campaign_result=None, insight_result=None):
        self.campaign_result = campaign_result
        self.insight_result = insight_result
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt):
        stmt_str = str(stmt)
        if "ad_campaigns" in stmt_str:
            return _Result(self.campaign_result)
        elif "ad_insights_daily" in stmt_str:
            return _Result(self.insight_result)
        return _Result(None)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

@pytest.mark.anyio
async def test_sync_ad_account_insights_meta_success(monkeypatch):
    account = AdAccount(
        id=1,
        client_id=10,
        platform="meta",
        external_account_id="act_123",
        access_token_enc="enc_token",
        account_currency="USD",
        is_active=True
    )

    mock_fetch = AsyncMock(return_value=[
        {
            "campaign_id": "camp123",
            "campaign_name": "Campaign One",
            "date": date(2026, 6, 1),
            "spend": 100.0,
            "impressions": 5000,
            "clicks": 150,
            "purchases": 2,
            "revenue": 300.0
        }
    ])
    monkeypatch.setattr("app.services.ad_sync_worker.fetch_meta_daily_insights", mock_fetch)

    db = _Db(campaign_result=None, insight_result=None)

    await sync_ad_account_insights(db, account)

    assert len(db.added) == 2

    campaign = next(x for x in db.added if isinstance(x, AdCampaign))
    assert campaign.ad_account_id == 1
    assert campaign.platform == "meta"
    assert campaign.external_campaign_id == "camp123"
    assert campaign.name == "Campaign One"

    insight = next(x for x in db.added if isinstance(x, AdInsightDaily))
    assert insight.client_id == 10
    assert insight.platform == "meta"
    assert insight.external_campaign_id == "camp123"
    assert insight.insight_date == date(2026, 6, 1)
    assert insight.spend == 100.0
    assert insight.impressions == 5000
    assert insight.clicks == 150
    assert insight.platform_purchases == 2
    assert insight.platform_revenue == 300.0
    assert insight.currency == "USD"

    assert db.committed is True
    assert db.rolled_back is False

@pytest.mark.anyio
async def test_sync_ad_account_insights_meta_existing_updates(monkeypatch):
    account = AdAccount(
        id=1,
        client_id=10,
        platform="meta",
        external_account_id="act_123",
        access_token_enc="enc_token",
        account_currency="USD",
        is_active=True
    )

    mock_fetch = AsyncMock(return_value=[
        {
            "campaign_id": "camp123",
            "campaign_name": "New Campaign Name",
            "date": date(2026, 6, 1),
            "spend": 120.0,
            "impressions": 6000,
            "clicks": 180,
            "purchases": 3,
            "revenue": 400.0
        }
    ])
    monkeypatch.setattr("app.services.ad_sync_worker.fetch_meta_daily_insights", mock_fetch)

    existing_campaign = AdCampaign(
        id=2,
        ad_account_id=1,
        platform="meta",
        external_campaign_id="camp123",
        name="Old Campaign Name"
    )
    existing_insight = AdInsightDaily(
        id=3,
        client_id=10,
        platform="meta",
        external_campaign_id="camp123",
        insight_date=date(2026, 6, 1),
        spend=50.0
    )

    db = _Db(campaign_result=existing_campaign, insight_result=existing_insight)

    await sync_ad_account_insights(db, account)

    assert len(db.added) == 0
    assert existing_campaign.name == "New Campaign Name"
    assert existing_insight.spend == 120.0
    assert existing_insight.impressions == 6000
    assert existing_insight.clicks == 180
    assert existing_insight.platform_purchases == 3
    assert existing_insight.platform_revenue == 400.0

    assert db.committed is True
    assert db.rolled_back is False
