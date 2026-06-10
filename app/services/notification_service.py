import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.incomplete_checkout import IncompleteCheckout
from app.models.notification_job import NotificationJob

logger = logging.getLogger(__name__)


def get_field(obj, path, default=None):
    """Safely traverse dictionary or object attributes."""
    parts = path.split(".")
    current = obj
    for part in parts:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return default
    return current if current is not None else default


def _bd_time_label() -> str:
    return datetime.now(timezone(timedelta(hours=6))).strftime("%d-%m-%Y %I:%M %p")


def format_purchase_message(client: Client, event_payload: dict) -> str:
    """Format purchase notification message for store owners."""
    store_name = client.name
    order_id = get_field(event_payload, "custom_data.order_id") or get_field(event_payload, "event_id") or "N/A"
    amount = get_field(event_payload, "custom_data.value") or get_field(event_payload, "raw_order_data.total") or 0
    try:
        amount_str = f"{float(amount):.2f}"
    except (ValueError, TypeError):
        amount_str = str(amount)

    currency = get_field(event_payload, "custom_data.currency") or "BDT"
    contents = get_field(event_payload, "custom_data.contents")
    num_items = get_field(event_payload, "custom_data.num_items")
    if num_items is None:
        num_items = len(contents) if isinstance(contents, list) else 1

    return "\n".join([
        "নতুন অর্ডার এসেছে!",
        f"স্টোর: {store_name}",
        f"অর্ডার আইডি: #{order_id}",
        f"পরিমাণ: {amount_str} {currency}",
        f"আইটেম সংখ্যা: {num_items}",
        f"সময়: {_bd_time_label()}",
    ])


def format_incomplete_checkout_message(client: Client, checkout_payload: IncompleteCheckout) -> str:
    """Format incomplete checkout recovery alert message for store owners."""
    store_name = client.name
    customer_phone = checkout_payload.phone or "N/A"
    amount = checkout_payload.amount or 0
    try:
        amount_str = f"{float(amount):.2f}"
    except (ValueError, TypeError):
        amount_str = str(amount)
    currency = checkout_payload.currency or "BDT"

    product_names = []
    if checkout_payload.products:
        for product in checkout_payload.products:
            if isinstance(product, dict):
                name = product.get("content_name") or product.get("name")
                if name:
                    product_names.append(str(name))
    items_str = ", ".join(product_names) if product_names else "Product"

    return "\n".join([
        "ইনকমপ্লিট চেকআউট অ্যালার্ট!",
        f"স্টোর: {store_name}",
        f"কাস্টমার ফোন: {customer_phone}",
        f"কার্ট ভ্যালু: {amount_str} {currency}",
        f"আইটেম: {items_str}",
        f"সময়: {_bd_time_label()}",
        "",
        "টিপস: কাস্টমারকে এখনই কল দিন।",
    ])


async def _insert_notification_job_safely(db: AsyncSession, job: NotificationJob) -> bool:
    """Insert a notification job and ignore duplicate dedupe keys."""
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
        return True
    except Exception as exc:
        logger.debug("Duplicate notification job ignored (dedupe_key=%s): %s", job.dedupe_key, exc)
        return False


async def create_purchase_whatsapp_job(db: AsyncSession, client: Client, event_payload: dict) -> NotificationJob | None:
    """Queue a purchase notification job if client settings are enabled."""
    if not (client.owner_notify_whatsapp and client.owner_whatsapp_number and client.whatsapp_instance_id):
        return None

    order_id = get_field(event_payload, "custom_data.order_id") or get_field(event_payload, "event_id") or f"auto-{int(time.time())}"
    dedupe_key = f"purchase:{client.id}:{order_id}:whatsapp"

    job = NotificationJob(
        client_id=client.id,
        whatsapp_instance_id=client.whatsapp_instance_id,
        event_type="purchase",
        channel="whatsapp",
        provider="evolution",
        payload=event_payload,
        message_text=format_purchase_message(client, event_payload),
        dedupe_key=dedupe_key,
        status="pending",
        attempt_count=0,
        max_attempts=4,
    )

    inserted = await _insert_notification_job_safely(db, job)
    return job if inserted else None


async def create_incomplete_checkout_whatsapp_job(db: AsyncSession, client: Client, checkout: IncompleteCheckout) -> NotificationJob | None:
    """Queue an incomplete checkout notification job if client settings are enabled."""
    if not (client.owner_notify_whatsapp and client.owner_whatsapp_number and client.whatsapp_instance_id):
        return None

    if checkout.id:
        dedupe_key = f"incomplete_checkout:{client.id}:{checkout.id}:whatsapp"
    else:
        date_hour = datetime.now(timezone(timedelta(hours=6))).strftime("%Y-%m-%d-%H")
        dedupe_key = f"incomplete_checkout:{client.id}:{checkout.phone}:{date_hour}:whatsapp"

    checkout_payload = {
        "id": checkout.id,
        "phone": checkout.phone,
        "customer_name": checkout.customer_name,
        "email": checkout.email,
        "address": checkout.address,
        "amount": str(checkout.amount),
        "currency": checkout.currency,
        "products": checkout.products,
    }

    job = NotificationJob(
        client_id=client.id,
        whatsapp_instance_id=client.whatsapp_instance_id,
        event_type="incomplete_checkout",
        channel="whatsapp",
        provider="evolution",
        payload=checkout_payload,
        message_text=format_incomplete_checkout_message(client, checkout),
        dedupe_key=dedupe_key,
        status="pending",
        attempt_count=0,
        max_attempts=4,
    )

    inserted = await _insert_notification_job_safely(db, job)
    return job if inserted else None
