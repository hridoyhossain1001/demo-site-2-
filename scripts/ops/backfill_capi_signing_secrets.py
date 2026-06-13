"""Backfill encrypted CAPI signing secrets for clients.

Dry-run by default. Pass --confirm to write missing secrets. The script never
prints raw signing secrets; clients can receive them through the existing
portal/plugin connection endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import AsyncSessionLocal
from app.models.client import Client
from app.services.client_secrets import decrypt_capi_signing_secret, ensure_capi_signing_secret


def secret_is_valid(encrypted_value: str | None) -> bool:
    if not encrypted_value:
        return False
    try:
        return bool(decrypt_capi_signing_secret(encrypted_value))
    except Exception:
        return False


def client_summary(client: Client, *, had_valid_secret: bool, changed: bool) -> dict[str, Any]:
    return {
        "id": client.id,
        "name": client.name,
        "domain": client.domain,
        "is_active": client.is_active,
        "had_valid_secret": had_valid_secret,
        "changed": changed,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill encrypted CAPI signing secrets.")
    parser.add_argument("--confirm", action="store_true", help="Write missing encrypted signing secrets.")
    parser.add_argument("--include-inactive", action="store_true", help="Also backfill inactive clients.")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        stmt = select(Client).order_by(Client.id)
        if not args.include_inactive:
            stmt = stmt.where(Client.is_active.is_(True))
        result = await db.execute(stmt)
        clients = list(result.scalars().all())

        summaries: list[dict[str, Any]] = []
        changed_count = 0
        missing_count = 0
        invalid_count = 0
        for client in clients:
            had_value = bool(client.capi_signing_secret)
            had_valid_secret = secret_is_valid(client.capi_signing_secret)
            if not had_valid_secret:
                missing_count += 1
                if had_value:
                    invalid_count += 1

            changed = False
            if args.confirm and not had_valid_secret:
                ensure_capi_signing_secret(client)
                changed = True
                changed_count += 1
            summaries.append(client_summary(client, had_valid_secret=had_valid_secret, changed=changed))

        if args.confirm and changed_count:
            await db.commit()

        output = {
            "mode": "write" if args.confirm else "dry-run",
            "scanned_clients": len(clients),
            "missing_or_invalid_before": missing_count,
            "invalid_encrypted_values_before": invalid_count,
            "changed_count": changed_count,
            "clients": summaries,
        }
        print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
