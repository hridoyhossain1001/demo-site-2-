import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, or_, and_

from app.database import AsyncSessionLocal
from app.models.client import Client
from app.models.courier_order import CourierOrder
from app.routers.courier_webhook import process_courier_status_change
from app.security import decrypt_token
from app.services.courier_service import CourierService
from app.services.plan_service import has_growth_access
from app.services.redis_pool import get_redis

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1800 # 30 minutes fallback check
POLL_LOCK_KEY = "courier:status-poll:lock"
POLL_LOCK_TTL_SECONDS = int(os.getenv("COURIER_POLL_LOCK_TTL_SECONDS", "1200"))
POLL_MAX_CONCURRENCY = int(os.getenv("COURIER_POLL_MAX_CONCURRENCY", "8"))
_RELEASE_LOCK_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


async def _sync_courier_order(item: dict) -> None:
    try:
        new_status = None

        if item["courier_provider"] == "steadfast":
            if item["steadfast_api_key"] and item["steadfast_secret_key"]:
                new_status = await CourierService.check_steadfast_status(
                    api_key=item["steadfast_api_key"],
                    secret_key=decrypt_token(item["steadfast_secret_key"]),
                    tracking_code=item["courier_tracking_id"],
                )

        elif item["courier_provider"] == "pathao":
            if item["pathao_api_key"] and item["pathao_secret_key"] and item["pathao_store_id"]:
                try:
                    client_id, email = item["pathao_api_key"].split("|", 1)
                    decrypted_secret_pass = decrypt_token(item["pathao_secret_key"])
                    client_secret, password = decrypted_secret_pass.split("|", 1)

                    new_status = await CourierService.check_pathao_status(
                        client_id=client_id,
                        client_secret=client_secret,
                        email=email,
                        password=password,
                        consignment_id=item["courier_order_id"],
                        base_url=CourierService.pathao_base_url(item["pathao_environment"]),
                    )
                except ValueError:
                    logger.error(f"Pathao credential format incorrect for client {item['client_name']}")

        elif item["courier_provider"] == "redx":
            if item["redx_access_token"]:
                new_status = await CourierService.check_redx_status(
                    access_token=decrypt_token(item["redx_access_token"]),
                    tracking_id=item["courier_tracking_id"],
                )

        if new_status:
            logger.info(f"Syncing status for order {item['order_id']}: {item['courier_status']} -> {new_status}")
            async with AsyncSessionLocal() as db:
                order_result = await db.execute(
                    select(CourierOrder)
                    .where(CourierOrder.id == item["id"])
                    .with_for_update()
                )
                order = order_result.scalar_one_or_none()
                if order:
                    await process_courier_status_change(db, order, new_status)
                    await db.commit()

    except Exception as e:
        logger.error(f"Error syncing status for courier order {item['id']}: {e}")


async def _poll_active_courier_orders_unlocked() -> None:
    logger.info("Starting periodic courier status sync loop...")

    # 1. Fetch active courier orders and client credentials quickly
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CourierOrder, Client)
            .join(Client, CourierOrder.client_id == Client.id)
            .where(
                or_(
                    CourierOrder.courier_status.in_(["pending", "picked", "in_transit"]),
                    and_(
                        CourierOrder.courier_status == "delivered",
                        CourierOrder.pending_event_id.is_not(None),
                        CourierOrder.purchase_event_sent.is_(False),
                    ),
                    and_(
                        CourierOrder.courier_status.in_(["returned", "cancelled"]),
                        CourierOrder.pending_event_id.is_not(None),
                        CourierOrder.purchase_event_sent.is_(True),
                        CourierOrder.refund_event_sent.is_(False),
                    ),
                )
            )
        )
        active_rows = result.all()

        if not active_rows:
            logger.info("No active courier orders to sync.")
            return

        logger.info(f"Found {len(active_rows)} active courier orders to sync.")

        orders_to_sync = []
        for order, client in active_rows:
            if not has_growth_access(client):
                continue
            orders_to_sync.append({
                "id": order.id,
                "order_id": order.order_id,
                "courier_provider": order.courier_provider,
                "courier_tracking_id": order.courier_tracking_id,
                "courier_order_id": order.courier_order_id,
                "courier_status": order.courier_status,
                "client_name": client.name,
                "steadfast_api_key": client.steadfast_api_key,
                "steadfast_secret_key": client.steadfast_secret_key,
                "pathao_api_key": client.pathao_api_key,
                "pathao_secret_key": client.pathao_secret_key,
                "pathao_store_id": client.pathao_store_id,
                "pathao_environment": client.pathao_environment,
                "redx_access_token": client.redx_access_token,
            })

    # Perform provider calls concurrently while keeping a bounded API load.
    semaphore = asyncio.Semaphore(POLL_MAX_CONCURRENCY)

    async def sync_with_limit(item: dict) -> None:
        async with semaphore:
            await _sync_courier_order(item)

    await asyncio.gather(*(sync_with_limit(item) for item in orders_to_sync))


async def poll_active_courier_orders() -> None:
    """Run one polling cycle, with a Redis lock when Redis is configured."""
    redis = get_redis()
    if redis is None:
        await _poll_active_courier_orders_unlocked()
        return

    lock_token = uuid.uuid4().hex
    try:
        acquired = await redis.set(POLL_LOCK_KEY, lock_token, nx=True, ex=POLL_LOCK_TTL_SECONDS)
    except Exception as exc:
        logger.warning(f"Courier status poll lock unavailable; polling without lock: {exc}")
        await _poll_active_courier_orders_unlocked()
        return

    if not acquired:
        logger.info("Skipping courier status poll; another worker owns the poll lock.")
        return

    try:
        await _poll_active_courier_orders_unlocked()
    finally:
        try:
            await redis.eval(_RELEASE_LOCK_LUA, 1, POLL_LOCK_KEY, lock_token)
        except Exception as exc:
            logger.warning(f"Could not release courier status poll lock: {exc}")


async def poll_courier_statuses_forever() -> None:
    """Background loop to sync courier statuses forever."""
    logger.info("Courier status worker initialized.")
    # Wait 60s after startup to run the first check
    await asyncio.sleep(60)

    while True:
        try:
            await poll_active_courier_orders()
        except Exception as e:
            logger.error(f"Error in courier status poll loop: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
