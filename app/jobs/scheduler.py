"""APScheduler background jobs — sourcing, outreach, reminders."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.job import Job, JobStatus
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.candidate import Candidate
from app.services.sourcing import source_candidates_for_job
from app.services.outreach import send_bulk_outreach
from app.services.scheduling import send_interview_reminders
from app.models.outreach import OutreachChannel, OutreachType
from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

scheduler = AsyncIOScheduler(timezone="UTC")


async def _job_auto_source():
    """Every 6 hours: source new candidates for all ACTIVE jobs."""
    logger.info("[SCHEDULER] Running auto-sourcing for active jobs")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.status == JobStatus.ACTIVE))
        jobs = result.scalars().all()
        for job in jobs:
            try:
                entries = await source_candidates_for_job(job, db)
                await db.commit()
                logger.info("[SCHEDULER] Sourced %d entries for job %d", len(entries), job.id)
            except Exception as exc:
                logger.error("[SCHEDULER] Sourcing failed for job %d: %s", job.id, exc)
                await db.rollback()


async def _job_auto_outreach():
    """Every hour: contact newly shortlisted candidates who haven't been reached yet."""
    if not settings.auto_outreach_enabled:
        return
    logger.info("[SCHEDULER] Running auto-outreach")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ShortlistEntry).where(ShortlistEntry.status == ShortlistStatus.SHORTLISTED)
        )
        entries = result.scalars().all()

        # Group by job
        by_job: dict[int, list[ShortlistEntry]] = {}
        for e in entries:
            by_job.setdefault(e.job_id, []).append(e)

        for job_id, job_entries in by_job.items():
            job_result = await db.execute(select(Job).where(Job.id == job_id))
            job = job_result.scalar_one_or_none()
            if not job:
                continue

            candidates = []
            for entry in job_entries:
                cand_result = await db.execute(
                    select(Candidate).where(Candidate.id == entry.candidate_id)
                )
                candidate = cand_result.scalar_one_or_none()
                if candidate:
                    candidates.append(candidate)

            try:
                logs = await send_bulk_outreach(
                    candidates=candidates,
                    job=job,
                    channel=OutreachChannel.EMAIL,
                    outreach_type=OutreachType.INITIAL_CONTACT,
                    db=db,
                    delay_seconds=settings.outreach_delay_seconds,
                )
                for entry in job_entries:
                    entry.status = ShortlistStatus.CONTACTED
                await db.commit()
                sent = sum(1 for l in logs if l.status.value == "SENT")
                logger.info("[SCHEDULER] Auto-outreach: %d sent for job %d", sent, job_id)
            except Exception as exc:
                logger.error("[SCHEDULER] Outreach failed for job %d: %s", job_id, exc)
                await db.rollback()


async def _job_send_reminders():
    """Daily at 8 AM UTC: send interview reminders for next-day interviews."""
    logger.info("[SCHEDULER] Sending interview reminders")
    async with AsyncSessionLocal() as db:
        try:
            count = await send_interview_reminders(db)
            await db.commit()
            logger.info("[SCHEDULER] Sent %d reminders", count)
        except Exception as exc:
            logger.error("[SCHEDULER] Reminder job failed: %s", exc)
            await db.rollback()


def start_scheduler() -> None:
    """Register all jobs and start the scheduler (called on app startup)."""
    scheduler.add_job(
        _job_auto_source,
        trigger=IntervalTrigger(hours=6),
        id="auto_source",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_auto_outreach,
        trigger=IntervalTrigger(hours=1),
        id="auto_outreach",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_send_reminders,
        trigger=CronTrigger(hour=8, minute=0),
        id="send_reminders",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped")
