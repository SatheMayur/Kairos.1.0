"""Follow-up sequence — auto-chase CONTACTED candidates who haven't replied.

Timeline per candidate (measured from initial outreach sent_at):
  Day 0 : initial contact sent         (cron/outreach)
  Day 3 : follow-up #1 if no reply     (cron/followup)
  Day 6 : follow-up #2 if still silent (cron/followup)
  Day 9+: mark DROPPED                  (cron/followup)

REJECT_DRAFT_ONLY rule is not affected — this module never sends rejections.
"""
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate
from app.models.job import Job
from app.models.outreach import OutreachChannel, OutreachLog, OutreachStatus, OutreachType
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.outreach import send_outreach
from app.utils.logging import get_logger

logger = get_logger(__name__)

FOLLOWUP_1_DAYS = 3
FOLLOWUP_2_DAYS = 6
DROP_DAYS = 9


def _render_followup_wa(candidate: Candidate, job: Job, attempt: int) -> str:
    first = candidate.name.split()[0]
    company = job.company or "K. Girdharlal International"
    if attempt == 1:
        return (
            f"Hi {first}, I messaged a few days ago about the *{job.title}* role at "
            f"{company}. Still interested? Reply *YES* and I'll share full details + "
            f"schedule a quick call. 🙏"
        )
    return (
        f"Hi {first}, final follow-up on the *{job.title}* opportunity at {company}. "
        f"Reply *YES* to proceed or *NO* to pass — no hard feelings either way! 🌟"
    )


def _render_followup(candidate: Candidate, job: Job, attempt: int) -> tuple[str, str]:
    subject = f"Following up — {job.title} at {job.company or 'K. Girdharlal International'}"
    if attempt == 1:
        body = f"""Hi {candidate.name},

I reached out a few days ago about the {job.title} role at \
{job.company or 'K. Girdharlal International'} and wanted to follow up in case \
my earlier message got buried.

This is a great opportunity for someone with your background. If you are \
interested, simply reply YES and I will send you the full JD and schedule a \
quick call.

If this role is not the right fit right now, no worries — I will keep you in \
mind for future openings.

Best regards,
HR Team | {job.company or 'K. Girdharlal International'}"""
    else:
        body = f"""Hi {candidate.name},

This is my final follow-up regarding the {job.title} role at \
{job.company or 'K. Girdharlal International'}.

If you are open to exploring this opportunity, please reply YES and I will \
share the full details. Otherwise, I completely understand and wish you all the \
best in your career.

Warm regards,
HR Team | {job.company or 'K. Girdharlal International'}"""
    return subject, body


async def send_followups(db: AsyncSession) -> dict:
    """Send day-3 / day-6 follow-ups for silent CONTACTED candidates.

    Returns a summary: {followup1_sent, followup2_sent, dropped}.
    """
    now = datetime.utcnow()
    followup1_sent = followup2_sent = dropped = 0

    result = await db.execute(
        select(ShortlistEntry).where(ShortlistEntry.status == ShortlistStatus.CONTACTED)
    )
    entries = result.scalars().all()

    for entry in entries:
        # Find initial outreach timestamp
        log_res = await db.execute(
            select(OutreachLog)
            .where(
                and_(
                    OutreachLog.candidate_id == entry.candidate_id,
                    OutreachLog.job_id == entry.job_id,
                    OutreachLog.outreach_type == OutreachType.INITIAL_CONTACT,
                    OutreachLog.status == OutreachStatus.SENT,
                )
            )
            .order_by(OutreachLog.sent_at.asc())
        )
        initial = log_res.scalars().first()
        if not initial or not initial.sent_at:
            continue

        days_elapsed = (now - initial.sent_at).days

        # Skip if candidate already replied
        replied = await db.execute(
            select(OutreachLog).where(
                and_(
                    OutreachLog.candidate_id == entry.candidate_id,
                    OutreachLog.job_id == entry.job_id,
                    OutreachLog.status == OutreachStatus.REPLIED,
                )
            )
        )
        if replied.scalars().first():
            continue

        # Count follow-ups already sent
        fu_res = await db.execute(
            select(OutreachLog).where(
                and_(
                    OutreachLog.candidate_id == entry.candidate_id,
                    OutreachLog.job_id == entry.job_id,
                    OutreachLog.outreach_type == OutreachType.FOLLOW_UP,
                    OutreachLog.status == OutreachStatus.SENT,
                )
            )
        )
        followups_sent = len(fu_res.scalars().all())

        # Drop after day 9 if two follow-ups sent with no response
        if days_elapsed >= DROP_DAYS and followups_sent >= 2:
            entry.status = ShortlistStatus.DROPPED
            dropped += 1
            logger.info(
                "Dropped candidate %d job %d — no reply in %d days",
                entry.candidate_id, entry.job_id, days_elapsed,
            )
            continue

        candidate_res = await db.execute(
            select(Candidate).where(Candidate.id == entry.candidate_id)
        )
        candidate = candidate_res.scalar_one_or_none()
        job_res = await db.execute(select(Job).where(Job.id == entry.job_id))
        job = job_res.scalar_one_or_none()
        if not candidate or not job:
            continue

        # Use WhatsApp when candidate has a phone (consistent with initial outreach)
        has_phone = bool(candidate.whatsapp or candidate.phone)
        wa_channel = OutreachChannel.WHATSAPP if has_phone else OutreachChannel.EMAIL

        if days_elapsed >= FOLLOWUP_1_DAYS and followups_sent == 0:
            subject, email_body = _render_followup(candidate, job, attempt=1)
            # Email follow-up
            await send_outreach(
                candidate=candidate, job=job,
                channel=OutreachChannel.EMAIL,
                outreach_type=OutreachType.FOLLOW_UP,
                db=db, subject=subject, body=email_body,
            )
            # WhatsApp follow-up (if phone available)
            if has_phone:
                await send_outreach(
                    candidate=candidate, job=job,
                    channel=OutreachChannel.WHATSAPP,
                    outreach_type=OutreachType.FOLLOW_UP,
                    db=db, body=_render_followup_wa(candidate, job, attempt=1),
                )
            followup1_sent += 1
            logger.info(
                "Follow-up #1 sent: candidate=%d job=%d (day %d) wa=%s",
                candidate.id, job.id, days_elapsed, has_phone,
            )

        elif days_elapsed >= FOLLOWUP_2_DAYS and followups_sent == 1:
            subject, email_body = _render_followup(candidate, job, attempt=2)
            await send_outreach(
                candidate=candidate, job=job,
                channel=OutreachChannel.EMAIL,
                outreach_type=OutreachType.FOLLOW_UP,
                db=db, subject=subject, body=email_body,
            )
            if has_phone:
                await send_outreach(
                    candidate=candidate, job=job,
                    channel=OutreachChannel.WHATSAPP,
                    outreach_type=OutreachType.FOLLOW_UP,
                    db=db, body=_render_followup_wa(candidate, job, attempt=2),
                )
            followup2_sent += 1
            logger.info(
                "Follow-up #2 sent: candidate=%d job=%d (day %d) wa=%s",
                candidate.id, job.id, days_elapsed, has_phone,
            )

    return {
        "followup1_sent": followup1_sent,
        "followup2_sent": followup2_sent,
        "dropped": dropped,
    }
