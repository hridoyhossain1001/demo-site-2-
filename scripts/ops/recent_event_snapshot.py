"""Print a compact recent production event snapshot."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import AsyncSessionLocal


QUERIES = {
    "events_60m_by_client": """
        SELECT c.name, c.domain, e.event_name, count(*) AS count, max(e.created_at) AS latest
        FROM event_logs e
        JOIN clients c ON c.id = e.client_id
        WHERE e.created_at > now() - interval '60 minutes'
        GROUP BY c.name, c.domain, e.event_name
        ORDER BY latest DESC, count DESC
    """,
    "latest_events": """
        SELECT c.name, c.domain, e.event_name, e.event_id, e.status,
               left(coalesce(e.error_message, ''), 220) AS error_message,
               e.created_at
        FROM event_logs e
        JOIN clients c ON c.id = e.client_id
        ORDER BY e.created_at DESC
        LIMIT 25
    """,
    "outbox_recent_60m": """
        SELECT status, count(*) AS count, max(created_at) AS latest
        FROM event_outbox
        WHERE created_at > now() - interval '60 minutes'
        GROUP BY status
        ORDER BY latest DESC NULLS LAST
    """,
    "failed_recent_60m": """
        SELECT status, count(*) AS count, max(created_at) AS latest
        FROM failed_events
        WHERE created_at > now() - interval '60 minutes'
        GROUP BY status
        ORDER BY latest DESC NULLS LAST
    """,
}


async def run_query(label: str, sql: str) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(sql))).all()
        return [dict(row._mapping) for row in rows]


async def main() -> int:
    snapshot = {}
    for label, sql in QUERIES.items():
        snapshot[label] = await run_query(label, sql)
    print(json.dumps(snapshot, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
