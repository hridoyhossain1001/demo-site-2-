import asyncio
import logging
import os
import random
import socket
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_, or_
from app.database import AsyncSessionLocal
from app.models.notification_job import NotificationJob
from app.models.whatsapp_instance import WhatsAppInstance
from app.models.client import Client
from app.services.whatsapp_provider import EvolutionWhatsAppProvider

logger = logging.getLogger(__name__)

WORKER_ID = os.getenv("NOTIFICATION_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
WORKER_BATCH_SIZE = int(os.getenv("NOTIFICATION_WORKER_BATCH_SIZE", "5"))
WORKER_POLL_SECONDS = float(os.getenv("NOTIFICATION_WORKER_POLL_SECONDS", "5.0"))
WORKER_LOCK_DURATION_MINUTES = int(os.getenv("NOTIFICATION_WORKER_LOCK_DURATION_MINUTES", "5"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_attempt_after(attempts: int) -> datetime:
    # Retries: 1 min, 5 min, 15 min
    delays = [60, 300, 900]
    delay = delays[min(max(attempts - 1, 0), len(delays) - 1)]
    return _now() + timedelta(seconds=delay)


async def claim_due_jobs(db, limit: int = WORKER_BATCH_SIZE) -> list[NotificationJob]:
    """Atomic claim of notification jobs that are pending or retryable."""
    now = _now()
    stmt = (
        select(NotificationJob)
        .where(
            and_(
                or_(
                    NotificationJob.status.in_(["pending", "failed"]),
                    and_(
                        NotificationJob.status == "processing",
                        NotificationJob.locked_until.is_not(None),
                        NotificationJob.locked_until <= now,
                    ),
                ),
                NotificationJob.attempt_count < NotificationJob.max_attempts,
                or_(NotificationJob.next_attempt_at.is_(None), NotificationJob.next_attempt_at <= now),
                or_(NotificationJob.locked_until.is_(None), NotificationJob.locked_until <= now),
            )
        )
        .order_by(NotificationJob.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    for job in jobs:
        job.status = "processing"
        job.locked_by = WORKER_ID
        job.locked_until = now + timedelta(minutes=WORKER_LOCK_DURATION_MINUTES)

    if jobs:
        await db.commit()
    else:
        await db.rollback()

    return jobs


async def process_jobs_with_instance_limits(jobs: list[NotificationJob]) -> list[object]:
    """Process a batch while keeping sends from the same WhatsApp instance serial."""
    groups: dict[int | None, list[int]] = {}
    for job in jobs:
        groups.setdefault(job.whatsapp_instance_id, []).append(job.id)

    async def process_group(job_ids: list[int]) -> list[object]:
        results: list[object] = []
        for job_id in job_ids:
            try:
                await process_job(job_id)
                results.append(None)
            except Exception as exc:
                results.append(exc)
        return results

    grouped_results = await asyncio.gather(
        *(process_group(job_ids) for job_ids in groups.values()),
        return_exceptions=True,
    )
    results: list[object] = []
    for group_result in grouped_results:
        if isinstance(group_result, Exception):
            results.append(group_result)
        else:
            results.extend(group_result)
    return results


async def process_job(job_id: int) -> None:
    """Processes a single WhatsApp notification job."""
    async with AsyncSessionLocal() as db:
        job = await db.get(NotificationJob, job_id)
        if not job or job.status == "sent" or job.attempt_count >= job.max_attempts:
            return

        client = await db.get(Client, job.client_id)
        if not client or not client.is_active:
            job.status = "failed"
            job.error_message = "Client inactive or missing"
            job.locked_by = None
            job.locked_until = None
            await db.commit()
            return

        # Fetch instance config
        instance = None
        if job.whatsapp_instance_id:
            instance = await db.get(WhatsAppInstance, job.whatsapp_instance_id)

        if not instance or instance.status != "active":
            job.status = "failed"
            job.error_message = "WhatsApp instance not active or missing"
            job.locked_by = None
            job.locked_until = None
            await db.commit()
            return

        if not client.owner_whatsapp_number:
            job.status = "failed"
            job.error_message = "Client owner WhatsApp number is missing"
            job.locked_by = None
            job.locked_until = None
            await db.commit()
            return

    # Per-instance delay of 3-5 seconds to prevent spam trigger
    await asyncio.sleep(random.uniform(3.0, 5.0))

    try:
        # Send text via provider
        await EvolutionWhatsAppProvider.send_text(
            instance_name=instance.instance_name,
            to_number=client.owner_whatsapp_number,
            message=job.message_text,
            base_url=instance.base_url,
        )

        # Update status to sent on success
        async with AsyncSessionLocal() as db:
            db_job = await db.get(NotificationJob, job_id)
            db_instance = await db.get(WhatsAppInstance, instance.id)
            if db_job:
                db_job.status = "sent"
                db_job.sent_at = _now()
                db_job.error_message = None
                db_job.locked_by = None
                db_job.locked_until = None
                db_job.attempt_count += 1
            if db_instance:
                db_instance.last_sent_at = _now()
            await db.commit()
            logger.info(f"Successfully sent WhatsApp notification job {job_id}")

    except Exception as exc:
        error_msg = str(exc)[:500]
        async with AsyncSessionLocal() as db:
            db_job = await db.get(NotificationJob, job_id)
            if db_job:
                db_job.attempt_count += 1
                db_job.error_message = error_msg
                db_job.locked_by = None
                db_job.locked_until = None

                if db_job.attempt_count >= db_job.max_attempts:
                    db_job.status = "failed"
                    logger.error(f"WhatsApp notification job {job_id} permanently failed after {db_job.max_attempts} attempts: {error_msg}")
                else:
                    db_job.status = "failed"
                    db_job.next_attempt_at = _next_attempt_after(db_job.attempt_count)
                    logger.warning(f"WhatsApp notification job {job_id} failed. Next retry at {db_job.next_attempt_at}. Error: {error_msg}")
            await db.commit()


async def run_notification_worker_forever() -> None:
    """Background worker daemon loop."""
    logger.info(f"WhatsApp notification worker started: {WORKER_ID}")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                jobs = await claim_due_jobs(db)
            if jobs:
                results = await process_jobs_with_instance_limits(jobs)
                for res in results:
                    if isinstance(res, Exception):
                        logger.error(f"Error in notification worker task processing: {res}", exc_info=res)
            else:
                await asyncio.sleep(WORKER_POLL_SECONDS)
        except Exception as exc:
            logger.error(f"WhatsApp notification worker loop error: {exc}")
            await asyncio.sleep(WORKER_POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_notification_worker_forever())
