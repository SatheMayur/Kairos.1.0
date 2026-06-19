"""Outreach API — trigger and view outreach messages."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.outreach import OutreachLog, OutreachChannel, OutreachType
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.schemas.outreach import OutreachLogCreate, OutreachLogRead
from app.services.outreach import send_outreach, send_bulk_outreach, queue_email_direct
from pydantic import BaseModel
from app.config import get_settings
from app.utils.logging import get_logger

router = APIRouter(prefix="/outreach", tags=["outreach"])
logger = get_logger(__name__)
settings = get_settings()


@router.post("/send", response_model=OutreachLogRead)
async def send_single(
    candidate_id: int,
    job_id: int,
    channel: OutreachChannel,
    outreach_type: OutreachType = OutreachType.INITIAL_CONTACT,
    db: AsyncSession = Depends(get_db),
):
    """Send a single outreach message to one candidate."""
    candidate = await _get_candidate(candidate_id, db)
    job = await _get_job(job_id, db)
    log = await send_outreach(
        candidate=candidate,
        job=job,
        channel=channel,
        outreach_type=outreach_type,
        db=db,
    )
    return log


@router.post("/bulk/{job_id}", summary="Bulk outreach to all shortlisted candidates for a job")
async def bulk_outreach(
    job_id: int,
    channel: OutreachChannel = OutreachChannel.EMAIL,
    include_contacted: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Contact all SHORTLISTED candidates for a job who have not yet been contacted.

    Set include_contacted=true to also re-blast CONTACTED candidates (useful for WhatsApp follow-up).
    """
    job = await _get_job(job_id, db)

    statuses = [ShortlistStatus.SHORTLISTED]
    if include_contacted:
        statuses.append(ShortlistStatus.CONTACTED)

    result = await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.job_id == job_id,
            ShortlistEntry.status.in_(statuses),
        )
    )
    entries = result.scalars().all()

    candidates = []
    for entry in entries:
        cand_result = await db.execute(
            select(Candidate).where(Candidate.id == entry.candidate_id)
        )
        candidate = cand_result.scalar_one_or_none()
        if candidate:
            candidates.append(candidate)

    logs = await send_bulk_outreach(
        candidates=candidates,
        job=job,
        channel=channel,
        outreach_type=OutreachType.INITIAL_CONTACT,
        db=db,
        delay_seconds=settings.outreach_delay_seconds,
    )

    # Mark CONTACTED only where a real channel actually sent (not phone-less
    # PLATFORM_MESSAGE placeholders that reach no one).
    log_by_cand = {lg.candidate_id: lg for lg in logs}
    for entry in entries:
        lg = log_by_cand.get(entry.candidate_id)
        if lg and lg.status.value == "SENT" and lg.channel in (
            OutreachChannel.WHATSAPP, OutreachChannel.EMAIL, OutreachChannel.SMS
        ):
            entry.status = ShortlistStatus.CONTACTED

    sent = len([l for l in logs if l.status.value == "SENT"])
    return {"sent": sent, "skipped": len(logs) - sent, "total": len(logs)}


@router.post("/run-all", summary="WhatsApp every un-contacted candidate (with a phone) across all jobs")
async def run_all_outreach(db: AsyncSession = Depends(get_db)):
    """One-click 'approach everyone': contact every candidate who is shortlisted OR
    pending review and HAS a phone, who hasn't been contacted yet — preferring
    WhatsApp (falls back to email if the bridge is offline). Candidates with no
    phone (e.g. Apna profiles still locked) are skipped and reported so the owner
    can unlock them. Marks CONTACTED only on a real delivered message.
    """
    from datetime import datetime, timedelta
    from app.models.wa_connection import WaConnection

    res = await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.status.in_([ShortlistStatus.SHORTLISTED, ShortlistStatus.PENDING])
        )
    )
    entries = res.scalars().all()

    conn = await db.get(WaConnection, 1)
    wa_live = bool(
        conn and conn.status == "CONNECTED" and conn.last_poll_at
        and (datetime.utcnow() - conn.last_poll_at) < timedelta(minutes=3)
    )
    channel = OutreachChannel.WHATSAPP if wa_live else OutreachChannel.EMAIL

    by_job: dict[int, list] = {}
    for e in entries:
        by_job.setdefault(e.job_id, []).append(e)

    messaged = 0
    no_phone = 0
    for job_id, job_entries in by_job.items():
        job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if not job:
            continue
        cand_map: dict[int, Candidate] = {}
        for e in job_entries:
            c = (await db.execute(select(Candidate).where(Candidate.id == e.candidate_id))).scalar_one_or_none()
            if c:
                cand_map[e.candidate_id] = c
        reachable = [c for c in cand_map.values() if (c.phone or c.whatsapp)]
        no_phone += len(cand_map) - len(reachable)
        if not reachable:
            continue
        logs = await send_bulk_outreach(
            candidates=reachable, job=job, channel=channel,
            outreach_type=OutreachType.INITIAL_CONTACT, db=db,
            delay_seconds=settings.outreach_delay_seconds,
        )
        log_by_cand = {lg.candidate_id: lg for lg in logs}
        for e in job_entries:
            lg = log_by_cand.get(e.candidate_id)
            if lg and lg.status.value == "SENT" and lg.channel in (
                OutreachChannel.WHATSAPP, OutreachChannel.EMAIL, OutreachChannel.SMS
            ):
                e.status = ShortlistStatus.CONTACTED
                messaged += 1
    await db.commit()
    return {"messaged": messaged, "no_phone_skipped": no_phone, "channel": channel.value}


class DirectEmailRequest(BaseModel):
    to: str
    subject: str
    body: str
    candidate_name: str = ""
    role: str = ""
    priority: str = "NORMAL"


@router.post("/queue-direct", summary="Queue a one-off email without a DB candidate record")
async def queue_direct(req: DirectEmailRequest):
    """Push any email straight into the Google Sheets Email Queue.

    Useful for acknowledgements, one-off replies, and admin messages.
    Apps Script picks it up within 5 minutes and sends automatically.
    """
    ok = await queue_email_direct(
        to=req.to,
        subject=req.subject,
        body=req.body,
        candidate_name=req.candidate_name,
        role=req.role,
        priority=req.priority,
    )
    if not ok:
        raise HTTPException(
            status_code=503,
            detail=(
                "Email delivery not configured. Fix (pick one): "
                "(A) Deploy AI_HR_AutoSend_v4_WITH_WEBHOOK.gs as a Google Apps Script Web App "
                "and set APPS_SCRIPT_WEB_APP_URL in Vercel — preferred, no SMTP needed. "
                "(B) Set SMTP_PASSWORD (Gmail App Password) in Vercel env vars."
            ),
        )
    return {"status": "QUEUED", "to": req.to, "subject": req.subject}


@router.get("", response_model=list[OutreachLogRead])
async def list_outreach(
    candidate_id: Optional[int] = None,
    job_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(OutreachLog)
        .offset(skip)
        .limit(limit)
        .order_by(OutreachLog.created_at.desc())
    )
    if candidate_id:
        query = query.where(OutreachLog.candidate_id == candidate_id)
    if job_id:
        query = query.where(OutreachLog.job_id == job_id)
    result = await db.execute(query)
    return result.scalars().all()


async def _get_candidate(candidate_id: int, db: AsyncSession) -> Candidate:
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return obj


async def _get_job(job_id: int, db: AsyncSession) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Job not found")
    return obj
