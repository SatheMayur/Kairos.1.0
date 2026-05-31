"""THE one outreach service — Email, WhatsApp, SMS, Call (placeholder).

All channels route through send_outreach().  Channel implementations are
self-contained; adding a new provider means adding a new _send_* function.
"""
import asyncio
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.outreach import OutreachLog, OutreachChannel, OutreachStatus, OutreachType
from app.models.candidate import Candidate
from app.models.job import Job
from app.config import get_settings
from app.utils.logging import get_logger
from app.utils.retry import with_retry

logger = get_logger(__name__)
settings = get_settings()

# ── Message templates ──────────────────────────────────────────────────────────

def _render_initial_contact(candidate: Candidate, job: Job) -> tuple[str, str]:
    subject = f"Exciting Opportunity: {job.title} at {job.company or 'our company'}"
    body = f"""Hi {candidate.name},

I hope this message finds you well.

We came across your profile and believe you could be a great fit for the role of
{job.title} at {job.company or 'our company'}.

Role details:
• Position   : {job.title}
• Location   : {job.location or 'TBD'}
• Experience : {job.experience_min or ''}–{job.experience_max or ''} years
• Salary     : ₹{int(job.salary_min or 0):,} – ₹{int(job.salary_max or 0):,}

If you are interested or would like more details, simply reply YES to this message
and we will share the full JD and schedule a quick call.

Warm regards,
HR Team | {job.company or 'K. Girdharlal International'}
"""
    return subject, body


def _render_slot_proposal(candidate: Candidate, job: Job, slots: list[str], token: str) -> tuple[str, str]:
    slot_lines = "\n".join(f"  • {s}" for s in slots)
    confirm_url = f"{settings.interview_confirmation_base_url}/confirm/{token}"
    subject = f"Interview Slots — {job.title}"
    body = f"""Hi {candidate.name},

Great news! We'd love to schedule an interview for the {job.title} role.

Please pick a slot that works for you:
{slot_lines}

Confirm your preferred slot here:
{confirm_url}

The interview will be approximately {30} minutes via Google Meet.

Looking forward to speaking with you!

HR Team | {job.company or 'K. Girdharlal International'}
"""
    return subject, body


def _render_reminder(candidate: Candidate, job: Job, scheduled_at: datetime, meet_link: Optional[str]) -> tuple[str, str]:
    subject = f"Interview Reminder — {job.title} tomorrow"
    body = f"""Hi {candidate.name},

This is a friendly reminder about your interview scheduled for:

  📅 {scheduled_at.strftime('%A, %d %B %Y at %I:%M %p IST')}
  🔗 {meet_link or 'Link will be shared shortly'}

Role: {job.title} at {job.company or 'our company'}

Please join 2 minutes early. If you need to reschedule, reply to this message.

Best,
HR Team | {job.company or 'K. Girdharlal International'}
"""
    return subject, body


# ── Channel implementations ────────────────────────────────────────────────────

async def _send_email(to: str, subject: str, body: str, candidate_name: str = "", role: str = "") -> str:
    """Send email: Google Sheets Email Queue (primary) → SMTP (fallback)."""
    # Primary: write to Sheets Email Queue (Apps Script sends within 5 min)
    if settings.use_sheets_email_queue:
        from app.services.email_queue_sheets import queue_email
        queued = await queue_email(
            to=to, subject=subject, body=body,
            candidate_name=candidate_name, role=role,
        )
        if queued:
            return f"queue-{hash(to + subject)}"

    # If no SMTP password configured, mock immediately (avoids hanging TCP connection)
    if not settings.smtp_password:
        logger.warning("MOCK email (dev) to %s | %s", to, subject[:60])
        return f"mock-email-{hash(to)}"

    # Fallback: direct SMTP
    try:
        import aiosmtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
            timeout=10,
        )
        logger.info("Email sent (SMTP) to %s subject=%r", to, subject)
        return f"email-{hash(to + subject)}"
    except Exception as exc:
        if settings.app_env == "development":
            logger.warning("MOCK email to %s | %s | %s", to, subject, str(exc)[:80])
            return f"mock-email-{hash(to)}"
        raise


@with_retry(max_attempts=2, exceptions=(Exception,))
async def _send_whatsapp(to: str, body: str) -> str:
    """Send via Twilio WhatsApp API."""
    if settings.app_env == "development" or not settings.twilio_account_sid:
        logger.warning("MOCK WhatsApp to %s: %s", to, body[:60])
        return f"mock-wa-{hash(to)}"
    import httpx
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            data={
                "From": settings.twilio_whatsapp_from,
                "To": f"whatsapp:{to}",
                "Body": body,
            },
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("sid", "unknown")


@with_retry(max_attempts=2, exceptions=(Exception,))
async def _send_sms(to: str, body: str) -> str:
    """Send via Twilio SMS."""
    if settings.app_env == "development" or not settings.twilio_account_sid:
        logger.warning("MOCK SMS to %s: %s", to, body[:60])
        return f"mock-sms-{hash(to)}"
    import httpx
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            data={"From": settings.twilio_sms_from, "To": to, "Body": body},
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("sid", "unknown")


async def _send_call(to: str, body: str) -> str:
    """Call workflow placeholder — logs intent, returns placeholder ID."""
    logger.info("CALL QUEUED for %s — script: %s", to, body[:80])
    return f"call-placeholder-{hash(to)}"


async def _send_platform_message(profile_url: str, body: str) -> str:
    """Log a message intended for a closed platform (CAD Crowd, LinkedIn, etc).

    No API access to these platforms, so the message is stored for the
    recruiter to send manually through the platform's interface.
    """
    logger.info("PLATFORM_MESSAGE logged for %s — send manually: %s", profile_url, body[:80])
    return f"platform-{hash(profile_url)}"


# ── Public API ─────────────────────────────────────────────────────────────────

async def send_outreach(
    *,
    candidate: Candidate,
    job: Job,
    channel: OutreachChannel,
    outreach_type: OutreachType,
    db: AsyncSession,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    slots: Optional[list[str]] = None,
    confirmation_token: Optional[str] = None,
    scheduled_at: Optional[datetime] = None,
    meet_link: Optional[str] = None,
) -> OutreachLog:
    """Send a single outreach message on the given channel and log it.

    Callers pass body/subject directly or let the service render them
    from outreach_type.
    """
    # Auto-render message if not provided
    if body is None:
        if outreach_type == OutreachType.INITIAL_CONTACT:
            subject, body = _render_initial_contact(candidate, job)
        elif outreach_type == OutreachType.SLOT_PROPOSAL and slots and confirmation_token:
            subject, body = _render_slot_proposal(candidate, job, slots, confirmation_token)
        elif outreach_type == OutreachType.REMINDER and scheduled_at:
            subject, body = _render_reminder(candidate, job, scheduled_at, meet_link)
        else:
            body = body or "Please reply to confirm your interest."
            subject = subject or f"Regarding {job.title}"

    log = OutreachLog(
        candidate_id=candidate.id,
        job_id=job.id,
        channel=channel,
        outreach_type=outreach_type,
        subject=subject,
        message=body,
        status=OutreachStatus.PENDING,
    )
    db.add(log)
    await db.flush()

    try:
        effective_channel, recipient = _resolve_channel(candidate, channel)

        # Update log with the actual channel used (may differ from requested)
        log.channel = effective_channel

        if effective_channel == OutreachChannel.UNREACHABLE:
            log.status = OutreachStatus.FAILED
            log.error_detail = "No reachable contact — no email, phone, or platform profile"
            logger.warning(
                "Outreach UNREACHABLE: candidate=%d (%s) job=%d",
                candidate.id, candidate.name, log.job_id,
            )
            return log

        if effective_channel == OutreachChannel.PLATFORM_MESSAGE:
            msg_id = await _send_platform_message(recipient, body)
        elif effective_channel == OutreachChannel.EMAIL:
            msg_id = await _send_email(
                recipient, subject or "", body,
                candidate_name=candidate.name,
                role=candidate.current_role or "",
            )
        elif effective_channel == OutreachChannel.WHATSAPP:
            msg_id = await _send_whatsapp(recipient, body)
        elif effective_channel == OutreachChannel.SMS:
            msg_id = await _send_sms(recipient, body)
        else:
            msg_id = await _send_call(recipient, body)

        log.status = OutreachStatus.SENT
        log.sent_at = datetime.utcnow()
        log.provider_message_id = msg_id
        logger.info(
            "Outreach sent: log_id=%d channel=%s candidate=%d",
            log.id, effective_channel, candidate.id,
        )

    except Exception as exc:
        log.status = OutreachStatus.FAILED
        log.error_detail = str(exc)[:500]
        logger.error("Outreach failed: log_id=%d error=%s", log.id, exc)

    return log


def _resolve_channel(
    candidate: Candidate, requested: OutreachChannel
) -> tuple[OutreachChannel, str]:
    """Return (channel, recipient) choosing the best available contact method.

    Priority: requested channel → email → phone (WhatsApp/SMS) → platform profile → unreachable
    """
    # Try the explicitly requested channel first
    if requested == OutreachChannel.EMAIL and candidate.email:
        return OutreachChannel.EMAIL, candidate.email
    if requested == OutreachChannel.WHATSAPP and (candidate.whatsapp or candidate.phone):
        return OutreachChannel.WHATSAPP, candidate.whatsapp or candidate.phone
    if requested in (OutreachChannel.SMS, OutreachChannel.CALL) and candidate.phone:
        return requested, candidate.phone
    if requested == OutreachChannel.PLATFORM_MESSAGE and candidate.source_ref:
        return OutreachChannel.PLATFORM_MESSAGE, candidate.source_ref

    # Auto-fallback chain
    if candidate.email:
        return OutreachChannel.EMAIL, candidate.email
    if candidate.whatsapp or candidate.phone:
        return OutreachChannel.WHATSAPP, candidate.whatsapp or candidate.phone
    if candidate.source_ref:
        return OutreachChannel.PLATFORM_MESSAGE, candidate.source_ref

    return OutreachChannel.UNREACHABLE, ""


async def send_bulk_outreach(
    *,
    candidates: list[Candidate],
    job: Job,
    channel: OutreachChannel,
    outreach_type: OutreachType,
    db: AsyncSession,
    delay_seconds: float = 2.0,
) -> list[OutreachLog]:
    """Send outreach to many candidates with a configurable delay between sends."""
    logs: list[OutreachLog] = []
    for candidate in candidates:
        log = await send_outreach(
            candidate=candidate,
            job=job,
            channel=channel,
            outreach_type=outreach_type,
            db=db,
        )
        logs.append(log)
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
    return logs
