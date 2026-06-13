import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.dependencies import CachedClient
from app.models.client import Client
from app.models.incomplete_checkout import IncompleteCheckout
from app.models.pending_event import PendingEvent
from app.routers.client_api import (
    ManualRecoveryOrderItem,
    ManualRecoveryOrderRequest,
    _create_manual_recovery_order,
    _normalize_order_product_attributes,
    _order_price_breakdown,
)
from app.routers.incomplete_checkouts import (
    IncompleteCheckoutConvert,
    IncompleteCheckoutUpsert,
    _normalize_phone,
    recover_open_checkouts_for_order,
    upsert_incomplete_checkout,
    convert_incomplete_checkout,
)
from app.services.expiry_service import _pending_events_by_client_phone, _reconcile_stale_checkout


def test_client_incomplete_checkout_get_is_read_only_and_refresh_is_post():
    source = Path("app/routers/client_api.py").read_text(encoding="utf-8")
    get_route = source.split('@router.get("/incomplete-checkouts")', 1)[1].split(
        '@router.post("/incomplete-checkouts/refresh")',
        1,
    )[0]
    refresh_route = source.split('@router.post("/incomplete-checkouts/refresh")', 1)[1].split(
        '@router.post("/incomplete-checkouts/{checkout_id}/status")',
        1,
    )[0]
    portal_source = Path("client-portal/src/App.tsx").read_text(encoding="utf-8")

    assert "_refresh_incomplete_checkout_states" not in get_route
    assert "await _refresh_incomplete_checkout_states(db, client.id)" in refresh_route
    assert "fetch('/api/incomplete-checkouts/refresh', { method: 'POST' })" in portal_source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01816101745", "8801816101745"),
        ("1816101745", "8801816101745"),
        ("+880 1816-101745", "8801816101745"),
    ],
)
def test_normalize_phone_accepts_bd_mobile_formats(raw, expected):
    assert _normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "017000000000", "+12025550123"])
def test_normalize_phone_rejects_invalid_or_non_bd_numbers(raw):
    with pytest.raises(HTTPException) as exc:
        _normalize_phone(raw)
    assert exc.value.status_code == 422


def test_upsert_accepts_legacy_empty_php_campaign_array():
    payload = IncompleteCheckoutUpsert(
        visitor_id="bk.test.visitor",
        phone="01816101745",
        campaign_data=[],
    )
    assert payload.campaign_data == {}


def test_courier_product_attributes_normalize_common_variant_shapes():
    attributes = _normalize_order_product_attributes({
        "attributes": [{"name": "Color", "option": "Black"}],
        "variation": {"attribute_pa_size": "36"},
        "meta_data": [{"display_key": "Cup", "display_value": "B"}],
        "number": "XL-42",
    })

    assert attributes == {
        "Color": "Black",
        "Size": "36",
        "Cup": "B",
        "Number": "XL-42",
    }


def test_courier_order_price_breakdown_separates_total_from_product_prices():
    breakdown = _order_price_breakdown(
        [{"price": 950, "quantity": 1}],
        {"value": 1060},
        {},
    )

    assert breakdown == {
        "productSubtotal": 950.0,
        "deliveryCharge": 0.0,
        "discount": 0.0,
        "otherAdjustment": 110.0,
        "orderTotal": 1060.0,
    }


def test_courier_order_price_breakdown_uses_explicit_delivery_and_discount():
    breakdown = _order_price_breakdown(
        [{"price": 950, "quantity": 1}],
        {"value": 1040},
        {"delivery_charge": 110, "discount": 20},
    )

    assert breakdown["deliveryCharge"] == 110.0
    assert breakdown["discount"] == 20.0
    assert breakdown["otherAdjustment"] == 0.0


def test_courier_order_price_breakdown_tolerates_non_numeric_event_value():
    breakdown = _order_price_breakdown(
        [{"price": "950", "quantity": 1}],
        {"value": "not-a-number"},
        {"cod_amount": "1,060"},
    )

    assert breakdown["productSubtotal"] == 950.0
    assert breakdown["orderTotal"] == 1060.0
    assert breakdown["otherAdjustment"] == 110.0


def test_pending_orders_are_indexed_by_client_and_normalized_phone():
    matching = PendingEvent(
        client_id=1,
        order_id="16781",
        event_data={},
        raw_order_data={"recipient_phone": "+880 1816-101745"},
    )
    other_client = PendingEvent(
        client_id=2,
        order_id="16782",
        event_data={},
        raw_order_data={"recipient_phone": "01816101745"},
    )
    invalid = PendingEvent(
        client_id=1,
        order_id="16783",
        event_data={},
        raw_order_data={"recipient_phone": "invalid"},
    )

    indexed = _pending_events_by_client_phone([matching, other_client, invalid])

    assert indexed[(1, "8801816101745")] == [matching]
    assert indexed[(2, "8801816101745")] == [other_client]
    assert len(indexed) == 2


def _cached_client(plan_tier: str = "growth") -> CachedClient:
    return CachedClient(
        id=1,
        name="Test Client",
        api_key="test-key",
        public_key="public-key",
        portal_key="portal-key",
        pixel_id="pixel",
        access_token="token",
        test_event_code=None,
        tiktok_test_event_code=None,
        is_active=True,
        domain=None,
        rate_limit=5000,
        daily_quota=100000,
        monthly_limit=50000,
        enable_facebook=True,
        enable_tiktok=True,
        enable_ga4=True,
        tiktok_pixel_id=None,
        tiktok_access_token=None,
        ga4_measurement_id=None,
        ga4_api_secret=None,
        deferred_purchase=False,
        webhook_url=None,
        plan_tier=plan_tier,
        trial_started_at=None,
        trial_ends_at=None,
    )


def _client_model(plan_tier: str = "growth") -> Client:
    return Client(
        id=1,
        name="Test Client",
        api_key="test-key",
        public_key="public-key",
        portal_key="portal-key",
        pixel_id="pixel",
        access_token="token",
        is_active=True,
        plan_tier=plan_tier,
        owner_notify_whatsapp=False,
    )


def _manual_order_payload(**overrides) -> ManualRecoveryOrderRequest:
    data = {
        "customer_name": "Ron",
        "phone": "01816101745",
        "address": "Road 19, Nikunja 2, Dhaka",
        "items": [
            ManualRecoveryOrderItem(
                name="Black Lace Bra Panty Set",
                content_id="sku-1",
                quantity=1,
                price=950,
                attributes={"Color": "Black", "Size": "36"},
            )
        ],
        "delivery_charge": 110,
        "discount": 0,
        "note": "Confirmed by phone",
    }
    data.update(overrides)
    return ManualRecoveryOrderRequest(**data)


@pytest.mark.asyncio
async def test_convert_marks_all_matching_open_drafts_recovered():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        payload = IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745")
        await upsert_incomplete_checkout(payload, _cached_client(), db)
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(visitor_id="bk.test.visitor.2", phone="01816101745"),
            _cached_client(),
            db,
        )
        rows = (await db.execute(select(IncompleteCheckout))).scalars().all()
        for row in rows:
            row.status = "incomplete"
            row.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=25)
        await db.commit()

        response = await convert_incomplete_checkout(
            IncompleteCheckoutConvert(phone="01816101745", order_id="1001"),
            _cached_client(),
            db,
        )

        rows = (await db.execute(select(IncompleteCheckout))).scalars().all()

    await engine.dispose()
    assert response["converted"] is True
    assert response["converted_count"] == 1

    recovered_drafts = [r for r in rows if r.status == "recovered"]
    ignored_drafts = [r for r in rows if r.status == "ignored"]

    assert len(recovered_drafts) == 1
    assert len(ignored_drafts) == 1
    assert recovered_drafts[0].visitor_id == "bk.test.visitor.2"
    assert recovered_drafts[0].order_id == "1001"


@pytest.mark.asyncio
async def test_convert_requires_growth_access():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        with pytest.raises(HTTPException) as exc:
            await convert_incomplete_checkout(
                IncompleteCheckoutConvert(phone="01816101745", order_id="1001"),
                _cached_client(plan_tier="free"),
                db,
            )

    await engine.dispose()
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_upsert_after_recent_recovery_does_not_create_new_incomplete_draft():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        payload = IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745")
        first = await upsert_incomplete_checkout(payload, _cached_client(), db)
        row = (await db.execute(select(IncompleteCheckout))).scalar_one()
        row.status = "incomplete"
        row.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=25)
        await db.commit()
        await convert_incomplete_checkout(
            IncompleteCheckoutConvert(visitor_id="bk.test.visitor", phone="01816101745", order_id="1001"),
            _cached_client(),
            db,
        )
        second = await upsert_incomplete_checkout(payload, _cached_client(), db)
        count = len((await db.execute(select(IncompleteCheckout))).scalars().all())

    await engine.dispose()
    assert second["suppressed"] is True
    assert second["id"] == first["id"]
    assert second["status"] == "recovered"
    assert count == 1


@pytest.mark.asyncio
async def test_upsert_after_recent_direct_order_match_does_not_create_late_draft():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        payload = IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745")
        first = await upsert_incomplete_checkout(payload, _cached_client(), db)
        await recover_open_checkouts_for_order(
            db,
            client_id=1,
            phone="01816101745",
            order_id="1001",
        )
        await db.commit()

        second = await upsert_incomplete_checkout(payload, _cached_client(), db)
        rows = (await db.execute(select(IncompleteCheckout))).scalars().all()

    await engine.dispose()
    assert second["suppressed"] is True
    assert second["id"] == first["id"]
    assert second["status"] == "ignored"
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_convert_matches_without_visitor_id_mismatch():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        payload = IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745")
        await upsert_incomplete_checkout(payload, _cached_client(), db)
        row = (await db.execute(select(IncompleteCheckout))).scalar_one()
        row.status = "incomplete"
        row.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=25)
        await db.commit()

        # Convert with a different visitor_id but same phone
        response = await convert_incomplete_checkout(
            IncompleteCheckoutConvert(visitor_id="bk.different.visitor", phone="01816101745", order_id="1001"),
            _cached_client(),
            db,
        )

        rows = (await db.execute(select(IncompleteCheckout))).scalars().all()

    await engine.dispose()
    assert response["converted"] is True
    assert response["converted_count"] == 1
    assert rows[0].status == "recovered"
    assert rows[0].order_id == "1001"


@pytest.mark.asyncio
async def test_purchase_ingest_fallback_recovers_matching_open_checkout():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745"),
            _cached_client(),
            db,
        )
        row = (await db.execute(select(IncompleteCheckout))).scalar_one()
        row.status = "incomplete"
        row.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=25)
        await db.commit()
        converted = await recover_open_checkouts_for_order(
            db,
            client_id=1,
            phone="01816101745",
            order_id="16781",
        )
        await db.commit()
        row = (await db.execute(select(IncompleteCheckout))).scalar_one()

    await engine.dispose()
    assert converted == 1
    assert row.status == "recovered"
    assert row.order_id == "16781"


@pytest.mark.asyncio
async def test_old_order_does_not_recover_newer_checkout_with_same_phone():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(visitor_id="bk.new.visitor", phone="01816101745"),
            _cached_client(),
            db,
        )
        checkout = (await db.execute(select(IncompleteCheckout))).scalar_one()
        checkout.status = "incomplete"
        checkout.last_activity_at = datetime.now(timezone.utc)
        old_order_time = checkout.last_activity_at - timedelta(days=2)
        await db.commit()

        converted = await recover_open_checkouts_for_order(
            db,
            client_id=1,
            phone="01816101745",
            order_id="old-order",
            activity_window_start=old_order_time - timedelta(hours=12),
            activity_window_end=old_order_time + timedelta(minutes=5),
        )
        await db.commit()
        checkout = (await db.execute(select(IncompleteCheckout))).scalar_one()

    await engine.dispose()
    assert converted == 0
    assert checkout.status == "incomplete"
    assert checkout.order_id is None


@pytest.mark.asyncio
async def test_direct_checkout_completion_hides_active_draft_without_recovered_status():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745"),
            _cached_client(),
            db,
        )
        converted = await recover_open_checkouts_for_order(
            db,
            client_id=1,
            phone="01816101745",
            order_id="16781",
        )
        await db.commit()
        row = (await db.execute(select(IncompleteCheckout))).scalar_one()

    await engine.dispose()
    assert converted == 0
    assert row.status == "ignored"
    assert row.order_id == "16781"


@pytest.mark.asyncio
async def test_stale_worker_does_not_reverse_matched_active_draft_to_incomplete():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745"),
            _cached_client(),
            db,
        )
        checkout = (await db.execute(select(IncompleteCheckout))).scalar_one()
        checkout.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=21)
        pending = PendingEvent(
            client_id=1,
            order_id="16781",
            event_data={},
            raw_order_data={"recipient_phone": "01816101745"},
            status="pending",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        db.add(pending)
        await db.commit()

        outcome = await _reconcile_stale_checkout(db, checkout, [pending])
        await db.commit()
        checkout = await db.get(IncompleteCheckout, checkout.id)

    await engine.dispose()
    assert outcome == "matched"
    assert checkout.status == "ignored"
    assert checkout.order_id == "16781"


@pytest.mark.asyncio
async def test_manual_recovery_order_creates_pending_order_and_marks_lead_recovered():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        client = _client_model()
        db.add(client)
        await db.commit()
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(
                visitor_id="bk.test.visitor",
                phone="01816101745",
                customer_name="Ron",
                address="Old address",
                amount=950,
                products=[{"content_name": "Old Product", "quantity": 1}],
            ),
            _cached_client(),
            db,
        )
        checkout = (await db.execute(select(IncompleteCheckout))).scalar_one()
        checkout.status = "contacted"
        await db.commit()

        response = await _create_manual_recovery_order(
            db,
            client=client,
            checkout_id=checkout.id,
            payload=_manual_order_payload(),
        )

        checkout = await db.get(IncompleteCheckout, checkout.id)
        pending = (await db.execute(select(PendingEvent))).scalar_one()

    await engine.dispose()
    assert response["success"] is True
    assert response["orderId"].startswith("manual-")
    assert checkout.status == "recovered"
    assert checkout.order_id == pending.order_id
    assert pending.status == "pending"
    assert pending.portal_state == "manual_recovery"
    assert pending.raw_order_data["recipient_phone"] == "8801816101745"
    assert pending.raw_order_data["recipient_address"] == "Road 19, Nikunja 2, Dhaka"
    assert pending.raw_order_data["cod_amount"] == 1060
    assert pending.event_data["custom_data"]["contents"][0]["attributes"] == {"Color": "Black", "Size": "36"}


@pytest.mark.asyncio
async def test_manual_recovery_order_rejects_active_direct_checkout_lead():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        client = _client_model()
        db.add(client)
        await db.commit()
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745"),
            _cached_client(),
            db,
        )
        checkout = (await db.execute(select(IncompleteCheckout))).scalar_one()
        with pytest.raises(HTTPException) as exc:
            await _create_manual_recovery_order(
                db,
                client=client,
                checkout_id=checkout.id,
                payload=_manual_order_payload(),
            )

    await engine.dispose()
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_manual_recovery_order_prevents_duplicate_order_from_same_lead():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        client = _client_model()
        db.add(client)
        await db.commit()
        await upsert_incomplete_checkout(
            IncompleteCheckoutUpsert(visitor_id="bk.test.visitor", phone="01816101745"),
            _cached_client(),
            db,
        )
        checkout = (await db.execute(select(IncompleteCheckout))).scalar_one()
        checkout.status = "incomplete"
        await db.commit()

        await _create_manual_recovery_order(
            db,
            client=client,
            checkout_id=checkout.id,
            payload=_manual_order_payload(),
        )
        with pytest.raises(HTTPException) as exc:
            await _create_manual_recovery_order(
                db,
                client=client,
                checkout_id=checkout.id,
                payload=_manual_order_payload(),
            )

    await engine.dispose()
    assert exc.value.status_code == 400
