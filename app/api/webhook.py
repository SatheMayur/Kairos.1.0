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
from app.utils.phone import to_local_10

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
    """Extract the phone digits from ANY WhatsApp JID, e.g.
    '919876543210@c.us' / '...@s.whatsapp.net' / '...@lid' → '9876543210'."""
    return to_local_10(chat_id)


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


async def _front_desk(db, phone_10: str, body_text: str, push_name: str | None,
                      raw_jid: str | None = None):
    """HR front desk for people not yet in the system. Like a real employee, it
    answers anyone who messages — but only ENGAGES (and creates a lead) when the AI
    judges it's a genuine job inquiry, so it never auto-replies to spam, vendors,
    or personal contacts. Without an AI provider it stays silent (just flags)."""
    from app.services.llm import llm_provider, llm_json
    from app.models.job import Job, JobStatus

    if llm_provider() == "none":
        await _trace(db, "NO_CANDIDATE", f"phone {phone_10} not matched (front desk needs AI)")
        return

    jobs = (await db.execute(select(Job).where(Job.status == JobStatus.ACTIVE))).scalars().all()
    roles = ", ".join(j.title for j in jobs) or "various roles"

    prompt = f"""You are the HR front desk for K. Girdharlal International, a diamond manufacturer in Surat.
Someone NOT in our system messaged the company WhatsApp. Open roles: {roles}.
Their WhatsApp name: {push_name or 'unknown'}
Their message: "{body_text}"

Decide if this is a genuine job-seeker / recruitment inquiry — NOT spam, a forward, a vendor,
or personal chatter. If yes, write a warm, short WhatsApp reply: greet them, say we're hiring for
{roles}, and ask which role interests them plus their name, current role and total experience.

Return ONLY JSON: {{"is_jobseeker": true|false, "name": "<their name or empty>", "reply": "<reply or empty>"}}"""

    r = await llm_json(prompt, max_tokens=400)
    if not r or not r.get("is_jobseeker") or not r.get("reply"):
        await _trace(db, "NO_CANDIDATE", f"phone {phone_10}: not a job inquiry — left for human review")
        return

    from app.models.candidate import Candidate, CandidateSource
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.conversation import Conversation

    name = (r.get("name") or push_name or "WhatsApp Lead").strip()[:120]
    cand = Candidate(name=name, phone=phone_10, whatsapp=phone_10, source=CandidateSource.MANUAL)
    db.add(cand)
    await db.flush()

    # Put the new lead into the pipeline so the recruiter sees them. Attach to the
    # first open role as a placeholder; the greeting asks which role they actually want.
    if jobs:
        db.add(ShortlistEntry(job_id=jobs[0].id, candidate_id=cand.id, score=0,
                              status=ShortlistStatus.PENDING))
        db.add(Conversation(
            candidate_id=cand.id, job_id=jobs[0].id, collected={}, status="ACTIVE",
            last_intent="GENERAL",
            history=[{"dir": "in", "text": body_text[:300]},
                     {"dir": "out", "text": r["reply"][:300]}],
        ))

    reply_to = raw_jid if (raw_jid and "@" in raw_jid) else phone_10
    await send_whatsapp(reply_to, r["reply"], db=db)
    await _trace(db, "LEAD_CREATED", f"new lead #{cand.id} ({name}) from {phone_10}")
    await db.commit()


async def _handle_inbound(from_jid: str, body_text: str, session: str,
                          raw_jid: str | None = None, push_name: str | None = None):
    """Core logic — runs in background after 200 is returned to WAHA.

    Uses Claude AI to classify the reply intent, then auto-responds accordingly.
    Falls back to keyword matching when AI is unavailable.
    """
    phone_10 = _clean_phone(from_jid)
    logger.info("Inbound WhatsApp from %s (%s): %r", from_jid, phone_10, body_text[:80])

    async with AsyncSessionLocal() as db:
        raw_note = ""
        if raw_jid and raw_jid != from_jid:
            raw_note = f" (raw {raw_jid})"
        await _trace(db, "RECEIVED", f"from {phone_10}{raw_note}: {body_text[:110]}")

        # Guard: broadcasts / status / malformed senders have no real number.
        # Without this, an empty phone makes the LIKE query match a random candidate.
        if len(phone_10) < 8 or from_jid.endswith("@broadcast") or "broadcast" in (from_jid or "").lower():
            await _trace(db, "IGNORED", f"non-personal sender ({(from_jid or '')[:40]})")
            return

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
            # Unknown sender → HR front desk: greet & capture genuine job-seekers.
            await _front_desk(db, phone_10, body_text, push_name, raw_jid)
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
        # Reply to the EXACT conversation the candidate messaged from. For WhatsApp
        # privacy IDs (<id>@lid) the stored digits aren't a routable number, so a
        # reply built from them never arrives — sending to the original JID does.
        reply_to = raw_jid if (raw_jid and "@" in raw_jid) else phone_wa
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
                            reply_to,
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
        action = classification.get("action", "answer")
        auto_response = classification.get("reply", "")
        needs_human = classification.get("needs_human", False)
        new_collected = classification.get("collected", conv.collected or {})
        logger.info("Reply for candidate %d: intent=%s action=%s", candidate.id, intent, action)
        await _trace(db, "HANDLED",
                     f"candidate {candidate.id} ({candidate.name}) intent={intent} action={action}")

        # ── Act on the recruiter agent's decision ───────────────────────────
        # The agent screens first (asks CTC / notice / location) and only chooses
        # "schedule" once it has enough — so we never jump straight to slots.
        if action == "close":
            entry.status = ShortlistStatus.NOT_INTERESTED
            await send_whatsapp(reply_to, auto_response, db=db)

        elif action == "schedule":
            entry.status = ShortlistStatus.INTERESTED
            await db.flush()
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
                    reply_to,
                    f"{auto_response}\n\nI've already shared a few interview slots — "
                    f"please reply with *1*, *2*, or *3* to confirm your preferred time.",
                    db=db,
                )
            else:
                slots = generate_slots(days_ahead=3, num_slots=3)
                slot_lines = "\n".join(
                    f"  {i+1}. {s.strftime('%A %d %b, %I:%M %p IST')}"
                    for i, s in enumerate(slots)
                )
                await send_whatsapp(
                    reply_to,
                    f"{auto_response}\n\nHere are 3 available slots for your *{job.title}* "
                    f"interview at {company}:\n\n{slot_lines}\n\n"
                    f"Reply with *1*, *2*, or *3* to confirm your preferred slot.",
                    db=db,
                )
                try:
                    await propose_interview_slots(
                        candidate=candidate, job=job,
                        channel=OutreachChannel.WHATSAPP, db=db, slots=slots,
                    )
                except Exception as exc:
                    logger.warning("Could not create interview record: %s", exc)

        else:  # ask_info / answer — screen or answer, but do NOT schedule yet
            already_escalated = (conv.status == "NEEDS_HUMAN")
            if needs_human and already_escalated:
                # Already handed to a human on a previous turn — stay silent so we
                # don't keep repeating the same message. Let Kirti take over.
                auto_response = ""
            else:
                if not needs_human and intent in ("INTERESTED", "SCHEDULE_QUERY", "SALARY_QUERY", "MORE_INFO"):
                    entry.status = ShortlistStatus.INTERESTED
                await send_whatsapp(reply_to, auto_response, db=db)

        # ── Persist conversation memory ──────────────────────────────────────
        from datetime import datetime as _dt
        now_iso = _dt.utcnow().isoformat()
        outbound_summary = (
            "(sent interview slot options)" if action == "schedule" else (auto_response or "")
        )
        thread = list(conv.history or [])
        thread.append({"dir": "in", "text": body_text[:600], "ts": now_iso})
        if outbound_summary:
            thread.append({"dir": "out", "text": outbound_summary[:600], "ts": now_iso})
        conv.history = thread[-20:]   # keep the last 20 turns
        conv.collected = new_collected
        conv.last_intent = intent
        conv.needs_human = bool(needs_human)
        if action == "close":
            conv.status = "NOT_INTERESTED"
        elif action == "schedule":
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
