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

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.candidate import Candidate
from app.models.interview import Interview, InterviewStatus
from app.models.job import Job
from app.models.outreach import OutreachChannel, OutreachLog, OutreachStatus, OutreachType
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.conversation import Conversation
from app.services.conversation_agent import converse
from app.services.scheduling import generate_slots, propose_interview_slots
from app.services.whatsapp_openclaw import is_negative, is_positive, send_whatsapp, _extract_phone
from app.utils.logging import get_logger

router = APIRouter(prefix="/webhook", tags=["webhook"])
logger = get_logger(__name__)
settings = get_settings()


# ── WhatsApp health + test endpoints ─────────────────────────────────────────

@router.get("/whatsapp/status")
async def whatsapp_status():
    """Check WAHA session connectivity. Called by System Health page."""
    if not settings.openclaw_api_url:
        return {"configured": False, "status": "not_configured",
                "message": "Set OPENCLAW_API_URL in Vercel env vars"}
    import httpx
    base = settings.openclaw_api_url.rstrip("/")
    headers = {}
    if settings.openclaw_api_key:
        headers["X-Api-Key"] = settings.openclaw_api_key
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{base}/api/sessions/{settings.openclaw_session}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                wstatus = data.get("status", data.get("engine", {}).get("state", "unknown"))
                return {"configured": True, "status": wstatus,
                        "session": settings.openclaw_session,
                        "message": f"Session '{settings.openclaw_session}': {wstatus}"}
            return {"configured": True, "status": "error",
                    "message": f"WAHA returned HTTP {r.status_code}"}
    except Exception as exc:
        return {"configured": True, "status": "unreachable",
                "message": f"Cannot reach {base}: {exc}"}


@router.post("/whatsapp/test")
async def whatsapp_test_send(payload: dict, db: AsyncSession = Depends(get_db)):
    """Send a test WhatsApp message via bridge queue or direct WAHA. Body: {phone, message}"""
    phone = payload.get("phone", "").strip()
    message = payload.get("message", "Test message from K. Girdharlal HR System ✅")
    if not phone:
        raise HTTPException(status_code=422, detail="phone is required")
    msg_id = await send_whatsapp(phone, message, db=db)
    if msg_id:
        via = "bridge-queue" if msg_id.startswith("queued:") else "direct-waha"
        return {"sent": True, "msg_id": msg_id, "to": phone, "via": via}
    raise HTTPException(status_code=502, detail="WhatsApp send failed — bridge not connected and OPENCLAW_API_URL not set")


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


async def _trace(db, status: str, detail: str):
    """Record an inbound-handling step so it's visible even with LOG_LEVEL=WARNING.

    Written to watchdog_log (check_name='wa_inbound') and committed immediately,
    so early-return paths still leave a trail. Readable at GET /wa/inbound-trace.
    """
    try:
        from app.models.watchdog import WatchdogLog
        db.add(WatchdogLog(check_name="wa_inbound", status=status, detail=(detail or "")[:480]))
        await db.commit()
    except Exception as exc:
        logger.warning("inbound trace failed: %s", exc)


async def _handle_inbound(from_jid: str, body_text: str, session: str):
    """Core logic — runs in background after 200 is returned to WAHA.

    Uses Claude AI to classify the reply intent, then auto-responds accordingly.
    Falls back to keyword matching when AI is unavailable.
    """
    phone_10 = _clean_phone(from_jid)
    logger.info("Inbound WhatsApp from %s (%s): %r", from_jid, phone_10, body_text[:80])

    async with AsyncSessionLocal() as db:
        await _trace(db, "RECEIVED", f"from {phone_10}: {body_text[:120]}")

        # Find candidate by phone (match last 10 digits)
        result = await db.execute(
            select(Candidate).where(
                or_(
                    Candidate.phone.like(f"%{phone_10}"),
                    Candidate.whatsapp.like(f"%{phone_10}"),
                )
            )
        )
        candidate = result.scalars().first()
        if not candidate:
            await _trace(db, "NO_CANDIDATE", f"phone {phone_10} not matched to any candidate")
            return

        # Prefer an active entry, but fall back to the most recent entry of ANY
        # status so a genuine reply is never silently dropped on a technicality.
        sl_res = await db.execute(
            select(ShortlistEntry)
            .where(
                and_(
                    ShortlistEntry.candidate_id == candidate.id,
                    ShortlistEntry.status.in_([
                        ShortlistStatus.CONTACTED,
                        ShortlistStatus.INTERESTED,
                        ShortlistStatus.SHORTLISTED,
                        ShortlistStatus.INTERVIEW_SCHEDULED,
                        ShortlistStatus.PENDING,
                    ]),
                )
            )
            .order_by(ShortlistEntry.created_at.desc())
        )
        entry = sl_res.scalars().first()
        if not entry:
            any_res = await db.execute(
                select(ShortlistEntry)
                .where(ShortlistEntry.candidate_id == candidate.id)
                .order_by(ShortlistEntry.created_at.desc())
            )
            entry = any_res.scalars().first()
        if not entry:
            await _trace(db, "NO_ENTRY",
                         f"candidate {candidate.id} ({candidate.name}) has no shortlist entry")
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
        name_parts = (candidate.name or "").split()
        first_name = name_parts[0] if name_parts else "there"
        company = job.company or "K. Girdharlal International"

        # ── Slot number check first (fastest path, no AI needed) ────────────
        if re.search(r"\b[123]\b", body_text.strip()) and not is_positive(body_text):
            slot_match = re.search(r"\b([123])\b", body_text)
            slot_choice = int(slot_match.group(1)) - 1 if slot_match else 0
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
                        raw_slot = slots_raw[slot_choice]
                        try:
                            interview.scheduled_at = datetime.fromisoformat(raw_slot)
                        except ValueError:
                            clean = raw_slot.replace(" IST", "")
                            interview.scheduled_at = datetime.strptime(clean, "%A, %d %B %Y %I:%M %p")
                        interview.status = InterviewStatus.CONFIRMED
                        interview.meet_link = "https://meet.google.com/new"
                        entry.status = ShortlistStatus.INTERVIEW_SCHEDULED
                        slot_dt = interview.scheduled_at
                        await send_whatsapp(
                            phone_wa,
                            f"✅ Confirmed! Your interview is scheduled for:\n\n"
                            f"📅 *{slot_dt.strftime('%A, %d %b %Y at %I:%M %p IST')}*\n"
                            f"🔗 Google Meet link will be sent 1 hour before.\n"
                            f"Role: {job.title} at {company}\n\n"
                            f"Please join 2 minutes early. See you then! 👋",
                            db=db,
                        )
                        logger.info("Interview %d CONFIRMED — candidate %d slot %d", interview.id, candidate.id, slot_choice + 1)
                except Exception as exc:
                    logger.error("Slot confirmation error: %s", exc)
                await db.commit()
                return
            # No active proposal — fall through to AI classification

        # ── Conversation Agent (multi-turn, with memory) ─────────────────────
        salary_info = "No bar for the right candidate"
        if job.salary_min and job.salary_max:
            salary_info = f"₹{int(job.salary_min):,}–₹{int(job.salary_max):,}/month"
        elif job.salary_min:
            salary_info = f"₹{int(job.salary_min):,}+/month"

        # Load (or start) the running conversation thread for this candidate+job
        conv_res = await db.execute(
            select(Conversation).where(
                and_(
                    Conversation.candidate_id == candidate.id,
                    Conversation.job_id == entry.job_id,
                )
            )
        )
        conv = conv_res.scalars().first()
        if not conv:
            conv = Conversation(candidate_id=candidate.id, job_id=entry.job_id,
                                collected={}, history=[], status="ACTIVE")
            db.add(conv)

        classification = await converse(
            candidate=candidate,
            job=job,
            history=conv.history or [],
            collected=conv.collected or {},
            new_text=body_text,
            salary_info=salary_info,
        )

        intent = classification.get("intent", "GENERAL")
        auto_response = classification.get("reply", "")
        needs_human = classification.get("needs_human", False)
        new_collected = classification.get("collected", conv.collected or {})
        logger.info("Reply intent for candidate %d: %s (needs_human=%s)", candidate.id, intent, needs_human)
        await _trace(db, "HANDLED",
                     f"candidate {candidate.id} ({candidate.name}) intent={intent} -> replying")

        if intent == "INTERESTED":
            entry.status = ShortlistStatus.INTERESTED
            await db.flush()

            # Check if slots already proposed
            existing = await db.execute(
                select(Interview).where(
                    and_(
                        Interview.candidate_id == candidate.id,
                        Interview.job_id == entry.job_id,
                        Interview.status.in_([InterviewStatus.PROPOSED, InterviewStatus.CONFIRMED]),
                    )
                )
            )
            if existing.scalars().first():
                await send_whatsapp(
                    phone_wa,
                    f"Hi {first_name}, great! I already sent you interview slot options — "
                    f"please check and reply with *1*, *2*, or *3* to confirm your preferred slot.",
                    db=db,
                )
            else:
                slots = generate_slots(days_ahead=3, num_slots=3)
                slot_lines = "\n".join(
                    f"  {i+1}. {s.strftime('%A %d %b, %I:%M %p IST')}"
                    for i, s in enumerate(slots)
                )
                await send_whatsapp(
                    phone_wa,
                    f"Hi {first_name}, that's wonderful! 🎉\n\n"
                    f"Here are 3 available slots for your *{job.title}* interview at {company}:\n\n"
                    f"{slot_lines}\n\n"
                    f"Reply with *1*, *2*, or *3* to confirm your preferred slot.",
                    db=db,
                )
                try:
                    await propose_interview_slots(
                        candidate=candidate,
                        job=job,
                        channel=OutreachChannel.WHATSAPP,
                        db=db,
                        slots=slots,
                    )
                except Exception as exc:
                    logger.warning("Could not create interview record: %s", exc)

        elif intent == "NOT_INTERESTED" or intent == "WITHDRAWAL":
            entry.status = ShortlistStatus.NOT_INTERESTED
            await send_whatsapp(phone_wa, auto_response, db=db)

        elif intent == "SALARY_QUERY":
            # Send salary info, then re-ask interest
            await send_whatsapp(phone_wa, auto_response, db=db)

        elif intent == "SCHEDULE_QUERY":
            # They're asking about timing — treat as interested, send slots
            entry.status = ShortlistStatus.INTERESTED
            await db.flush()
            existing = await db.execute(
                select(Interview).where(
                    and_(
                        Interview.candidate_id == candidate.id,
                        Interview.job_id == entry.job_id,
                        Interview.status == InterviewStatus.PROPOSED,
                    )
                )
            )
            if existing.scalars().first():
                await send_whatsapp(phone_wa, auto_response, db=db)
            else:
                slots = generate_slots(days_ahead=3, num_slots=3)
                slot_lines = "\n".join(
                    f"  {i+1}. {s.strftime('%A %d %b, %I:%M %p IST')}"
                    for i, s in enumerate(slots)
                )
                await send_whatsapp(
                    phone_wa,
                    f"Hi {first_name}! Here are 3 available slots for your *{job.title}* interview:\n\n"
                    f"{slot_lines}\n\n"
                    f"Reply with *1*, *2*, or *3* to confirm. 📅",
                    db=db,
                )
                try:
                    await propose_interview_slots(candidate=candidate, job=job, channel=OutreachChannel.WHATSAPP, db=db, slots=slots)
                except Exception as exc:
                    logger.warning("Slot proposal error: %s", exc)

        elif intent == "MORE_INFO":
            await send_whatsapp(phone_wa, auto_response, db=db)

        else:
            # GENERAL or unrecognised — ask for clear YES/NO
            await send_whatsapp(phone_wa, auto_response, db=db)

        # ── Persist conversation memory ──────────────────────────────────────
        from datetime import datetime as _dt
        now_iso = _dt.utcnow().isoformat()
        outbound_summary = (
            "(sent interview slot options)"
            if intent in ("INTERESTED", "SCHEDULE_QUERY")
            else (auto_response or "")
        )
        thread = list(conv.history or [])
        thread.append({"dir": "in", "text": body_text[:600], "ts": now_iso})
        if outbound_summary:
            thread.append({"dir": "out", "text": outbound_summary[:600], "ts": now_iso})
        conv.history = thread[-20:]   # keep the last 20 turns
        conv.collected = new_collected
        conv.last_intent = intent
        conv.needs_human = bool(needs_human)
        if intent in ("NOT_INTERESTED", "WITHDRAWAL"):
            conv.status = "NOT_INTERESTED"
        elif intent in ("INTERESTED", "SCHEDULE_QUERY"):
            conv.status = "SCHEDULING"
        elif needs_human:
            conv.status = "NEEDS_HUMAN"
        else:
            conv.status = "ACTIVE"

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
