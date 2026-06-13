"""Strict-mode E2E tracking smoke for the production server.

The script sends a small signed batch for an active domain-bound client and
checks that the server accepts it and persists the expected outbox rows.
It does not print API keys or signing secrets.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select, text


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import AsyncSessionLocal
from app.models.client import Client
from app.services.client_secrets import decrypt_capi_signing_secret


EVENT_NAMES = ["PageView", "ViewContent", "AddToCart", "InitiateCheckout", "Purchase"]


def sign(body: bytes, secret: str, timestamp: str) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


async def load_smoke_client() -> tuple[int, str, str, str, str]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Client)
            .where(Client.is_active.is_(True))
            .where(Client.domain.is_not(None))
            .order_by(Client.id.desc())
        )
        for client in result.scalars().all():
            domain = (client.domain or "").split(",")[0].strip().lower()
            if not domain:
                continue
            secret = decrypt_capi_signing_secret(client.capi_signing_secret)
            if secret:
                return client.id, client.name, client.api_key, domain, secret
    raise RuntimeError("No active domain-bound client with a decryptable CAPI signing secret was found.")


def post_events(*, api_key: str, origin: str, body: bytes, secret: str, timestamp: str) -> tuple[int, dict[str, Any]]:
    request = Request(
        "http://127.0.0.1:8000/api/v1/events?force_send=true",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "BuykoriE2ESmoke/1.0",
            "X-API-Key": api_key,
            "X-Buykori-Version": "ops-e2e-smoke",
            "X-Buykori-Installation-ID": "ops-e2e-smoke",
            "X-CAPI-Origin": origin,
            "X-CAPI-Timestamp": timestamp,
            "X-CAPI-Signature": sign(body, secret, timestamp),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload or "{}")
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(payload or "{}")
        except json.JSONDecodeError:
            parsed = {"error": payload}
        return exc.code, parsed


async def outbox_snapshot(client_id: int, event_prefix: str) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                """
                SELECT
                    status,
                    attempts,
                    event_payload,
                    created_at
                FROM event_outbox
                WHERE client_id = :client_id
                  AND CAST(event_payload AS TEXT) LIKE :event_prefix
                ORDER BY created_at DESC
                """
            ),
            {"client_id": client_id, "event_prefix": "%" + event_prefix + "%"},
        )
        rows: list[dict[str, Any]] = []
        for row in result:
            data = dict(row._mapping)
            payload = data.pop("event_payload") or []
            events = payload if isinstance(payload, list) else [payload]
            matched_events = [
                {
                    "event_name": event.get("event_name"),
                    "event_id": event.get("event_id"),
                }
                for event in events
                if isinstance(event, dict) and str(event.get("event_id") or "").startswith(event_prefix)
            ]
            data["matched_event_count"] = len(matched_events)
            data["matched_events"] = matched_events
            rows.append(data)
        return rows


async def event_log_snapshot(client_id: int, event_prefix: str) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                """
                SELECT event_name, event_id, status, error_message, created_at
                FROM event_logs
                WHERE client_id = :client_id
                  AND event_id LIKE :event_prefix
                ORDER BY created_at DESC
                """
            ),
            {"client_id": client_id, "event_prefix": event_prefix + "%"},
        )
        return [dict(row._mapping) for row in result]


async def wait_for_delivery_snapshot(client_id: int, event_prefix: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outbox_rows: list[dict[str, Any]] = []
    log_rows: list[dict[str, Any]] = []
    for _ in range(10):
        outbox_rows = await outbox_snapshot(client_id, event_prefix)
        log_rows = await event_log_snapshot(client_id, event_prefix)
        outbox_event_count = sum(int(row.get("matched_event_count") or 0) for row in outbox_rows)
        if outbox_event_count >= len(EVENT_NAMES) or len(log_rows) >= len(EVENT_NAMES):
            break
        await asyncio.sleep(1.5)
    return outbox_rows, log_rows


async def main() -> int:
    client_id, client_name, api_key, domain, signing_secret = await load_smoke_client()
    origin = f"https://{domain}"
    now = int(time.time())
    prefix = f"ops-e2e-{now}"
    events: list[dict[str, Any]] = []
    for index, event_name in enumerate(EVENT_NAMES, start=1):
        event_id = f"{prefix}-{index}-{event_name.lower()}"
        event: dict[str, Any] = {
            "event_name": event_name,
            "event_time": now,
            "event_id": event_id,
            "event_source_url": f"{origin}/ops-smoke",
            "action_source": "website",
            "user_data": {
                "em": ["smoke@example.com"],
                "ph": ["01700000000"],
                "client_ip_address": "127.0.0.1",
                "client_user_agent": "BuykoriE2ESmoke/1.0",
            },
            "custom_data": {
                "currency": "BDT",
                "value": 123.0,
                "content_ids": ["ops-smoke-product"],
                "content_type": "product",
                "num_items": 1,
                "contents": [{"id": "ops-smoke-product", "quantity": 1, "item_price": 123.0}],
            },
        }
        if event_name == "Purchase":
            event["custom_data"]["order_id"] = f"{prefix}-order"
        events.append(event)

    body = json.dumps({"data": events}, separators=(",", ":")).encode("utf-8")
    status, payload = post_events(
        api_key=api_key,
        origin=origin,
        body=body,
        secret=signing_secret,
        timestamp=str(now),
    )
    rows, log_rows = await wait_for_delivery_snapshot(client_id, prefix)
    matched_event_count = sum(int(row.get("matched_event_count") or 0) for row in rows)
    persisted_or_logged_count = max(matched_event_count, len(log_rows))
    output = {
        "client": client_name,
        "domain": domain,
        "request_status": status,
        "events_sent": len(events),
        "events_received": payload.get("events_received"),
        "outbox_rows": len(rows),
        "matched_outbox_events": matched_event_count,
        "outbox_statuses": rows,
        "event_log_rows": len(log_rows),
        "event_logs": log_rows,
        "ok": (
            status == 202
            and payload.get("events_received") == len(events)
            and persisted_or_logged_count == len(events)
        ),
        "detail": payload.get("detail"),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0 if output["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
