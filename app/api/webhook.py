"""Inbound WhatsApp webhook — receives messages from OpenClaw / WAHA.

WAHA calls POST /webhook/whatsapp for every incoming message.
We detect candidate intent and auto-advance the pipeline:

  "YES / interested"  → INTERESTED → propose interview slots via WhatsApp
  "NO / not interested" → NOT_INTERESTED (silent — no auto-rejection sent)
  anything else       → acknowledge and ask them to reply YES or NO
"""
import hashlib
import hmac
import json
import re

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy import and_, select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.candidate import Candidate
from app.models.interview import Interview, InterviewStatus
from app.models.job import Job
from app.models.outreach import OutreachChannel, OutreachLog, OutreachStatus, OutreachType
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.scheduling import generate_slots, propose_interview_slots
from app.services.whatsapp_openclaw import is_negative, is_positive, send_whatsapp, _extract_phone
from app.utils.logging import get_logger

router = APIRouter(prefix="/webhook", tags=["webhook"])
logger = get_logger(__name__)
settings = get_settings()


def _verify_signature(body: bytes, sig_header: str) -> bool:
    """Verify HMAC-SHA256 signature from WAHA if secret is configured."""
    if not settings.openclaw_webhook_secret:
        return True  # no secret set — accept all (lock down in production)
    expected = hmac.new(
        settings.openclaw_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", sig_header or "")


def _clean_phone(chat_id: str) -> str:
    """Extract digits from WAHA chatId: '919876543210@c.us' → '9876543210'"""
    digits = _extract_phone(chat_id)
    if digits.startswith("91") and len(digits) == 12:
        return digits[2:]   # strip country code → last 10 digits
    return digits[-10:]     # fallback: last 10


async def _handle_inbound(from_jid: str, body_text: str, session: str):
    """Core logic — runs in background after 200 is returned to WAHA."""
    phone_10 = _clean_phone(from_jid)
    logger.info("Inbound WhatsApp from %s (%s): %r", from_jid, phone_10, body_text[:80])

    async with AsyncSessionLocal() as db:
        # Find candidate by phone (match last 10 digits)
        result = await db.execute(
            select(Candidate).where(
                Candidate.phone.like(f"%{phone_10}")
            )
        )
        candidate = result.scalars().first()
        if not candidate:
            # Unknown sender — politely ignore
            logger.info("No candidate found for phone %s — ignoring", phone_10)
            return

        # Find their active shortlist entry (CONTACTED or INTERESTED)
        sl_res = await db.execute(
            select(ShortlistEntry)
            .where(
                and_(
                    ShortlistEntry.candidate_id == candidate.id,
                    ShortlistEntry.status.in_([
                        ShortlistStatus.CONTACTED,
                        ShortlistStatus.INTERESTED,
                        ShortlistStatus.SHORTLISTED,
                    ]),
                )
            )
            .order_by(ShortlistEntry.created_at.desc())
        )
        entry = sl_res.scalars().first()
        if not entry:
            logger.info(
                "Candidate %d has no active shortlist entry — ignoring reply", candidate.id
            )
            return

        job_res = await db.execute(select(Job).where(Job.id == entry.job_id))
        job = job_res.scalar_one_or_none()
        if not job:
            return

        # Mark outreach log as REPLIED
        log_res = await db.execute(
            select(OutreachLog).where(
                and_(
                    OutreachLog.candidate_id == candidate.id,
                    OutreachLog.job_id == entry.job_id,
                    OutreachLog.status == OutreachStatus.SENT,
                )
            ).order_by(OutreachLog.sent_at.desc())
        )
        last_log = log_res.scalars().first()
        if last_log:
            last_log.status = OutreachStatus.REPLIED
            last_log.reply_text = body_text[:500]

        phone_wa = candidate.whatsapp or candidate.phone or ""

        if is_positive(body_text):
            # ── Candidate is interested ──────────────────────────────────────
            entry.status = ShortlistStatus.INTERESTED
            await db.flush()

            # Check if slots already proposed
            existing_interview = await db.execute(
                select(Interview).where(
                    and_(
                        Interview.candidate_id == candidate.id,
                        Interview.job_id == entry.job_id,
                        Interview.status.in_([InterviewStatus.PROPOSED, InterviewStatus.CONFIRMED]),
                    )
                )
            )
            if existing_interview.scalars().first():
                # Already proposed — just remind
                await send_whatsapp(
                    phone_wa,
                    f"Hi {candidate.name.split()[0]}, great! I already sent you interview "
                    f"slot options — please check and confirm. "
                    f"If you need new slots, just say 'reschedule'.",
                )
            else:
                # Propose interview slots via WhatsApp
                slots = generate_slots(days_ahead=3, num_slots=3)
                slot_lines = "\n".join(
                    f"  {i+1}. {s.strftime('%A %d %b, %I:%M %p IST')}"
                    for i, s in enumerate(slots)
                )
                confirm_url = (
                    f"{settings.interview_confirmation_base_url}"
                    f"/api/v1/interviews/propose"
                )
                await send_whatsapp(
                    phone_wa,
                    f"Hi {candidate.name.split()[0]}, that's wonderful! 🎉\n\n"
                    f"Here are 3 available slots for your {job.title} interview at "
                    f"{job.company or 'K. Girdharlal International'}:\n\n"
                    f"{slot_lines}\n\n"
                    f"Reply with *1*, *2*, or *3* to confirm your preferred slot. "
                    f"The interview is ~30 minutes via Google Meet.",
                )
                logger.info(
                    "WhatsApp slot proposal sent to candidate %d (%s)",
                    candidate.id, candidate.name,
                )

                # Also create interview record via the scheduling service
                try:
                    await propose_interview_slots(
                        candidate=candidate,
                        job=job,
                        channel=OutreachChannel.WHATSAPP,
                        db=db,
                        slots=[s for s in slots],
                    )
                except Exception as exc:
                    logger.warning("Could not create interview record: %s", exc)

        elif is_negative(body_text):
            # ── Not interested ───────────────────────────────────────────────
            entry.status = ShortlistStatus.NOT_INTERESTED
            await send_whatsapp(
                phone_wa,
                f"Hi {candidate.name.split()[0]}, no problem at all! "
                f"Thank you for letting us know. We'll keep you in mind for "
                f"future opportunities. Wishing you all the best! 🙏",
            )
            logger.info(
                "Candidate %d marked NOT_INTERESTED via WhatsApp", candidate.id
            )

        elif re.search(r"\b[123]\b", body_text.strip()):
            # ── Candidate picked a slot number ───────────────────────────────
            slot_choice = int(re.search(r"\b([123])\b", body_text).group(1)) - 1
            interview_res = await db.execute(
                select(Interview).where(
                    and_(
                        Interview.candidate_id == candidate.id,
                        Interview.job_id == entry.job_id,
                        Interview.status == InterviewStatus.PROPOSED,
                    )
                ).order_by(Interview.created_at.desc())
            )
            interview = interview_res.scalars().first()
            if interview:
                try:
                    slots_raw = json.loads(interview.proposed_slots or "[]")
                    if slot_choice < len(slots_raw):
                        from datetime import datetime
                        interview.scheduled_at = datetime.fromisoformat(slots_raw[slot_choice])
                        interview.status = InterviewStatus.CONFIRMED
                        interview.meet_link = f"https://meet.google.com/new"
                        entry.status = ShortlistStatus.INTERVIEW_SCHEDULED

                        slot_dt = interview.scheduled_at
                        await send_whatsapp(
                            phone_wa,
                            f"✅ Confirmed! Your interview is scheduled for:\n\n"
                            f"📅 *{slot_dt.strftime('%A, %d %b %Y at %I:%M %p IST')}*\n"
                            f"🔗 Google Meet link will be sent 1 hour before.\n"
                            f"Role: {job.title} at {job.company or 'K. Girdharlal International'}\n\n"
                            f"Please join 2 minutes early. See you then! 👋",
                        )
                        logger.info(
                            "Interview %d CONFIRMED via WhatsApp — candidate %d slot %d",
                            interview.id, candidate.id, slot_choice + 1,
                        )
                except Exception as exc:
                    logger.error("Slot confirmation error: %s", exc)
            else:
                await send_whatsapp(
                    phone_wa,
                    "I couldn't find an active slot proposal for you. "
                    "Please reply *YES* and I'll send fresh slots right away.",
                )

        else:
            # ── Unknown reply — ask for YES/NO ───────────────────────────────
            await send_whatsapp(
                phone_wa,
                f"Hi {candidate.name.split()[0]}, thanks for your message! 😊\n\n"
                f"Are you interested in the *{job.title}* role at "
                f"{job.company or 'K. Girdharlal International'}?\n\n"
                f"Reply *YES* to proceed or *NO* if not interested.",
            )

        await db.commit()


@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=""),
):
    """Receives inbound WhatsApp messages forwarded by OpenClaw / WAHA.

    Returns 200 immediately; processing happens in a background task.
    """
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    if event not in ("message", "message.any"):
        return {"status": "ignored", "event": event}

    msg_payload = payload.get("payload", {})
    from_jid: str = msg_payload.get("from", "")
    body_text: str = msg_payload.get("body", "").strip()
    from_me: bool = msg_payload.get("fromMe", False)

    # Ignore our own outbound messages echoed back
    if from_me or not from_jid or not body_text:
        return {"status": "ignored"}

    # Skip group messages (group JIDs end with @g.us)
    if from_jid.endswith("@g.us"):
        return {"status": "ignored", "reason": "group message"}

    session = payload.get("session", settings.openclaw_session)
    background_tasks.add_task(_handle_inbound, from_jid, body_text, session)

    return {"status": "queued"}
