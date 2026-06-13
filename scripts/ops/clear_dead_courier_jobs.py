"""List or delete dead courier booking jobs.

The default mode is a dry-run. Pass --confirm-delete to remove only rows whose
status is exactly "dead" from courier_booking_jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import AsyncSessionLocal
from app.models.courier_booking_job import CourierBookingJob


def serialize_job(job: CourierBookingJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "client_id": job.client_id,
        "courier_order_id": job.courier_order_id,
        "provider": job.provider,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "last_error": (job.last_error or "")[:240],
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="List or delete dead courier booking jobs.")
    parser.add_argument("--confirm-delete", action="store_true", help="Delete dead courier booking jobs.")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CourierBookingJob)
            .where(CourierBookingJob.status == "dead")
            .order_by(CourierBookingJob.created_at.desc())
        )
        jobs = list(result.scalars().all())
        output: dict[str, Any] = {
            "mode": "delete" if args.confirm_delete else "dry-run",
            "dead_count": len(jobs),
            "jobs": [serialize_job(job) for job in jobs],
        }
        if args.confirm_delete and jobs:
            await db.execute(delete(CourierBookingJob).where(CourierBookingJob.status == "dead"))
            await db.commit()
            output["deleted_count"] = len(jobs)
        else:
            output["deleted_count"] = 0
        print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
