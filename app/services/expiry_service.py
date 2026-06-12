"""
Pending Events Auto-Expiry Service
───────────────────────────────────
৭ দিনের বেশি পুরোনো pending events auto-expire করে।
Facebook ৭ দিনের বেশি পুরোনো event গ্রহণ করে না,
তাই expired events আর কোনো কাজে আসবে না।
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import update, and_, select

from app.database import AsyncSessionLocal
from app.models.pending_event import PendingEvent

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 7               # Facebook-এর ৭ দিনের limit
CHECK_INTERVAL_HOURS = 1      # প্রতি ১ ঘণ্টায় চেক করো


async def downgrade_expired_trials_once(now: datetime | None = None) -> int:
    """Persist Free-plan limits for clients whose 14-day Growth trial has ended."""
    from app.models.client import Client
    from app.services.plan_service import apply_expired_trial_downgrade

    current = now or datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        clients_r = await db.execute(
            select(Client).where(
                and_(
                    Client.is_active == True,
                    Client.plan_tier == "free",
                    Client.trial_ends_at.is_not(None),
                    Client.trial_ends_at <= current,
                )
            )
        )
        clients = clients_r.scalars().all()
        changed_count = 0
        for client in clients:
            if apply_expired_trial_downgrade(client, current):
                changed_count += 1

        if changed_count:
            await db.commit()
            logger.info("Downgraded %s expired trial client(s) to Free limits.", changed_count)

        return changed_count


async def expire_old_pending_events():
    """
    Background loop — প্রতি ১ ঘণ্টায় পুরোনো pending events expire করে এবং expired COD orders auto-confirm করে।
    """
    logger.info("⏰ Pending Events Expiry & Auto-Confirm Service शुरू হয়েছে।")

    while True:
        try:
            await downgrade_expired_trials_once()

            # 1. Auto-confirm COD orders based on client config (older than N days)
            async with AsyncSessionLocal() as db:
                from app.models.client import Client
                from app.routers.deferred_events import _queue_confirmed_event
                from app.dependencies import _snapshot
                from app.services.plan_service import has_growth_access
                from sqlalchemy import select

                clients_r = await db.execute(
                    select(Client).where(
                        and_(
                            Client.is_active == True,
                            Client.deferred_purchase == True,
                            Client.auto_confirm_days > 0
                        )
                    )
                )
                clients = clients_r.scalars().all()

                for client in clients:
                    if not has_growth_access(client):
                        continue
                    auto_confirm_cutoff = datetime.now(timezone.utc) - timedelta(days=client.auto_confirm_days)
                    pending_r = await db.execute(
                        select(PendingEvent)
                        .where(
                            and_(
                                PendingEvent.client_id == client.id,
                                PendingEvent.status == "pending",
                                PendingEvent.created_at <= auto_confirm_cutoff
                            )
                        )
                        .with_for_update(skip_locked=True)
                    )
                    pending_events = pending_r.scalars().all()

                    if pending_events:
                        cached_client = _snapshot(client)
                        confirmed_count = 0
                        for pe in pending_events:
                            if pe.portal_state == "operations_only":
                                continue
                            try:
                                async with db.begin_nested():
                                    await _queue_confirmed_event(cached_client, pe, db)
                                    pe.status = "confirmed"
                                    pe.confirmed_at = datetime.now(timezone.utc)
                                confirmed_count += 1
                            except Exception as ex:
                                logger.error(f"⏰ Background auto-confirm failed for order {pe.order_id}: {ex}")
                        if confirmed_count:
                            await db.commit()
                            logger.info(f"⏰ Background auto-confirmed {confirmed_count} COD orders for client {client.name}")

            # 2. Expire remaining old pending events (older than 7 days)
            cutoff = datetime.now(timezone.utc) - timedelta(days=EXPIRY_DAYS)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    update(PendingEvent)
                    .where(
                        and_(
                            PendingEvent.status == "pending",
                            PendingEvent.created_at < cutoff,
                        )
                    )
                    .values(status="expired")
                )
                expired_count = result.rowcount or 0
                await db.commit()

                if expired_count:
                    logger.info(
                        f"⏰ {expired_count} pending events expired "
                        f"(older than {EXPIRY_DAYS} days)"
                    )

            await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)

        except Exception as e:
            logger.error(f"⏰ Expiry service error: {e}")
            await asyncio.sleep(60)  # Error হলে ১ মিনিট পরে retry


async def run_incomplete_checkout_refresh_loop():
    """
    Background worker loop to periodically check active checkouts,
    refresh stale ones to 'incomplete' status (older than 20 mins),
    and enqueue WhatsApp notifications for store owners.
    """
    import os
    logger.info("⏰ Incomplete Checkout Auto-Refresh Loop started.")
    poll_interval = int(os.getenv("INCOMPLETE_CHECKOUT_POLL_SECONDS", "60"))

    while True:
        try:
            now = datetime.now(timezone.utc)
            inactive_before = now - timedelta(minutes=20)

            async with AsyncSessionLocal() as db:
                from app.models.incomplete_checkout import IncompleteCheckout
                from app.models.client import Client
                from app.models.pending_event import PendingEvent
                from app.routers.incomplete_checkouts import _normalize_phone, recover_open_checkouts_for_order
                from app.services.notification_service import create_incomplete_checkout_whatsapp_job

                def matching_order_id(checkout, pending_events):
                    try:
                        checkout_phone = _normalize_phone(checkout.phone or "")
                    except Exception:
                        return None
                    checkout_activity = checkout.last_activity_at
                    if checkout_activity and checkout_activity.tzinfo is None:
                        checkout_activity = checkout_activity.replace(tzinfo=timezone.utc)
                    for pending in pending_events:
                        pending_created = pending.created_at
                        if checkout_activity and pending_created:
                            if pending_created.tzinfo is None:
                                pending_created = pending_created.replace(tzinfo=timezone.utc)
                            if pending_created < checkout_activity - timedelta(minutes=5):
                                continue
                        raw = pending.raw_order_data if isinstance(pending.raw_order_data, dict) else {}
                        candidate = str(
                            raw.get("recipient_phone")
                            or raw.get("customer_phone")
                            or raw.get("billing_phone")
                            or ""
                        ).strip()
                        if not candidate:
                            continue
                        try:
                            if _normalize_phone(candidate) == checkout_phone:
                                return pending.order_id
                        except Exception:
                            continue
                    return None

                # Find all active checkouts that are older than 20 minutes and lock them
                stale_result = await db.execute(
                    select(IncompleteCheckout).where(
                        IncompleteCheckout.status == "active",
                        IncompleteCheckout.last_activity_at < inactive_before,
                    )
                    .with_for_update(skip_locked=True)
                )
                stale_checkouts = stale_result.scalars().all()

                if stale_checkouts:
                    client_ids = {c.client_id for c in stale_checkouts}
                    clients_r = await db.execute(
                        select(Client).where(Client.id.in_(client_ids))
                    )
                    clients = {c.id: c for c in clients_r.scalars().all()}

                    pending_r = await db.execute(
                        select(PendingEvent).where(
                            and_(
                                PendingEvent.client_id.in_(client_ids),
                                PendingEvent.raw_order_data.is_not(None),
                                PendingEvent.created_at >= inactive_before - timedelta(hours=12),
                            )
                        )
                    )
                    pending_by_client: dict[int, list[PendingEvent]] = {}
                    for pending in pending_r.scalars().all():
                        pending_by_client.setdefault(pending.client_id, []).append(pending)

                    refreshed_count = 0
                    recovered_count = 0
                    for checkout in stale_checkouts:
                        client = clients.get(checkout.client_id)
                        order_id = matching_order_id(checkout, pending_by_client.get(checkout.client_id, []))
                        if order_id:
                            recovered = await recover_open_checkouts_for_order(
                                db,
                                client_id=checkout.client_id,
                                phone=checkout.phone or "",
                                order_id=str(order_id),
                            )
                            if recovered:
                                recovered_count += 1
                                continue
                        checkout.status = "incomplete"
                        if client:
                            await create_incomplete_checkout_whatsapp_job(db, client, checkout)
                        refreshed_count += 1

                    await db.commit()
                    logger.info(
                        "Auto-refreshed %s stale checkout(s), recovered %s from matching orders, and queued WhatsApp alerts.",
                        refreshed_count,
                        recovered_count,
                    )

            await asyncio.sleep(poll_interval)
        except Exception as e:
            logger.error(f"⏰ Incomplete Checkout refresh loop error: {e}")
            await asyncio.sleep(30)
