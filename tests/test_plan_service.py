from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dependencies import _snapshot
from app.routers import courier_webhook
from app.routers.events import _upsert_order_record
from app.services import expiry_service
from app.services.plan_service import (
    FREE_EVENT_LIMIT,
    FREE_ORDER_LIMIT,
    TRIAL_EVENT_LIMIT,
    TRIAL_ORDER_LIMIT,
    apply_expired_trial_downgrade,
    assign_paid_plan,
    has_growth_access,
    new_free_values,
    new_trial_values,
    plan_summary,
    record_trial_identity,
    require_trial_available,
    trial_domains,
)


def _client(**overrides):
    values = {
        "id": 1,
        "name": "Test Store",
        "api_key": "api-key",
        "public_key": "public-key",
        "portal_key": "portal-key",
        "pixel_id": "123",
        "access_token": "encrypted-token",
        "test_event_code": None,
        "tiktok_test_event_code": None,
        "is_active": True,
        "domain": "example.com",
        "rate_limit": 120,
        "daily_quota": 1000,
        "monthly_limit": TRIAL_EVENT_LIMIT,
        "enable_facebook": True,
        "enable_tiktok": True,
        "enable_ga4": True,
        "tiktok_pixel_id": "tt-pixel",
        "tiktok_access_token": "tt-token",
        "ga4_measurement_id": "G-TEST",
        "ga4_api_secret": "ga-secret",
        "deferred_purchase": True,
        "webhook_url": None,
        "shopify_shared_secret": None,
        "event_rules": None,
        "courier_auto_send": True,
        "auto_confirm_days": 2,
        "plan_tier": "free",
        "billing_status": "trial",
        "trial_started_at": datetime.now(timezone.utc),
        "trial_ends_at": datetime.now(timezone.utc) + timedelta(days=14),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_new_account_trial_unlocks_growth_features_for_14_days():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    client = _client(**new_trial_values(now))

    summary = plan_summary(client, now)

    assert has_growth_access(client, now) is True
    assert summary["name"] == "Growth Trial"
    assert summary["billingStatus"] == "trial"
    assert summary["trialDaysRemaining"] == 14
    assert summary["eventsQuota"] == TRIAL_EVENT_LIMIT
    assert summary["ordersQuota"] == TRIAL_ORDER_LIMIT


def test_new_free_account_starts_without_growth_trial():
    client = _client(**new_free_values())

    summary = plan_summary(client)

    assert has_growth_access(client) is False
    assert summary["name"] == "Free Plan"
    assert summary["billingStatus"] == "free"
    assert summary["isTrial"] is False
    assert summary["eventsQuota"] == FREE_EVENT_LIMIT
    assert summary["ordersQuota"] == FREE_ORDER_LIMIT


def test_expired_trial_downgrades_paid_toggles_and_quotas():
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    client = _client(trial_ends_at=now - timedelta(seconds=1))

    assert apply_expired_trial_downgrade(client, now) is True
    assert client.enable_tiktok is False
    assert client.enable_ga4 is False
    assert client.deferred_purchase is False
    assert client.courier_auto_send is False
    assert client.auto_confirm_days == 0
    assert client.monthly_limit == FREE_EVENT_LIMIT

    summary = plan_summary(client, now)
    assert summary["name"] == "Free Plan"
    assert summary["eventsQuota"] == FREE_EVENT_LIMIT
    assert summary["ordersQuota"] == FREE_ORDER_LIMIT


def test_admin_cancel_paid_plan_applies_free_limits():
    client = _client(plan_tier="growth", trial_started_at=None, trial_ends_at=None, monthly_limit=500_000)

    assign_paid_plan(client, "free")

    assert client.plan_tier == "free"
    assert client.billing_status == "free"
    assert client.enable_tiktok is False
    assert client.enable_ga4 is False
    assert client.deferred_purchase is False
    assert client.courier_auto_send is False
    assert client.monthly_limit == FREE_EVENT_LIMIT


def test_admin_confirm_paid_plan_sets_billing_status():
    client = _client(plan_tier="free", monthly_limit=5_000)

    assign_paid_plan(client, "growth", billing_status="manual_invoice")

    assert client.plan_tier == "growth"
    assert client.billing_status == "manual_invoice"
    assert client.monthly_limit == 500_000


def test_admin_confirm_growth_does_not_carry_trial_quota():
    client = _client(plan_tier="free", billing_status="trial", monthly_limit=TRIAL_EVENT_LIMIT)

    assign_paid_plan(client, "growth", monthly_limit=TRIAL_EVENT_LIMIT, billing_status="paid")

    assert client.plan_tier == "growth"
    assert client.billing_status == "paid"
    assert client.monthly_limit == 500_000


def test_admin_confirm_growth_preserves_explicit_custom_quota():
    client = _client(plan_tier="free", billing_status="trial", monthly_limit=TRIAL_EVENT_LIMIT)

    assign_paid_plan(client, "growth", monthly_limit=300_000, billing_status="paid")

    assert client.monthly_limit == 300_000


def test_trial_domains_include_main_domain_for_subdomain_reuse_guard():
    assert trial_domains("https://checkout.example.com/path") == [
        "checkout.example.com",
        "example.com",
    ]
    assert trial_domains("shop.example.com.bd") == [
        "shop.example.com.bd",
        "example.com.bd",
    ]


def test_cached_client_blocks_stale_paid_toggles_after_trial_expiry():
    client = _client(trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1))

    cached = _snapshot(client)

    assert cached.enable_tiktok is False
    assert cached.enable_ga4 is False
    assert cached.deferred_purchase is False
    assert cached.monthly_limit == FREE_EVENT_LIMIT


def test_product_rules_document_plan_service_as_entitlement_source():
    rules = (Path(__file__).resolve().parents[1] / "docs" / "PRODUCT_RULES.md").read_text(encoding="utf-8")

    assert "Effective event quotas and feature access come from `app.services.plan_service`" in rules
    assert "Expired trials downgrade to the free entitlement set" in rules


class _FakeOrderRecordDb:
    def __init__(self):
        self.added = []

    @asynccontextmanager
    async def begin_nested(self):
        yield

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


@pytest.mark.anyio
async def test_operations_only_order_record_is_available_for_manual_courier_booking():
    db = _FakeOrderRecordDb()
    event = SimpleNamespace(
        event_id="wc_purchase_1001",
        event_time=1717200000,
        custom_data=SimpleNamespace(order_id="1001"),
        raw_order_data={"recipient_name": "Customer"},
        model_dump=lambda exclude_none: {
            "event_name": "Purchase",
            "event_id": "wc_purchase_1001",
            "custom_data": {"order_id": "1001"},
        },
    )

    await _upsert_order_record(db, _client(), event, portal_state="operations_only")

    assert len(db.added) == 1
    assert db.added[0].order_id == "1001"
    assert db.added[0].status == "pending"
    assert db.added[0].portal_state == "operations_only"


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeCourierStatusDb:
    def __init__(self, client, pending):
        self.results = iter([_ScalarResult(client), _ScalarResult(pending)])
        self.added = []

    async def execute(self, _stmt):
        return next(self.results)

    def add(self, row):
        self.added.append(row)


@pytest.mark.anyio
async def test_free_delivery_status_update_does_not_queue_paid_purchase_sync(monkeypatch):
    queued = False

    async def fake_queue(*_args, **_kwargs):
        nonlocal queued
        queued = True

    monkeypatch.setattr(courier_webhook, "_queue_confirmed_event", fake_queue)
    now = datetime.now(timezone.utc)
    client = _client(trial_ends_at=now - timedelta(days=1))
    pending = SimpleNamespace(id=2, status="courier_booked", portal_state="processing")
    order = SimpleNamespace(
        client_id=1,
        pending_event_id=2,
        courier_provider="steadfast",
        courier_status="pending",
        status_history=[],
        order_id="1001",
        purchase_event_sent=False,
        refund_event_sent=False,
    )
    db = _FakeCourierStatusDb(client, pending)

    await courier_webhook.process_courier_status_change(db, order, "delivered")

    assert order.courier_status == "delivered"
    assert queued is False
    assert order.purchase_event_sent is False


class _FakeExpiredTrialResult:
    def __init__(self, clients):
        self.clients = clients

    def scalars(self):
        return self

    def all(self):
        return self.clients


class _FakeExpiredTrialSession:
    def __init__(self, clients):
        self.clients = clients
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _stmt):
        return _FakeExpiredTrialResult(self.clients)

    async def commit(self):
        self.committed = True


@pytest.mark.anyio
async def test_expiry_worker_persists_expired_trial_downgrade(monkeypatch):
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    client = _client(trial_ends_at=now - timedelta(days=1))
    fake_session = _FakeExpiredTrialSession([client])

    monkeypatch.setattr(expiry_service, "AsyncSessionLocal", lambda: fake_session)

    changed = await expiry_service.downgrade_expired_trials_once(now)

    assert changed == 1
    assert fake_session.committed is True
    assert client.deferred_purchase is False
    assert client.courier_auto_send is False
    assert client.monthly_limit == FREE_EVENT_LIMIT


class _FakeTrialIdentityResult:
    def __init__(self, identity):
        self.identity = identity

    def scalar_one_or_none(self):
        return self.identity


class _FakeTrialIdentityDb:
    def __init__(self, identity=None):
        self.identity = identity
        self.added = []

    async def execute(self, _stmt):
        return _FakeTrialIdentityResult(self.identity)

    def add(self, row):
        self.added.append(row)


@pytest.mark.anyio
async def test_trial_identity_blocks_reused_domain():
    db = _FakeTrialIdentityDb(SimpleNamespace(client_id=7, domain="example.com", pixel_id=None))

    with pytest.raises(Exception) as exc:
        await require_trial_available(db, domain="https://www.example.com", exclude_client_id=8)

    assert "domain has already used" in str(exc.value)


@pytest.mark.anyio
async def test_trial_identity_records_domain_and_pixel_once():
    db = _FakeTrialIdentityDb()
    client = _client(id=3, domain="https://www.example.com", pixel_id="123456")

    created = await record_trial_identity(db, client, email="owner@example.com", source="setup")

    assert created == 2
    assert {row.domain for row in db.added} == {"example.com", None}
    assert {row.pixel_id for row in db.added} == {None, "123456"}
