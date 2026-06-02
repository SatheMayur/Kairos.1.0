"""Interviews API — schedule, confirm, and track interview slots."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.interview import Interview, InterviewStatus, InterviewRound
from app.models.candidate import Candidate
from app.models.job import Job
from app.schemas.interview import InterviewCreate, InterviewRead, InterviewUpdate
from app.services.scheduling import (
    propose_interview_slots,
    confirm_interview_slot,
    send_interview_reminders,
)
from app.models.outreach import OutreachChannel
from app.utils.logging import get_logger

router = APIRouter(prefix="/interviews", tags=["interviews"])
logger = get_logger(__name__)


class InterviewDirect(InterviewCreate):
    status: InterviewStatus = InterviewStatus.CONFIRMED
    scheduled_at: Optional[str] = None
    notes: Optional[str] = None


@router.post("", response_model=InterviewRead, status_code=status.HTTP_201_CREATED)
async def create_interview_direct(payload: InterviewDirect, db: AsyncSession = Depends(get_db)):
    """Admin endpoint — create a pre-confirmed or completed interview directly."""
    from datetime import datetime
    iv = Interview(
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        round=payload.round,
        status=payload.status,
        duration_minutes=payload.duration_minutes or 30,
        interviewer_name=payload.interviewer_name,
        interviewer_email=payload.interviewer_email,
        notes=payload.notes,
    )
    if payload.scheduled_at:
        iv.scheduled_at = datetime.fromisoformat(payload.scheduled_at)
    db.add(iv)
    await db.flush()
    return iv


@router.post("/propose", response_model=InterviewRead, status_code=status.HTTP_201_CREATED)
async def propose_slots(payload: InterviewCreate, db: AsyncSession = Depends(get_db)):
    """Generate slot options and send proposal to candidate."""
    candidate = await _get_candidate(payload.candidate_id, db)
    job = await _get_job(payload.job_id, db)
    interview = await propose_interview_slots(
        candidate=candidate,
        job=job,
        round=payload.round,
        interviewer_name=payload.interviewer_name,
        interviewer_email=payload.interviewer_email,
        channel=OutreachChannel.EMAIL,
        db=db,
    )
    return interview


@router.get("/confirm/{token}", summary="Candidate confirmation link")
async def confirm_slot(token: str, slot: int = 0, db: AsyncSession = Depends(get_db)):
    """Public endpoint — candidate clicks this link to confirm their slot.

    ?slot=0 picks the first proposed slot (default).
    """
    interview = await confirm_interview_slot(token=token, selected_slot_index=slot, db=db)
    if not interview:
        raise HTTPException(status_code=404, detail="Invalid or expired confirmation link")
    return {
        "message": "Interview confirmed!",
        "scheduled_at": interview.scheduled_at,
        "meet_link": interview.meet_link,
    }


@router.post("/reminders", summary="Trigger reminder dispatch (normally run by scheduler)")
async def trigger_reminders(db: AsyncSession = Depends(get_db)):
    count = await send_interview_reminders(db)
    return {"reminders_sent": count}


@router.get("", response_model=list[InterviewRead])
async def list_interviews(
    job_id: Optional[int] = None,
    candidate_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Interview)
        .offset(skip)
        .limit(limit)
        .order_by(Interview.created_at.desc())
    )
    if job_id:
        query = query.where(Interview.job_id == job_id)
    if candidate_id:
        query = query.where(Interview.candidate_id == candidate_id)
    if status:
        query = query.where(Interview.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{interview_id}", response_model=InterviewRead)
async def get_interview(interview_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(interview_id, db)


@router.patch("/{interview_id}", response_model=InterviewRead)
async def update_interview(
    interview_id: int, payload: InterviewUpdate, db: AsyncSession = Depends(get_db)
):
    interview = await _get_or_404(interview_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(interview, field, value)
    return interview


async def _get_or_404(interview_id: int, db: AsyncSession) -> Interview:
    result = await db.execute(select(Interview).where(Interview.id == interview_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail=f"Interview {interview_id} not found")
    return obj


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
