"""Smoke test strict CAPI signing after disabling API-key fallback.

The test uses a real active client with a configured domain and signing secret.
It expects an API-key-signed legacy request to fail with 403 and a
signing-secret-signed request to succeed with 202.
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

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import AsyncSessionLocal
from app.models.client import Client
from app.services.client_secrets import decrypt_capi_signing_secret


def sign(body: bytes, secret: str, timestamp: str) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


def post_browser_audit(
    *,
    api_key: str,
    origin: str,
    body: bytes,
    secret: str,
    timestamp: str,
) -> tuple[int, dict[str, Any]]:
    request = Request(
        "http://127.0.0.1:8000/api/v1/events/browser-audit",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "X-CAPI-Origin": origin,
            "X-CAPI-Timestamp": timestamp,
            "X-CAPI-Signature": sign(body, secret, timestamp),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload or "{}")
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(payload or "{}")
        except json.JSONDecodeError:
            parsed = {"error": payload}
        return exc.code, parsed


async def load_smoke_client() -> tuple[str, str, str, str]:
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
                return client.name, client.api_key, domain, secret
    raise RuntimeError("No active domain-bound client with a decryptable CAPI signing secret was found.")


async def main() -> int:
    client_name, api_key, domain, signing_secret = await load_smoke_client()
    origin = f"https://{domain}"
    timestamp = str(int(time.time()))
    body = json.dumps(
        {
            "platform": "tiktok",
            "event_name": "PageView",
            "event_id": f"strict-smoke-{timestamp}",
            "event_source_url": origin + "/",
            "page_title": "Strict CAPI smoke",
            "user_agent": "BuykoriStrictSmoke/1.0",
        },
        separators=(",", ":"),
    ).encode("utf-8")

    legacy_status, legacy_payload = post_browser_audit(
        api_key=api_key,
        origin=origin,
        body=body,
        secret=api_key,
        timestamp=timestamp,
    )
    strict_status, strict_payload = post_browser_audit(
        api_key=api_key,
        origin=origin,
        body=body,
        secret=signing_secret,
        timestamp=timestamp,
    )
    output = {
        "client": client_name,
        "domain": domain,
        "legacy_api_key_signature_status": legacy_status,
        "strict_signing_secret_status": strict_status,
        "ok": legacy_status == 403 and strict_status == 202,
        "legacy_detail": legacy_payload.get("detail"),
        "strict_detail": strict_payload.get("detail"),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0 if output["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
