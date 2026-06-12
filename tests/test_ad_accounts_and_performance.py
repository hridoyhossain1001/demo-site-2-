import os
from datetime import datetime, date, timezone
from types import SimpleNamespace
import pytest

# Set encryption key before imports
os.environ.setdefault("ENCRYPTION_KEY", "ZFhnf1szwemka8kBbH9jPTC7oKBRTEv0EqWt1J8AD0M=")

from app.security import decrypt_token
from app.models.ad_account import AdAccount
from app.routers.ad_accounts import (
    AdAccountCreate,
    connect_ad_account,
    list_ad_accounts,
    disconnect_ad_account
)
from app.routers.analytics import ad_performance_analytics


class MockResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else [self._value]

    def fetchall(self):
        return self._value if isinstance(self._value, list) else [self._value]


class MockDb:
    def __init__(self, query_result=None, dialect_name="sqlite"):
        self.query_result = query_result
        self.added = []
        self.deleted = []
        self.committed = False
        self.rolled_back = False
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
        self.executed_statements = []

    async def execute(self, statement, *args, **kwargs):
        self.executed_statements.append(str(statement))
        return MockResult(self.query_result)

    def add(self, entity):
        self.added.append(entity)

    async def delete(self, entity):
        self.deleted.append(entity)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, entity):
        # Assign mock ID if not set
        if getattr(entity, "id", None) is None:
            entity.id = 1
        if getattr(entity, "created_at", None) is None:
            entity.created_at = datetime.now(timezone.utc)
        if getattr(entity, "updated_at", None) is None:
            entity.updated_at = datetime.now(timezone.utc)


@pytest.mark.anyio
async def test_connect_ad_account_create_meta():
    db = MockDb(query_result=None)
    client = SimpleNamespace(id=42)
    body = AdAccountCreate(
        platform="meta",
        external_account_id="act_12345",
        account_name="Meta Test Account",
        access_token="plain-access-token",
        refresh_token=None,
        account_currency="USD",
        account_timezone="UTC"
    )

    response = await connect_ad_account(body, client=client, db=db)

    assert db.committed is True
    assert len(db.added) == 1
    new_account = db.added[0]
    assert new_account.client_id == 42
    assert new_account.platform == "meta"
    assert new_account.external_account_id == "act_12345"
    assert new_account.account_name == "Meta Test Account"
    assert decrypt_token(new_account.access_token_enc) == "plain-access-token"
    assert new_account.refresh_token_enc is None


@pytest.mark.anyio
async def test_connect_ad_account_update_meta():
    existing_account = AdAccount(
        id=12,
        client_id=42,
        platform="meta",
        external_account_id="act_12345",
        account_name="Old Name",
        access_token_enc="old-enc-token",
        account_currency="USD",
        account_timezone="UTC"
    )
    db = MockDb(query_result=existing_account)
    client = SimpleNamespace(id=42)
    body = AdAccountCreate(
        platform="meta",
        external_account_id="act_12345",
        account_name="New Name",
        access_token="new-plain-access-token",
        refresh_token=None,
        account_currency="BDT",
        account_timezone="Asia/Dhaka"
    )

    response = await connect_ad_account(body, client=client, db=db)

    assert db.committed is True
    assert len(db.added) == 0  # Should update in place, not add new
    assert existing_account.account_name == "New Name"
    assert existing_account.account_currency == "BDT"
    assert existing_account.account_timezone == "Asia/Dhaka"
    assert decrypt_token(existing_account.access_token_enc) == "new-plain-access-token"


@pytest.mark.anyio
async def test_list_ad_accounts():
    accounts = [
        AdAccount(id=1, client_id=42, platform="meta", external_account_id="act_1", access_token_enc="enc", account_currency="USD", account_timezone="UTC"),
        AdAccount(id=2, client_id=42, platform="tiktok", external_account_id="act_2", access_token_enc="enc", account_currency="USD", account_timezone="UTC")
    ]
    db = MockDb(query_result=accounts)
    client = SimpleNamespace(id=42)

    response = await list_ad_accounts(client=client, db=db)

    assert len(response) == 2
    assert response[0].id == 1
    assert response[1].id == 2


@pytest.mark.anyio
async def test_disconnect_ad_account_success():
    account = AdAccount(id=15, client_id=42, platform="meta", external_account_id="act_1", access_token_enc="enc", account_currency="USD", account_timezone="UTC")
    db = MockDb(query_result=account)
    client = SimpleNamespace(id=42)

    response = await disconnect_ad_account(id=15, client=client, db=db)

    assert db.committed is True
    assert len(db.deleted) == 1
    assert db.deleted[0].id == 15
    assert response == {"status": "success", "message": "Ad account disconnected successfully."}


@pytest.mark.anyio
async def test_ad_performance_analytics_zero_metrics():
    # Test that division by zero is handled safely when spend, impressions, clicks, etc are all zero
    class MockPerformanceRow:
        def __init__(self):
            self.campaign_id = "camp_123"
            self.campaign_name = "Zero Campaign"
            self.platform = "meta"
            self.spend = 0.0
            self.clicks = 0
            self.impressions = 0
            self.placed_purchases = 0
            self.placed_revenue = 0.0
            self.confirmed_purchases = 0
            self.confirmed_revenue = 0.0
            self.browser_page_views = 0
            self.server_page_views = 0

    db = MockDb(query_result=[MockPerformanceRow()])
    client = SimpleNamespace(id=42)

    response = await ad_performance_analytics(client=client, db=db, days=7)

    assert response.status == "success"
    assert len(response.data) == 1
    row = response.data[0]
    assert row.campaign_id == "camp_123"
    assert row.ctr == 0.0
    assert row.cpc == 0.0
    assert row.placed_roas == 0.0
    assert row.placed_cpa == 0.0
    assert row.confirmed_roas == 0.0
    assert row.confirmed_cpa == 0.0
    assert row.tracking_bypass_rate == 0.0


@pytest.mark.anyio
async def test_ad_performance_analytics_with_data():
    class MockPerformanceRow:
        def __init__(self):
            self.campaign_id = "camp_789"
            self.campaign_name = "Successful Campaign"
            self.platform = "tiktok"
            self.spend = 500.0
            self.clicks = 100
            self.impressions = 5000
            self.placed_purchases = 10
            self.placed_revenue = 1500.0
            self.confirmed_purchases = 8
            self.confirmed_revenue = 1200.0
            self.browser_page_views = 200
            self.server_page_views = 400

    db = MockDb(query_result=[MockPerformanceRow()])
    client = SimpleNamespace(id=42)

    response = await ad_performance_analytics(client=client, db=db, days=14)

    assert response.status == "success"
    assert response.period_days == 14
    assert len(response.data) == 1
    row = response.data[0]
    assert row.campaign_id == "camp_789"
    assert row.ctr == 2.0  # (100 / 5000) * 100
    assert row.cpc == 5.0  # 500.0 / 100
    assert row.placed_roas == 3.0  # 1500.0 / 500.0
    assert row.placed_cpa == 50.0  # 500.0 / 10
    assert row.confirmed_roas == 2.4  # 1200.0 / 500.0
    assert row.confirmed_cpa == 62.5  # 500.0 / 8
    assert row.tracking_bypass_rate == 50.0  # ((400 - 200) / 400) * 100


@pytest.mark.anyio
async def test_ad_performance_query_scopes_campaign_aggregates():
    db = MockDb(query_result=[], dialect_name="postgresql")
    client = SimpleNamespace(id=42)

    response = await ad_performance_analytics(client=client, db=db, days=7)

    query = db.executed_statements[0]
    assert response.status == "success"
    assert "c.id AS campaign_pk" in query
    assert "a.client_id = :client_id" in query
    assert "campaign_identity AS" in query
    assert "SUM(el.event_count)" in query
    assert "el.ad_platform = c.platform OR (el.ad_platform IS NULL AND ci.platform_count = 1)" in query
    assert "c.id = sc.campaign_pk" in query
    assert "c.platform = p.platform" in query
