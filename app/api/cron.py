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
        "ai_enabled": bool(settings.gemini_api_key or settings.anthropic_api_key),
        "ai_provider": "gemini" if settings.gemini_api_key else ("claude" if settings.anthropic_api_key else None),
        "ai_model": settings.gemini_model if settings.gemini_api_key else (settings.claude_model if settings.anthropic_api_key else None),
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

    from app.services.auto_outreach import decide_primary_channel, contact_job_entries

    sent_total = 0
    skipped_platform = 0
    job_results: dict = {}

    async with AsyncSessionLocal() as db:
        # Decide the channel once: WhatsApp when the bridge is live, else email.
        primary_channel, wa_live = await decide_primary_channel(db)
        logger.info("[CRON/outreach] whatsapp_live=%s primary_channel=%s", wa_live, primary_channel.value)

        # Approach human-APPROVED candidates only: SHORTLISTED entries that haven't
        # been contacted yet. PENDING (AI-suggested, not yet reviewed) are NOT
        # auto-messaged — a recruiter must shortlist them first. contact_job_entries
        # additionally skips PAUSED/CLOSED jobs and unreachable candidates (the
        # latter surfaced in Needs Fixing).
        result = await db.execute(
            select(ShortlistEntry).where(
                ShortlistEntry.status == ShortlistStatus.SHORTLISTED
            )
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
            try:
                res = await contact_job_entries(db, job, job_entries, channel=primary_channel)
                await db.commit()
                sent_total += res["sent"]
                skipped_platform += res["platform"]
                job_results[job_id] = {
                    "title": job.title,
                    "sent": res["sent"],
                    "contacted": res["contacted"],
                    "platform_message": res["platform"],
                    "unreachable": res["skipped_unreachable"],
                }
                logger.info(
                    "[CRON/outreach] job=%d sent=%d contacted=%d platform=%d unreachable=%d",
                    job_id, res["sent"], res["contacted"], res["platform"], res["skipped_unreachable"],
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


@router.post("/schedule", dependencies=[Depends(_verify_secret)])
async def cron_schedule():
    """Advance INTERESTED candidates into interview scheduling.

    Closes the stall where someone who replied 'yes' (via email, manual review,
    or the front desk) had no automated next step and sat in INTERESTED forever.
    Proposes slots to anyone INTERESTED with no open interview yet.
    """
    if not settings.auto_outreach_enabled:
        return {"skipped": True, "reason": "AUTO_OUTREACH_ENABLED=false"}

    from datetime import timedelta
    from app.models.wa_connection import WaConnection
    from app.models.interview import Interview, InterviewStatus
    from app.services.scheduling import propose_interview_slots

    proposed = 0
    already = 0
    errors: dict = {}
    async with AsyncSessionLocal() as db:
        conn = await db.get(WaConnection, 1)
        wa_live = bool(
            conn and conn.status == "CONNECTED" and conn.last_poll_at
            and (datetime.utcnow() - conn.last_poll_at) < timedelta(minutes=3)
        )
        channel = OutreachChannel.WHATSAPP if wa_live else OutreachChannel.EMAIL

        entries = (await db.execute(
            select(ShortlistEntry).where(ShortlistEntry.status == ShortlistStatus.INTERESTED)
        )).scalars().all()

        for entry in entries:
            # An entry is only truly INTERVIEW_SCHEDULED once a backing Interview
            # has a real date/time (i.e. the candidate confirmed a slot). A
            # PROPOSED interview with no scheduled_at is just "slots offered" — the
            # candidate stays INTERESTED until they pick one, so the pipeline never
            # shows a scheduled interview that doesn't actually exist.
            confirmed = (await db.execute(
                select(Interview).where(
                    Interview.candidate_id == entry.candidate_id,
                    Interview.job_id == entry.job_id,
                    Interview.scheduled_at.isnot(None),
                )
            )).scalars().first()
            if confirmed:
                entry.status = ShortlistStatus.INTERVIEW_SCHEDULED
                already += 1
                continue

            # Already have open (proposed) slots out — don't re-propose, and don't
            # mark scheduled yet (no time confirmed). Leave as INTERESTED.
            pending_iv = (await db.execute(
                select(Interview).where(
                    Interview.candidate_id == entry.candidate_id,
                    Interview.job_id == entry.job_id,
                    Interview.status.in_([InterviewStatus.PROPOSED, InterviewStatus.RESCHEDULED]),
                )
            )).scalars().first()
            if pending_iv:
                already += 1
                continue

            cand = await db.get(Candidate, entry.candidate_id)
            job = await db.get(Job, entry.job_id)
            if not cand or not job:
                continue
            try:
                # Sends slot options and creates a PROPOSED interview (no time yet).
                # Candidate stays INTERESTED until they confirm a slot (webhook /
                # confirmation link sets scheduled_at + INTERVIEW_SCHEDULED then).
                await propose_interview_slots(candidate=cand, job=job, channel=channel, db=db)
                proposed += 1
            except Exception as exc:
                errors[str(entry.candidate_id)] = str(exc)[:150]
                logger.error("[CRON/schedule] candidate=%d failed: %s", entry.candidate_id, exc)
        await db.commit()

    return {
        "ran_at": datetime.utcnow().isoformat() + "Z",
        "slots_proposed": proposed,
        "already_scheduled": already,
        "errors": errors,
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
    await _step("schedule", cron_schedule())
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
