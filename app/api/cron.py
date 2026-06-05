"""Secured cron endpoints — triggered by Vercel Cron or any external scheduler.

All POST endpoints require the X-Cron-Secret header to match settings.cron_secret.
If cron_secret is empty (local dev), the endpoints are open.

Routes:
  GET  /cron/status    — health check, no auth
  POST /cron/source    — source candidates for all active jobs   (every 6h)
  POST /cron/outreach  — send outreach to shortlisted candidates (every 1h)
  POST /cron/reminders — send interview reminders (24h window)   (daily 8AM UTC)
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.candidate import Candidate
from app.models.job import Job, JobStatus
from app.models.outreach import OutreachChannel, OutreachType
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.digest import generate_digest
from app.services.followup import send_followups
from app.services.outreach import send_bulk_outreach, queue_email_direct
from app.services.post_interview import process_completed_interviews
from app.services.scheduling import send_interview_reminders
from app.services.sourcing import source_candidates_for_job
from app.utils.logging import get_logger
from app.utils.error_log import log_error

logger = get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/cron", tags=["cron"])


def _verify_secret(x_cron_secret: str = Header(default="")):
    if not settings.cron_secret:
        return
    if x_cron_secret != settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron secret"
        )


@router.get("/status")
async def cron_status():
    return {
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "env": settings.app_env,
        "auto_outreach": settings.auto_outreach_enabled,
    }


@router.post("/source", dependencies=[Depends(_verify_secret)])
async def cron_source():
    """Source new candidates for all active jobs and auto-shortlist them."""
    job_results: dict = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.status == JobStatus.ACTIVE))
        jobs = result.scalars().all()
        for job in jobs:
            try:
                entries = await source_candidates_for_job(job, db)
                await db.commit()
                job_results[job.id] = {"title": job.title, "new_entries": len(entries)}
                logger.info("[CRON/source] job=%d sourced %d entries", job.id, len(entries))
            except Exception as exc:
                await db.rollback()
                job_results[job.id] = {"title": job.title, "error": str(exc)[:200]}
                logger.error("[CRON/source] job=%d failed: %s", job.id, exc)
                await log_error(message=str(exc), source="cron:source", exc=exc, path=f"/cron/source/job/{job.id}")
    return {"ran_at": datetime.utcnow().isoformat() + "Z", "jobs": job_results}


@router.post("/outreach", dependencies=[Depends(_verify_secret)])
async def cron_outreach():
    """Contact all SHORTLISTED candidates who haven't been reached yet."""
    if not settings.auto_outreach_enabled:
        return {"skipped": True, "reason": "AUTO_OUTREACH_ENABLED=false"}

    sent_total = 0
    skipped_platform = 0
    job_results: dict = {}

    async with AsyncSessionLocal() as db:
        # Decide the channel once: prefer WhatsApp, but if the bridge is offline
        # (no poll in the last 3 minutes) fall back to email so candidates are
        # still reached even when nobody's computer is running the bridge.
        from datetime import timedelta
        from app.models.wa_connection import WaConnection
        conn = await db.get(WaConnection, 1)
        wa_live = bool(
            conn and conn.status == "CONNECTED" and conn.last_poll_at
            and (datetime.utcnow() - conn.last_poll_at) < timedelta(minutes=3)
        )
        primary_channel = OutreachChannel.WHATSAPP if wa_live else OutreachChannel.EMAIL
        logger.info("[CRON/outreach] whatsapp_live=%s primary_channel=%s", wa_live, primary_channel.value)

        result = await db.execute(
            select(ShortlistEntry).where(ShortlistEntry.status == ShortlistStatus.SHORTLISTED)
        )
        entries = result.scalars().all()

        by_job: dict[int, list[ShortlistEntry]] = {}
        for e in entries:
            by_job.setdefault(e.job_id, []).append(e)

        for job_id, job_entries in by_job.items():
            job_res = await db.execute(select(Job).where(Job.id == job_id))
            job = job_res.scalar_one_or_none()
            if not job:
                continue

            candidates: list[Candidate] = []
            for entry in job_entries:
                c_res = await db.execute(
                    select(Candidate).where(Candidate.id == entry.candidate_id)
                )
                c = c_res.scalar_one_or_none()
                if c:
                    candidates.append(c)

            try:
                # primary_channel decided above: WhatsApp when the bridge is
                # live, otherwise email (so the autopilot never stalls).
                logs = await send_bulk_outreach(
                    candidates=candidates,
                    job=job,
                    channel=primary_channel,
                    outreach_type=OutreachType.INITIAL_CONTACT,
                    db=db,
                    delay_seconds=settings.outreach_delay_seconds,
                )
                for entry in job_entries:
                    entry.status = ShortlistStatus.CONTACTED
                await db.commit()

                sent = sum(1 for lg in logs if lg.status.value == "SENT")
                platform = sum(1 for lg in logs if lg.channel.value == "PLATFORM_MESSAGE")
                unreachable = sum(1 for lg in logs if lg.channel.value == "UNREACHABLE")
                sent_total += sent
                skipped_platform += platform

                job_results[job_id] = {
                    "title": job.title,
                    "sent": sent,
                    "platform_message": platform,
                    "unreachable": unreachable,
                }
                logger.info(
                    "[CRON/outreach] job=%d sent=%d platform=%d unreachable=%d",
                    job_id, sent, platform, unreachable,
                )
            except Exception as exc:
                await db.rollback()
                job_results[job_id] = {"title": job.title, "error": str(exc)[:200]}
                logger.error("[CRON/outreach] job=%d failed: %s", job_id, exc)
                await log_error(message=str(exc), source="cron:outreach", exc=exc, path=f"/cron/outreach/job/{job_id}")

    return {
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "sent_total": sent_total,
        "platform_messages": skipped_platform,
        "channel": primary_channel.value,
        "whatsapp_live": wa_live,
        "jobs": job_results,
    }


@router.post("/reminders", dependencies=[Depends(_verify_secret)])
async def cron_reminders():
    """Send interview reminders for confirmed interviews in the next 24 hours."""
    async with AsyncSessionLocal() as db:
        try:
            count = await send_interview_reminders(db)
            await db.commit()
            logger.info("[CRON/reminders] sent %d reminders", count)
            return {"ran_at": datetime.utcnow().isoformat() + "Z", "reminders_sent": count}
        except Exception as exc:
            await db.rollback()
            logger.error("[CRON/reminders] failed: %s", exc)
            await log_error(message=str(exc), source="cron:reminders", exc=exc, path="/cron/reminders")
            return {
                "ran_at": datetime.utcnow().isoformat() + "Z",
                "error": str(exc)[:200],
            }


@router.post("/followup", dependencies=[Depends(_verify_secret)])
async def cron_followup():
    """Send day-3 / day-6 follow-ups for CONTACTED candidates with no reply.
    Marks candidates as DROPPED after day-9 silence.
    """
    async with AsyncSessionLocal() as db:
        try:
            result = await send_followups(db)
            await db.commit()
            logger.info(
                "[CRON/followup] fu1=%d fu2=%d dropped=%d",
                result["followup1_sent"], result["followup2_sent"], result["dropped"],
            )
            return {"ran_at": datetime.utcnow().isoformat() + "Z", **result}
        except Exception as exc:
            await db.rollback()
            logger.error("[CRON/followup] failed: %s", exc)
            await log_error(message=str(exc), source="cron:followup", exc=exc, path="/cron/followup")
            return {"ran_at": datetime.utcnow().isoformat() + "Z", "error": str(exc)[:200]}


@router.post("/post-interview", dependencies=[Depends(_verify_secret)])
async def cron_post_interview():
    """Auto-complete overdue interviews and nudge candidates with unconfirmed slots."""
    async with AsyncSessionLocal() as db:
        try:
            result = await process_completed_interviews(db)
            await db.commit()
            logger.info(
                "[CRON/post-interview] completed=%d nudges=%d",
                result["auto_completed"], result["slot_nudges_sent"],
            )
            return {"ran_at": datetime.utcnow().isoformat() + "Z", **result}
        except Exception as exc:
            await db.rollback()
            logger.error("[CRON/post-interview] failed: %s", exc)
            await log_error(message=str(exc), source="cron:post-interview", exc=exc, path="/cron/post-interview")
            return {"ran_at": datetime.utcnow().isoformat() + "Z", "error": str(exc)[:200]}


@router.post("/digest", dependencies=[Depends(_verify_secret)])
async def cron_digest():
    """Send the morning HR pipeline digest to the recruiter."""
    if not settings.digest_enabled:
        return {"skipped": True, "reason": "DIGEST_ENABLED=false"}

    async with AsyncSessionLocal() as db:
        try:
            subject, body = await generate_digest(db)
            ok = await queue_email_direct(
                to=settings.digest_recipient_email,
                subject=subject,
                body=body,
                candidate_name="Kirti Chand",
                role="HR Manager",
                priority="HIGH",
            )
            logger.info("[CRON/digest] sent=%s subject=%s", ok, subject[:60])
            return {
                "ran_at": datetime.utcnow().isoformat() + "Z",
                "sent": ok,
                "subject": subject,
            }
        except Exception as exc:
            logger.error("[CRON/digest] failed: %s", exc)
            await log_error(message=str(exc), source="cron:digest", exc=exc, path="/cron/digest")
            return {"ran_at": datetime.utcnow().isoformat() + "Z", "error": str(exc)[:200]}


@router.post("/watchdog", dependencies=[Depends(_verify_secret)])
async def cron_watchdog():
    """Self-healing watchdog — detects failures, retries, and alerts. Runs every 30 min."""
    from app.services.watchdog import run_watchdog
    results = await run_watchdog()
    return {"ran_at": datetime.utcnow().isoformat() + "Z", **results}


@router.post("/daily", dependencies=[Depends(_verify_secret)])
async def cron_daily():
    """The full daily routine — runs by itself every day at 10:00 AM IST.

    Does all the routine recruitment work end to end (find candidates → score →
    contact → follow up → wrap up interviews → remind), then emails Kirti a
    briefing that summarises what was done and lists only the few things that
    still need a human decision. No human action is required to start this.
    """
    results: dict = {}

    async def _step(name, coro):
        try:
            results[name] = await coro
        except Exception as exc:  # never let one step abort the rest
            logger.error("[CRON/daily] step '%s' failed: %s", name, exc)
            await log_error(message=str(exc), source=f"cron:daily:{name}", exc=exc, path="/cron/daily")
            results[name] = {"error": str(exc)[:200]}

    # Reasoning step first: the manager agent looks at today's state and plans.
    # This is advisory — it explains and prioritises; the steps below still run
    # deterministically with their own guardrails.
    try:
        from app.services.orchestrator import generate_plan
        async with AsyncSessionLocal() as db:
            await generate_plan(db, persist=True)
            await db.commit()
    except Exception as exc:
        logger.error("[CRON/daily] planning failed: %s", exc)
        await log_error(message=str(exc), source="cron:daily:plan", exc=exc, path="/cron/daily")

    await _step("source", cron_source())
    await _step("outreach", cron_outreach())
    await _step("followup", cron_followup())
    await _step("post_interview", cron_post_interview())
    await _step("reminders", cron_reminders())

    # Final step: send the daily briefing summarising the run above
    digest_sent = False
    if settings.digest_enabled:
        async with AsyncSessionLocal() as db:
            try:
                subject, body = await generate_digest(db, run_results=results)
                digest_sent = await queue_email_direct(
                    to=settings.digest_recipient_email,
                    subject=subject,
                    body=body,
                    candidate_name="Kirti Chand",
                    role="HR Manager",
                    priority="HIGH",
                )
            except Exception as exc:
                logger.error("[CRON/daily] digest failed: %s", exc)
                await log_error(message=str(exc), source="cron:daily:digest", exc=exc, path="/cron/daily")

    results["digest_sent"] = digest_sent
    results["ran_at"] = datetime.utcnow().isoformat() + "Z"
    logger.info("[CRON/daily] complete — digest_sent=%s", digest_sent)
    return results
