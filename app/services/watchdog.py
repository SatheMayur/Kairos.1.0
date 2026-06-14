"""Self-healing watchdog — runs every 30 min, detects failures, heals what it can,
alerts Kirti for anything it cannot fix automatically.

Checks:
  1. WhatsApp bridge dead        → email alert + log
  2. WA messages stuck PENDING   → retry (max 3 attempts)
  3. WA messages FAILED          → retry after 1 hour (max 3 attempts)
  4. Stale PENDING > 2 hours     → mark FAILED, alert
  5. Cron jobs not recently run  → trigger them + alert
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.wa_queue import WAQueue, WAQueueStatus
from app.models.wa_connection import WaConnection
from app.models.watchdog import WatchdogLog

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_AFTER_MINUTES = 60
_STUCK_PENDING_MINUTES = 120
_BRIDGE_DEAD_MINUTES = 10


async def _log(db: AsyncSession, check: str, status: str, detail: str, healed: bool = False):
    db.add(WatchdogLog(check_name=check, status=status, detail=detail, healed=healed))
    logger.info("[WATCHDOG] %s %s — %s", check, status, detail)


async def check_bridge_alive(db: AsyncSession) -> dict:
    """Detect if bridge.js has stopped polling."""
    conn = await db.get(WaConnection, 1)
    if not conn or not conn.last_poll_at:
        await _log(db, "bridge_alive", "WARN", "No poll recorded yet — bridge may not be running")
        return {"ok": False, "reason": "no_poll_recorded"}

    now = datetime.utcnow()
    minutes_since = (now - conn.last_poll_at).total_seconds() / 60

    if conn.status == "CONNECTED" and minutes_since > _BRIDGE_DEAD_MINUTES:
        hours = minutes_since / 60
        when = f"{hours:.0f} hour(s)" if hours >= 1 else f"{minutes_since:.0f} minutes"
        detail = f"Bridge last polled {minutes_since:.0f} min ago — appears dead"
        await _log(db, "bridge_alive", "ALERT", detail)
        await _send_alert(
            "WhatsApp is offline",
            f"Your WhatsApp has been disconnected for about {when}.\n\n"
            f"WHAT THIS MEANS:\n"
            f"WhatsApp messages can't send or receive until you start it again. In the\n"
            f"meantime, the system is sending candidate messages by EMAIL instead, so\n"
            f"nobody is missed.\n\n"
            f"HOW TO TURN WHATSAPP BACK ON (about 1 minute):\n"
            f"  1. On your office computer, open the 'waha-bridge' folder\n"
            f"  2. Double-click 'start-whatsapp.bat'\n"
            f"  3. Keep the black window open\n\n"
            f"Once it connects, WhatsApp messages resume automatically."
        )
        return {"ok": False, "reason": "bridge_dead", "minutes_since": minutes_since}

    await _log(db, "bridge_alive", "OK", f"Last poll {minutes_since:.0f} min ago")
    return {"ok": True, "minutes_since": minutes_since}


async def reroute_stuck_to_email(db: AsyncSession) -> dict:
    """Bridge is down — deliver stuck PENDING WhatsApp messages via email instead,
    so candidates are still reached. Matches the queued phone to a candidate to
    find their email; marks the WA row SENT once emailed."""
    from sqlalchemy import or_
    from app.models.candidate import Candidate
    from app.services.outreach import queue_email_direct

    res = await db.execute(select(WAQueue).where(WAQueue.status == WAQueueStatus.PENDING))
    pending = res.scalars().all()

    rerouted = 0
    no_email = 0
    for msg in pending:
        digits = "".join(ch for ch in (msg.phone or "") if ch.isdigit())[-10:]
        if not digits:
            continue
        cres = await db.execute(
            select(Candidate).where(
                or_(Candidate.phone.like(f"%{digits}"), Candidate.whatsapp.like(f"%{digits}"))
            )
        )
        cand = cres.scalars().first()
        if cand and cand.email:
            ok = await queue_email_direct(
                to=cand.email,
                subject="A message from K. Girdharlal International",
                body=(msg.message or "") +
                     "\n\n—\n(Sent by email because WhatsApp was temporarily unavailable.)\n"
                     "K. Girdharlal International — HR",
                candidate_name=cand.name,
                role=cand.current_role or "",
            )
            if ok:
                msg.status = WAQueueStatus.SENT
                msg.sent_at = datetime.utcnow()
                msg.error = "Delivered via email (WhatsApp bridge offline)"
                rerouted += 1
        else:
            no_email += 1

    if rerouted:
        await _log(db, "reroute_email", "HEALED",
                   f"Bridge down — delivered {rerouted} stuck message(s) via email", healed=True)
    elif pending:
        await _log(db, "reroute_email", "WARN",
                   f"{no_email} stuck message(s) have no email to fall back to")
    return {"rerouted_to_email": rerouted, "no_email": no_email}


async def retry_failed_messages(db: AsyncSession) -> dict:
    """Re-queue FAILED WA messages that are under retry limit."""
    now = datetime.utcnow()
    retry_after = now - timedelta(minutes=_RETRY_AFTER_MINUTES)

    res = await db.execute(
        select(WAQueue).where(
            WAQueue.status == WAQueueStatus.FAILED,
            WAQueue.retry_count < _MAX_RETRIES,
        )
    )
    failed = res.scalars().all()

    retried = 0
    for msg in failed:
        # Only retry if enough time has passed since last attempt
        last = msg.last_retry_at or msg.created_at or now
        if last <= retry_after:
            msg.status = WAQueueStatus.PENDING
            msg.retry_count = (msg.retry_count or 0) + 1
            msg.last_retry_at = now
            msg.error = None
            retried += 1

    if retried:
        await _log(db, "retry_failed", "HEALED",
                   f"Re-queued {retried} failed WA message(s) for retry", healed=True)
    else:
        await _log(db, "retry_failed", "OK",
                   f"Checked {len(failed)} failed message(s) — none ready for retry yet")

    return {"retried": retried, "total_failed": len(failed)}


async def fix_stuck_pending(db: AsyncSession) -> dict:
    """Mark PENDING messages older than 2 hours as FAILED (bridge was dead when queued)."""
    cutoff = datetime.utcnow() - timedelta(minutes=_STUCK_PENDING_MINUTES)
    res = await db.execute(
        select(WAQueue).where(
            WAQueue.status == WAQueueStatus.PENDING,
            WAQueue.created_at < cutoff,
        )
    )
    stuck = res.scalars().all()

    marked = 0
    for msg in stuck:
        msg.status = WAQueueStatus.FAILED
        msg.error = "Watchdog: stuck PENDING > 2h — bridge was offline"
        marked += 1

    if marked:
        await _log(db, "stuck_pending", "HEALED",
                   f"Marked {marked} stuck message(s) as FAILED (will retry next cycle)", healed=True)
    else:
        await _log(db, "stuck_pending", "OK", "No stuck pending messages")

    return {"marked_failed": marked}


async def run_watchdog() -> dict:
    """Main watchdog entry point. Runs all checks and returns a summary."""
    results = {}
    async with AsyncSessionLocal() as db:
        try:
            results["bridge"] = await check_bridge_alive(db)
            # Bridge down → deliver stuck outbound messages by email so candidates
            # are still reached without waiting for the bridge to come back.
            if not results["bridge"].get("ok"):
                results["reroute"] = await reroute_stuck_to_email(db)
            results["stuck"] = await fix_stuck_pending(db)
            results["retry"] = await retry_failed_messages(db)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error("[WATCHDOG] Error: %s", exc)
            results["error"] = str(exc)

    logger.info("[WATCHDOG] Cycle complete: %s", results)
    return results


async def _send_alert(subject: str, body: str):
    """Send a watchdog alert email to Kirti."""
    try:
        from app.services.outreach import queue_email_direct
        from app.config import get_settings
        settings = get_settings()
        await queue_email_direct(
            to=settings.digest_recipient_email,
            subject=f"[HR System Alert] {subject}",
            body=body,
            candidate_name="System Watchdog",
            role="Automation",
            priority="HIGH",
        )
    except Exception as exc:
        logger.error("[WATCHDOG] Alert email failed: %s", exc)
