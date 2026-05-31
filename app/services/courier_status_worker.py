import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select, or_, and_

from app.database import AsyncSessionLocal
from app.models.client import Client
from app.models.courier_order import CourierOrder
from app.routers.courier_webhook import process_courier_status_change
from app.security import decrypt_token
from app.services.courier_service import CourierService

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1800 # 30 minutes fallback check

async def poll_active_courier_orders() -> None:
    logger.info("Starting periodic courier status sync loop...")

    # 1. Fetch active courier orders and client credentials quickly
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CourierOrder, Client)
            .join(Client, CourierOrder.client_id == Client.id)
            .where(
                CourierOrder.courier_status.in_(["pending", "picked", "in_transit"])
            )
        )
        active_rows = result.all()

        if not active_rows:
            logger.info("No active courier orders to sync.")
            return

        logger.info(f"Found {len(active_rows)} active courier orders to sync.")

        orders_to_sync = []
        for order, client in active_rows:
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
                "redx_access_token": client.redx_access_token,
            })

    # The db session is now closed. We perform HTTP calls session-free.
    for item in orders_to_sync:
        try:
            new_status = None

            # Check based on provider
            if item["courier_provider"] == "steadfast":
                if item["steadfast_api_key"] and item["steadfast_secret_key"]:
                    api_key = item["steadfast_api_key"]
                    secret_key = decrypt_token(item["steadfast_secret_key"])

                    new_status = await CourierService.check_steadfast_status(
                        api_key=api_key,
                        secret_key=secret_key,
                        tracking_code=item["courier_tracking_id"]
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
                            consignment_id=item["courier_order_id"]
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
                # Open a short-lived DB session to write the change
                async with AsyncSessionLocal() as db:
                    order_result = await db.execute(
                        select(CourierOrder).where(CourierOrder.id == item["id"])
                    )
                    order = order_result.scalar_one_or_none()
                    if order:
                        await process_courier_status_change(db, order, new_status)
                        await db.commit()

        except Exception as e:
            logger.error(f"Error syncing status for courier order {item['id']}: {e}")

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
