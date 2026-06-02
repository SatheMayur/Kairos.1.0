"""THE one scheduling service — proposes slots, confirms, books, reminds.

Workflow:
  1. propose_interview_slots()  → creates Interview(PROPOSED) + sends slot email
  2. confirm_interview_slot()   → called from confirmation URL, sets CONFIRMED
  3. send_interview_reminders() → called by background job 24h before interview
"""
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.interview import Interview, InterviewStatus, InterviewRound
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.outreach import OutreachChannel, OutreachType
from app.services.outreach import send_outreach
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Default slot generation: business hours Mon–Fri
_SLOT_HOUR_START = 10
_SLOT_HOUR_END = 17
_SLOT_DURATION_MINUTES = 30


def generate_slots(
    days_ahead: int = 3,
    num_slots: int = 3,
    duration_minutes: int = _SLOT_DURATION_MINUTES,
) -> list[datetime]:
    """Generate `num_slots` available interview slots starting from tomorrow."""
    slots: list[datetime] = []
    cursor = datetime.utcnow().replace(hour=_SLOT_HOUR_START, minute=0, second=0, microsecond=0)
    cursor += timedelta(days=1)  # start tomorrow
    while len(slots) < num_slots:
        if cursor.weekday() < 5 and _SLOT_HOUR_START <= cursor.hour < _SLOT_HOUR_END:
            slots.append(cursor)
            cursor += timedelta(minutes=duration_minutes * 2)
        else:
            cursor += timedelta(hours=1)
            if cursor.hour >= _SLOT_HOUR_END:
                cursor = cursor.replace(hour=_SLOT_HOUR_START, minute=0) + timedelta(days=1)
    return slots[:num_slots]


async def propose_interview_slots(
    *,
    candidate: Candidate,
    job: Job,
    round: InterviewRound = InterviewRound.SCREENING,
    interviewer_name: Optional[str] = None,
    interviewer_email: Optional[str] = None,
    channel: OutreachChannel = OutreachChannel.EMAIL,
    db: AsyncSession,
    slots: Optional[list[datetime]] = None,
) -> Interview:
    """Create an Interview record and send slot-proposal message to candidate."""
    if slots is None:
        slots = generate_slots()

    token = secrets.token_urlsafe(32)
    slot_strs = [s.strftime("%A, %d %B %Y %I:%M %p IST") for s in slots]

    interview = Interview(
        candidate_id=candidate.id,
        job_id=job.id,
        round=round,
        status=InterviewStatus.PROPOSED,
        duration_minutes=_SLOT_DURATION_MINUTES,
        interviewer_name=interviewer_name,
        interviewer_email=interviewer_email,
        confirmation_token=token,
        proposed_slots=json.dumps(slot_strs),
    )
    db.add(interview)
    await db.flush()

    await send_outreach(
        candidate=candidate,
        job=job,
        channel=channel,
        outreach_type=OutreachType.SLOT_PROPOSAL,
        db=db,
        slots=slot_strs,
        confirmation_token=token,
    )

    logger.info(
        "Interview proposed: interview_id=%d candidate=%d job=%d",
        interview.id, candidate.id, job.id,
    )
    return interview


async def confirm_interview_slot(
    *,
    token: str,
    selected_slot_index: int,
    db: AsyncSession,
) -> Optional[Interview]:
    """Mark interview as confirmed based on candidate's slot choice.

    Called from the public confirmation endpoint.
    """
    result = await db.execute(
        select(Interview).where(Interview.confirmation_token == token)
    )
    interview = result.scalar_one_or_none()
    if not interview:
        logger.warning("Invalid confirmation token: %s", token)
        return None

    if interview.status not in (InterviewStatus.PROPOSED, InterviewStatus.RESCHEDULED):
        logger.info("Interview %d already %s", interview.id, interview.status)
        return interview

    slots = json.loads(interview.proposed_slots or "[]")
    if selected_slot_index >= len(slots):
        logger.warning("Invalid slot index %d for interview %d", selected_slot_index, interview.id)
        return None

    chosen = slots[selected_slot_index]
    # Parse back to datetime (best-effort)
    try:
        scheduled_at = datetime.strptime(chosen, "%A, %d %B %Y %I:%M %p IST")
    except ValueError:
        scheduled_at = datetime.utcnow() + timedelta(days=2)

    interview.status = InterviewStatus.CONFIRMED
    interview.scheduled_at = scheduled_at
    interview.meet_link = f"https://meet.google.com/{secrets.token_urlsafe(8)}"

    logger.info("Interview %d confirmed for %s", interview.id, chosen)
    return interview


async def send_interview_reminders(db: AsyncSession) -> int:
    """Send reminders for confirmed interviews scheduled within the next 24 hours.

    Returns count of reminders sent.  Called by background scheduler.
    """
    now = datetime.utcnow()
    window_end = now + timedelta(hours=24)

    result = await db.execute(
        select(Interview).where(
            Interview.status == InterviewStatus.CONFIRMED,
            Interview.reminder_sent.is_(False),
            Interview.scheduled_at >= now,
            Interview.scheduled_at <= window_end,
        )
    )
    interviews = result.scalars().all()
    sent = 0

    for interview in interviews:
        candidate_result = await db.execute(
            select(Candidate).where(Candidate.id == interview.candidate_id)
        )
        candidate = candidate_result.scalar_one_or_none()
        job_result = await db.execute(select(Job).where(Job.id == interview.job_id))
        job = job_result.scalar_one_or_none()

        if not candidate or not job:
            continue

        await send_outreach(
            candidate=candidate,
            job=job,
            channel=OutreachChannel.EMAIL,
            outreach_type=OutreachType.REMINDER,
            db=db,
            scheduled_at=interview.scheduled_at,
            meet_link=interview.meet_link,
        )
        # Also queue a WhatsApp reminder when candidate has phone
        wa_phone = candidate.whatsapp or candidate.phone
        if wa_phone:
            try:
                from app.models.wa_queue import WAQueue
                slot_str = interview.scheduled_at.strftime("%A %d %b at %I:%M %p IST")
                wa_msg = (
                    f"Hi {candidate.name.split()[0]}, reminder: your *{job.title}* interview "
                    f"is tomorrow — *{slot_str}*.\n"
                    f"{'🔗 Meet: ' + interview.meet_link if interview.meet_link else '🔗 Link coming shortly.'}\n"
                    f"Please join 2 min early. All the best! 👍"
                )
                db.add(WAQueue(phone=wa_phone, message=wa_msg))
            except Exception:
                pass
        interview.reminder_sent = True
        sent += 1

    logger.info("Sent %d interview reminders", sent)
    return sent
