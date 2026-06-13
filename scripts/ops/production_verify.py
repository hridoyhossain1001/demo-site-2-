"""Read-only production health and rollout-readiness snapshot."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import AsyncSessionLocal
from app.models.client import Client
from app.services.client_secrets import decrypt_capi_signing_secret
from scripts.ops.staging_smoke_check import run_smoke


async def query_rows(statement: str) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(text(statement))
        return [dict(row._mapping) for row in result]


async def client_secret_snapshot() -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Client).where(Client.is_active.is_(True)).order_by(Client.id))
        clients = list(result.scalars().all())

    valid: list[dict[str, Any]] = []
    missing_or_invalid: list[dict[str, Any]] = []
    for client in clients:
        try:
            has_valid_secret = bool(decrypt_capi_signing_secret(client.capi_signing_secret))
        except Exception:
            has_valid_secret = False
        entry = {"id": client.id, "name": client.name, "domain": client.domain}
        if has_valid_secret:
            valid.append(entry)
        else:
            missing_or_invalid.append(entry)
    return {
        "active_total": len(clients),
        "active_with_decryptable_signing_secret": len(valid),
        "active_missing_or_invalid_signing_secret": len(missing_or_invalid),
        "missing_or_invalid_clients": missing_or_invalid,
    }


async def database_snapshot() -> dict[str, Any]:
    clients = await query_rows(
        """
        SELECT
            count(*) AS total_clients,
            count(*) FILTER (WHERE is_active) AS active_clients,
            count(*) FILTER (
                WHERE is_active
                  AND capi_signing_secret IS NOT NULL
                  AND capi_signing_secret <> ''
            ) AS active_with_signing_secret,
            count(*) FILTER (
                WHERE is_active
                  AND (capi_signing_secret IS NULL OR capi_signing_secret = '')
            ) AS active_missing_signing_secret
        FROM clients
        """
    )
    missing_signing_secrets = await query_rows(
        """
        SELECT id, name, domain
        FROM clients
        WHERE is_active
          AND (capi_signing_secret IS NULL OR capi_signing_secret = '')
        ORDER BY id
        """
    )
    queues: dict[str, list[dict[str, Any]]] = {}
    for table in ("failed_events", "event_outbox", "courier_booking_jobs"):
        queues[table] = await query_rows(
            f"SELECT status, count(*) AS count FROM {table} GROUP BY status ORDER BY status"
        )
    dead_courier_jobs = await query_rows(
        """
        SELECT id, client_id, courier_order_id, provider, attempts, max_attempts,
               left(coalesce(last_error, ''), 240) AS last_error, created_at
        FROM courier_booking_jobs
        WHERE status = 'dead'
        ORDER BY created_at DESC
        LIMIT 20
        """
    )
    return {
        "clients": clients[0],
        "client_secret_readiness": await client_secret_snapshot(),
        "active_clients_missing_signing_secret": missing_signing_secrets,
        "queues": queues,
        "dead_courier_jobs": dead_courier_jobs,
    }


async def main() -> int:
    admin_api_key = os.getenv("ADMIN_API_KEY")
    if not admin_api_key:
        raise SystemExit("ADMIN_API_KEY is required")

    smoke = run_smoke("http://127.0.0.1:8000", admin_api_key)
    snapshot = {
        "smoke": [
            {
                "name": result.name,
                "ok": result.ok,
                "status_code": result.status_code,
                "message": result.message,
            }
            for result in smoke
        ],
        "database": await database_snapshot(),
    }
    print(json.dumps(snapshot, indent=2, default=str))
    return 0 if all(result.ok for result in smoke) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
