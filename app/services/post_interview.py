"""Post-interview automation — close the loop after scheduled interviews.

Two jobs:
  1. CONFIRMED interviews past grace period → mark COMPLETED, email recruiter to log outcome
  2. PROPOSED slots with no confirmation after 48 h → send a slot-nudge to candidate
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.candidate import Candidate
from app.models.interview import Interview, InterviewStatus
from app.models.job import Job
from app.models.outreach import OutreachChannel, OutreachLog, OutreachType
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.outreach import queue_email_direct, send_outreach
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

OUTCOME_GRACE_HOURS = 2   # mark COMPLETED this many hours after interview ends
NO_CONFIRM_HOURS = 48     # nudge after this many hours without slot confirmation

RECRUITER_EMAIL = "kirti@kgirdharlal.com"


async def process_completed_interviews(db: AsyncSession) -> dict:
    """Auto-advance overdue interviews and nudge candidates with unconfirmed slots.

    Returns {auto_completed, slot_nudges_sent}.
    """
    now = datetime.utcnow()
    auto_completed = slot_nudges_sent = 0

    # ── 1. Mark overdue CONFIRMED interviews as COMPLETED ──────────────────────
    res = await db.execute(
        select(Interview).where(Interview.status == InterviewStatus.CONFIRMED)
    )
    for interview in res.scalars().all():
        if not interview.scheduled_at:
            continue
        end_time = interview.scheduled_at + timedelta(minutes=interview.duration_minutes)
        if now < end_time + timedelta(hours=OUTCOME_GRACE_HOURS):
            continue

        interview.status = InterviewStatus.COMPLETED
        auto_completed += 1

        # Roll shortlist entry back to PENDING so recruiter sees it
        sl_res = await db.execute(
            select(ShortlistEntry).where(
                and_(
                    ShortlistEntry.candidate_id == interview.candidate_id,
                    ShortlistEntry.job_id == interview.job_id,
                )
            )
        )
        entry = sl_res.scalar_one_or_none()
        if entry and entry.status == ShortlistStatus.INTERVIEW_SCHEDULED:
            entry.status = ShortlistStatus.PENDING

        # Fetch names for the notification
        c_res = await db.execute(
            select(Candidate).where(Candidate.id == interview.candidate_id)
        )
        candidate = c_res.scalar_one_or_none()
        j_res = await db.execute(select(Job).where(Job.id == interview.job_id))
        job = j_res.scalar_one_or_none()

        if candidate and job:
            dashboard = f"{settings.interview_confirmation_base_url}/ui/interviews"
            scheduled_str = interview.scheduled_at.strftime("%d %b %Y, %I:%M %p IST")
            await queue_email_direct(
                to=RECRUITER_EMAIL,
                subject=f"[Log outcome] {candidate.name} — {job.title} interview done",
                body=f"""Hi Kirti,

The {interview.round.value} interview for {candidate.name} ({job.title}) was \
scheduled at {scheduled_str} and should now be complete.

Please log the outcome in the dashboard:
{dashboard}

Candidate: {candidate.name}
Email    : {candidate.email or '—'}
Role     : {job.title}
Round    : {interview.round.value}
Notes    : {interview.notes or 'None recorded'}

Options: Shortlist for next round / Hire / Reject (draft only — never auto-sent)

— AI HR System (automated)
""",
                candidate_name=candidate.name,
                role=job.title,
                priority="HIGH",
            )
            logger.info(
                "Interview %d COMPLETED — recruiter notified (candidate=%d %s)",
                interview.id, candidate.id, candidate.name,
            )

    # ── 2. Nudge candidates who haven't confirmed their slot after 48 h ────────
    res2 = await db.execute(
        select(Interview).where(Interview.status == InterviewStatus.PROPOSED)
    )
    for interview in res2.scalars().all():
        age_hours = (now - interview.created_at).total_seconds() / 3600
        if age_hours < NO_CONFIRM_HOURS:
            continue

        # Skip if we already sent a nudge for this interview
        nudge_res = await db.execute(
            select(OutreachLog).where(
                and_(
                    OutreachLog.candidate_id == interview.candidate_id,
                    OutreachLog.job_id == interview.job_id,
                    OutreachLog.outreach_type == OutreachType.REMINDER,
                )
            )
        )
        if nudge_res.scalars().first():
            continue

        c_res = await db.execute(
            select(Candidate).where(Candidate.id == interview.candidate_id)
        )
        candidate = c_res.scalar_one_or_none()
        j_res = await db.execute(select(Job).where(Job.id == interview.job_id))
        job = j_res.scalar_one_or_none()
        if not candidate or not job:
            continue

        try:
            slots = json.loads(interview.proposed_slots or "[]")
        except Exception:
            slots = []
        slot_lines = "\n".join(f"  • {s}" for s in slots) or "  (see previous email)"
        confirm_url = (
            f"{settings.interview_confirmation_base_url}"
            f"/api/v1/interviews/confirm/{interview.confirmation_token}"
        )

        await send_outreach(
            candidate=candidate, job=job,
            channel=OutreachChannel.EMAIL,
            outreach_type=OutreachType.REMINDER,
            db=db,
            subject=f"Quick reminder — please confirm your interview slot | {job.title}",
            body=f"""Hi {candidate.name},

I sent you some interview slot options 48 hours ago for the {job.title} role \
and wanted to make sure you received them.

Available slots:
{slot_lines}

Confirm your slot here:
{confirm_url}

If none of these times work, simply reply and we will find a better slot.

Best regards,
HR Team | {job.company or 'K. Girdharlal International'}""",
        )
        slot_nudges_sent += 1
        logger.info(
            "Slot nudge sent — interview %d candidate=%d %s",
            interview.id, candidate.id, candidate.name,
        )
        # Also nudge via WhatsApp
        wa_phone = candidate.whatsapp or candidate.phone
        if wa_phone and slots:
            try:
                from app.models.wa_queue import WAQueue
                slot_lines_wa = "\n".join(f"{i+1}. {s}" for i, s in enumerate(slots[:3]))
                wa_nudge = (
                    f"Hi {candidate.name.split()[0]}, I sent you *{job.title}* interview "
                    f"slots 2 days ago — please confirm!\n\n"
                    f"{slot_lines_wa}\n\nReply *1*, *2*, or *3* to book. 🙏"
                )
                db.add(WAQueue(phone=wa_phone, message=wa_nudge))
            except Exception:
                pass

    return {"auto_completed": auto_completed, "slot_nudges_sent": slot_nudges_sent}
