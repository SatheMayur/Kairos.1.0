"""Automated outreach — the ONE place that decides 'who do we contact, on which
channel, right now', and turns a reachable shortlist entry into a sent message.

Used by:
  • cron/outreach          — hourly/daily sweep of everyone lined up
  • sourcing               — contact good matches the moment they're sourced
  • candidate add / import  — contact the moment a reachable candidate enters a pipeline

Hard rules (the owner set these):
  • Only PENDING or SHORTLISTED entries are auto-contacted (never rejected/closed,
    never someone already contacted or further along).
  • Only candidates we can actually reach (valid mobile or real email). A locked
    Apna profile / junk number is left for 'Needs contact info', never messaged.
  • CONTACTED is set ONLY when a message was really SENT on a real channel
    (WhatsApp / Email / SMS) — a platform placeholder or a failure keeps the
    entry SHORTLISTED so it's retried or surfaced, never falsely 'contacted'.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.outreach import OutreachChannel, OutreachType
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.data_quality import is_reachable
from app.services.outreach import send_bulk_outreach
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Channels that actually deliver to a person (vs. a manual-platform placeholder).
_REAL_CHANNELS = (OutreachChannel.WHATSAPP, OutreachChannel.EMAIL, OutreachChannel.SMS)
_CONTACTABLE = (ShortlistStatus.SHORTLISTED, ShortlistStatus.PENDING)


async def decide_primary_channel(db: AsyncSession) -> tuple[OutreachChannel, bool]:
    """Pick the channel to use right now: WhatsApp when the bridge is live (polled
    in the last 3 min), otherwise Email — so outreach never stalls when nobody's
    computer is running the WhatsApp bridge. Returns (channel, whatsapp_live)."""
    from app.models.wa_connection import WaConnection

    conn = await db.get(WaConnection, 1)
    wa_live = bool(
        conn and conn.status == "CONNECTED" and conn.last_poll_at
        and (datetime.utcnow() - conn.last_poll_at) < timedelta(minutes=3)
    )
    return (OutreachChannel.WHATSAPP if wa_live else OutreachChannel.EMAIL), wa_live


async def contact_job_entries(
    db: AsyncSession,
    job: Job,
    entries: list[ShortlistEntry],
    *,
    channel: OutreachChannel | None = None,
) -> dict:
    """Send initial contact to every reachable, not-yet-contacted PENDING/SHORTLISTED
    entry for ONE job. Flips them to CONTACTED on a real send. Idempotent: an entry
    already CONTACTED or further along is skipped, so re-running never double-messages.
    """
    if channel is None:
        channel, _ = await decide_primary_channel(db)

    # Which entries are eligible, and load their candidates.
    eligible: list[tuple[ShortlistEntry, Candidate]] = []
    cand_by_id: dict[int, Candidate] = {}
    skipped_unreachable = 0
    for entry in entries:
        if entry.status not in _CONTACTABLE:
            continue
        c = await db.get(Candidate, entry.candidate_id)
        if not c:
            continue
        cand_by_id[c.id] = c
        if is_reachable(c):
            eligible.append((entry, c))
        else:
            skipped_unreachable += 1

    if not eligible:
        return {"sent": 0, "contacted": 0, "skipped_unreachable": skipped_unreachable,
                "platform": 0, "channel": channel.value}

    logs = await send_bulk_outreach(
        candidates=[c for _, c in eligible],
        job=job,
        channel=channel,
        outreach_type=OutreachType.INITIAL_CONTACT,
        db=db,
        delay_seconds=settings.outreach_delay_seconds,
    )
    log_by_cand = {lg.candidate_id: lg for lg in logs}

    contacted = 0
    for entry, c in eligible:
        lg = log_by_cand.get(c.id)
        if (lg and lg.status.value == "SENT" and lg.channel in _REAL_CHANNELS
                and is_reachable(c)):
            entry.status = ShortlistStatus.CONTACTED
            contacted += 1

    sent = sum(1 for lg in logs if lg.status.value == "SENT")
    platform = sum(1 for lg in logs if lg.channel.value == "PLATFORM_MESSAGE")
    return {"sent": sent, "contacted": contacted,
            "skipped_unreachable": skipped_unreachable,
            "platform": platform, "channel": channel.value}


async def auto_outreach_after_sourcing(
    db: AsyncSession, job: Job, entries: list[ShortlistEntry]
) -> dict:
    """Contact freshly-sourced good matches immediately — but only if the owner has
    auto-outreach turned on. Never raises (sourcing must not fail because a message
    couldn't be queued)."""
    if not settings.auto_outreach_enabled:
        return {"skipped": True, "reason": "auto_outreach_disabled"}
    try:
        return await contact_job_entries(db, job, entries)
    except Exception as exc:  # outreach must never break the sourcing run
        logger.warning("auto_outreach_after_sourcing failed for job %d: %s", job.id, exc)
        return {"error": str(exc)[:200]}


async def contact_candidate_now(db: AsyncSession, candidate_id: int) -> dict:
    """Contact ONE candidate across all the jobs they're lined up for (PENDING/
    SHORTLISTED). Used by the 'Contact' button and the on-add hook for a single
    candidate. Returns a per-job summary."""
    entries = (await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.candidate_id == candidate_id,
            ShortlistEntry.status.in_(_CONTACTABLE),
        )
    )).scalars().all()
    if not entries:
        return {"contacted": 0, "reason": "no_open_pipeline_entry"}

    channel, _ = await decide_primary_channel(db)
    by_job: dict[int, list[ShortlistEntry]] = {}
    for e in entries:
        by_job.setdefault(e.job_id, []).append(e)

    total_contacted = 0
    jobs_done = []
    for job_id, job_entries in by_job.items():
        job = await db.get(Job, job_id)
        if not job:
            continue
        res = await contact_job_entries(db, job, job_entries, channel=channel)
        total_contacted += res.get("contacted", 0)
        jobs_done.append({"job_id": job_id, **res})
    return {"contacted": total_contacted, "channel": channel.value, "jobs": jobs_done}
