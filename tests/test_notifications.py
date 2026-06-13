import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.client import Client
from app.models.incomplete_checkout import IncompleteCheckout
from app.models.notification_job import NotificationJob
from app.models.whatsapp_instance import WhatsAppInstance
from app.services.notification_service import (
    create_purchase_whatsapp_job,
    format_incomplete_checkout_message,
    format_purchase_message,
)
from app.services.notification_worker import (
    _cancel_stale_incomplete_checkout_job,
    _mark_permanent_failure,
    _mark_retryable_failure,
    _next_attempt_after,
    claim_due_jobs,
    process_jobs_with_instance_limits,
)


def _mock_client() -> Client:
    return Client(
        id=1,
        name="Test Store",
        owner_notify_whatsapp=True,
        owner_whatsapp_number="8801816101745",
        whatsapp_instance_id=1,
        api_key="test_api_key",
        pixel_id="pixel",
        access_token="token",
        is_active=True,
    )


def _mock_event_payload() -> dict:
    return {
        "event_name": "Purchase",
        "event_time": 1718020800,
        "event_id": "test_order_123",
        "custom_data": {
            "order_id": "test_order_123",
            "value": 1500.50,
            "currency": "BDT",
            "num_items": 3,
        },
    }


def _mock_checkout() -> IncompleteCheckout:
    return IncompleteCheckout(
        id=10,
        client_id=1,
        visitor_id="visitor_99",
        phone="8801700000000",
        phone_hash="hash_value",
        customer_name="Hridoy Hossain",
        amount=Decimal("2500.00"),
        currency="BDT",
        products=[{"content_name": "Leather Jacket", "quantity": 1}],
        status="incomplete",
    )


async def _memory_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    return engine, Session


def test_purchase_message_formatting():
    message = format_purchase_message(_mock_client(), _mock_event_payload())

    assert "নতুন অর্ডার এসেছে!" in message
    assert "স্টোর: Test Store" in message
    assert "অর্ডার আইডি: #test_order_123" in message
    assert "পরিমাণ: 1500.50 BDT" in message
    assert "আইটেম সংখ্যা: 3" in message


def test_incomplete_checkout_message_formatting():
    message = format_incomplete_checkout_message(_mock_client(), _mock_checkout())

    assert "ইনকমপ্লিট চেকআউট অ্যালার্ট!" in message
    assert "স্টোর: Test Store" in message
    assert "কাস্টমার ফোন: 8801700000000" in message
    assert "কার্ট ভ্যালু: 2500.00 BDT" in message
    assert "আইটেম: Leather Jacket" in message
    assert "টিপস: কাস্টমারকে এখনই কল দিন।" in message


@pytest.mark.asyncio
async def test_duplicate_safe_jobs_creation():
    engine, Session = await _memory_session()
    async with Session() as db:
        client = _mock_client()
        db.add(client)
        db.add(WhatsAppInstance(id=1, instance_name="default-instance", phone_number="8801816101745", status="active"))
        await db.commit()

        payload = _mock_event_payload()
        job1 = await create_purchase_whatsapp_job(db, client, payload)
        assert job1 is not None
        assert job1.dedupe_key == "purchase:1:test_order_123:whatsapp"

        job2 = await create_purchase_whatsapp_job(db, client, payload)
        assert job2 is None

        jobs = (await db.execute(select(NotificationJob))).scalars().all()
        assert len(jobs) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_claiming_and_locking():
    engine, Session = await _memory_session()
    async with Session() as db:
        db.add(NotificationJob(
            client_id=1,
            whatsapp_instance_id=1,
            event_type="purchase",
            payload={},
            dedupe_key="job_claim_key_1",
            status="pending",
        ))
        await db.commit()

        jobs = await claim_due_jobs(db, limit=5)
        assert len(jobs) == 1
        assert jobs[0].status == "processing"
        assert jobs[0].locked_by is not None
        assert jobs[0].locked_until > datetime.now(timezone.utc)

    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_reclaims_expired_processing_jobs():
    engine, Session = await _memory_session()
    async with Session() as db:
        db.add(NotificationJob(
            client_id=1,
            whatsapp_instance_id=1,
            event_type="purchase",
            payload={},
            dedupe_key="expired_processing_key",
            status="processing",
            locked_by="dead-worker",
            locked_until=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        await db.commit()

        jobs = await claim_due_jobs(db, limit=5)
        assert len(jobs) == 1
        assert jobs[0].status == "processing"
        assert jobs[0].locked_by != "dead-worker"
        assert jobs[0].locked_until > datetime.now(timezone.utc)

    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_cancels_incomplete_alert_after_order_match():
    engine, Session = await _memory_session()
    async with Session() as db:
        checkout = _mock_checkout()
        checkout.status = "ignored"
        db.add(checkout)
        job = NotificationJob(
            client_id=1,
            whatsapp_instance_id=1,
            event_type="incomplete_checkout",
            payload={"id": checkout.id},
            dedupe_key="stale_incomplete_alert",
            status="processing",
            locked_by="worker",
            locked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db.add(job)
        await db.commit()

        cancelled = await _cancel_stale_incomplete_checkout_job(db, job)

    await engine.dispose()
    assert cancelled is True
    assert job.status == "cancelled"
    assert job.locked_by is None
    assert job.locked_until is None


@pytest.mark.asyncio
async def test_same_instance_jobs_are_processed_serially(monkeypatch):
    import app.services.notification_worker as worker

    active_by_instance = {1: 0}
    max_active_by_instance = {1: 0}

    async def fake_process_job(job_id: int):
        instance_id = 1 if job_id in {1, 2} else 2
        active_by_instance[instance_id] = active_by_instance.get(instance_id, 0) + 1
        max_active_by_instance[instance_id] = max(max_active_by_instance.get(instance_id, 0), active_by_instance[instance_id])
        await asyncio.sleep(0)
        active_by_instance[instance_id] -= 1

    monkeypatch.setattr(worker, "process_job", fake_process_job)
    jobs = [
        SimpleNamespace(id=1, whatsapp_instance_id=1),
        SimpleNamespace(id=2, whatsapp_instance_id=1),
        SimpleNamespace(id=3, whatsapp_instance_id=2),
    ]

    await process_jobs_with_instance_limits(jobs)

    assert max_active_by_instance[1] == 1


def test_next_attempt_backoff_times():
    now = datetime.now(timezone.utc)
    t1 = _next_attempt_after(1)
    t2 = _next_attempt_after(2)
    t3 = _next_attempt_after(3)

    assert 55 < (t1 - now).total_seconds() < 65
    assert 295 < (t2 - now).total_seconds() < 305
    assert 895 < (t3 - now).total_seconds() < 905


def test_retryable_failure_increments_attempt_and_releases_lock():
    job = NotificationJob(
        status="processing",
        attempt_count=0,
        max_attempts=4,
        locked_by="worker",
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    _mark_retryable_failure(job, "WhatsApp instance missing")

    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.next_attempt_at is not None
    assert job.locked_by is None
    assert job.locked_until is None


def test_permanent_failure_exhausts_attempts_and_releases_lock():
    job = NotificationJob(
        status="processing",
        attempt_count=0,
        max_attempts=4,
        locked_by="worker",
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    _mark_permanent_failure(job, "Client inactive")

    assert job.status == "failed"
    assert job.attempt_count == 4
    assert job.next_attempt_at is None
    assert job.locked_by is None
    assert job.locked_until is None
