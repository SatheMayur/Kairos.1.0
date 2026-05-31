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
from app.services.outreach import send_outreach, send_bulk_outreach
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
    db: AsyncSession = Depends(get_db),
):
    """Contact all SHORTLISTED candidates for a job who have not yet been contacted."""
    job = await _get_job(job_id, db)

    result = await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.job_id == job_id,
            ShortlistEntry.status == ShortlistStatus.SHORTLISTED,
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

    # Update shortlist status to CONTACTED
    for entry in entries:
        entry.status = ShortlistStatus.CONTACTED

    return {"sent": len([l for l in logs if l.status.value == "SENT"]), "total": len(logs)}


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
