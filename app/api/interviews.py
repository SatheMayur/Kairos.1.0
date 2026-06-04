"""Interviews API — schedule, confirm, and track interview slots."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
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


class InterviewOutcome(BaseModel):
    outcome: str  # HIRED | NEXT_ROUND | REJECTED | NO_SHOW
    notes: Optional[str] = None


@router.post("/{interview_id}/outcome")
async def log_interview_outcome(
    interview_id: int,
    payload: InterviewOutcome,
    db: AsyncSession = Depends(get_db),
):
    """Log interview result and trigger automatic follow-up actions.

    HIRED     → WhatsApp congratulations + queue offer email
    NEXT_ROUND → Auto-propose next round slots via WhatsApp
    REJECTED  → Draft rejection email (not auto-sent — Kirti reviews first)
    NO_SHOW   → WhatsApp "missed you" + reschedule offer
    """
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.candidate import Candidate
    from app.models.job import Job
    from app.services.whatsapp_openclaw import send_whatsapp
    from app.services.scheduling import generate_slots, propose_interview_slots
    from app.models.outreach import OutreachChannel
    import asyncio

    interview = await db.get(Interview, interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    interview.status = InterviewStatus.COMPLETED
    if payload.notes:
        interview.notes = payload.notes

    sl_res = await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.candidate_id == interview.candidate_id,
            ShortlistEntry.job_id == interview.job_id,
        )
    )
    entry = sl_res.scalar_one_or_none()

    status_map = {
        "HIRED": ShortlistStatus.HIRED,
        "NEXT_ROUND": ShortlistStatus.SHORTLISTED,
        "REJECTED": ShortlistStatus.REJECTED,
        "NO_SHOW": ShortlistStatus.DROPPED,
    }
    if entry and payload.outcome in status_map:
        entry.status = status_map[payload.outcome]

    # Fetch candidate + job for auto-triggers
    candidate = await db.get(Candidate, interview.candidate_id)
    job = await db.get(Job, interview.job_id)

    action_taken = "status_updated"

    if candidate and job:
        first_name = candidate.name.split()[0]
        phone = candidate.whatsapp or candidate.phone or ""
        company = job.company or "K. Girdharlal International"

        if payload.outcome == "HIRED" and phone:
            await send_whatsapp(
                phone,
                f"🎉 Congratulations {first_name}!\n\n"
                f"We are delighted to offer you the position of *{job.title}* at {company}.\n\n"
                f"Our HR team will call you within 24 hours to discuss the offer details and next steps.\n\n"
                f"Welcome to the team! 🙏",
                db=db,
            )
            # Queue a formal offer email
            try:
                from app.services.outreach import queue_email_direct
                await queue_email_direct(
                    to=candidate.email or "",
                    subject=f"Offer Letter — {job.title} at {company}",
                    body=(
                        f"Dear {candidate.name},\n\n"
                        f"We are pleased to offer you the position of {job.title} at {company}, Surat.\n\n"
                        f"Kirti Chand will contact you shortly to discuss compensation, joining date, and next steps.\n\n"
                        f"Warm regards,\n"
                        f"Kirti Chand\nHR Manager | {company}\nPh: 9033410606"
                    ),
                    candidate_name=candidate.name,
                    role=job.title,
                    priority="HIGH",
                ) if candidate.email else None
            except Exception as exc:
                logger.warning("Offer email failed for %s: %s", candidate.name, exc)
            action_taken = "hired_whatsapp_sent"

        elif payload.outcome == "NEXT_ROUND" and phone:
            # Determine next round
            round_order = [InterviewRound.SCREENING, InterviewRound.TECHNICAL, InterviewRound.HR, InterviewRound.FINAL]
            try:
                current_idx = round_order.index(interview.round)
                next_round = round_order[min(current_idx + 1, len(round_order) - 1)]
            except (ValueError, IndexError):
                next_round = InterviewRound.HR

            try:
                slots = generate_slots(days_ahead=3, num_slots=3)
                slot_lines = "\n".join(
                    f"  {i+1}. {s.strftime('%A %d %b, %I:%M %p IST')}"
                    for i, s in enumerate(slots)
                )
                await send_whatsapp(
                    phone,
                    f"Hi {first_name}, great news! 🎉\n\n"
                    f"You've progressed to the *{next_round.value.replace('_', ' ').title()} Round* "
                    f"for *{job.title}* at {company}.\n\n"
                    f"Here are 3 available slots:\n{slot_lines}\n\n"
                    f"Reply with *1*, *2*, or *3* to confirm your slot.",
                    db=db,
                )
                await propose_interview_slots(
                    candidate=candidate,
                    job=job,
                    round=next_round,
                    channel=OutreachChannel.WHATSAPP,
                    db=db,
                    slots=slots,
                )
                action_taken = "next_round_slots_sent"
            except Exception as exc:
                logger.warning("Next round scheduling failed for %s: %s", candidate.name, exc)
                action_taken = "next_round_status_updated"

        elif payload.outcome == "REJECTED":
            # Draft rejection — do NOT auto-send (Kirti reviews first)
            try:
                from app.services.outreach import queue_email_direct
                if candidate.email:
                    await queue_email_direct(
                        to=candidate.email,
                        subject=f"Re: {job.title} Application",
                        body=(
                            f"Dear {candidate.name},\n\n"
                            f"Thank you for your time and interest in the {job.title} position at {company}.\n\n"
                            f"After careful consideration, we have decided to move forward with other candidates "
                            f"whose qualifications more closely match our current requirements.\n\n"
                            f"We appreciate your interest and wish you the very best in your career journey.\n\n"
                            f"Warm regards,\nKirti Chand\nHR Manager | {company}"
                        ),
                        candidate_name=candidate.name,
                        role=job.title,
                        priority="LOW",
                    )
                    action_taken = "rejection_email_drafted"
            except Exception as exc:
                logger.warning("Rejection draft failed for %s: %s", candidate.name, exc)
                action_taken = "rejected_no_email"

        elif payload.outcome == "NO_SHOW" and phone:
            await send_whatsapp(
                phone,
                f"Hi {first_name}, we missed you for your interview today for the "
                f"*{job.title}* role at {company}. 😊\n\n"
                f"We understand things come up — would you like to reschedule?\n\n"
                f"Reply *YES* and we'll send you fresh slots right away.",
                db=db,
            )
            action_taken = "no_show_whatsapp_sent"

    await db.commit()
    return {
        "ok": True,
        "interview_id": interview_id,
        "outcome": payload.outcome,
        "action": action_taken,
    }


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
