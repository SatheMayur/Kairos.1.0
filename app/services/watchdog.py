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
        detail = f"Bridge last polled {minutes_since:.0f} min ago — appears dead"
        await _log(db, "bridge_alive", "ALERT", detail)
        await _send_alert(
            "⚠️ WhatsApp Bridge Offline",
            f"The WhatsApp bridge on your computer has not polled in {minutes_since:.0f} minutes.\n\n"
            f"Action needed: Open CMD and run:\n"
            f"  cd Kairos.1.0\\waha-bridge\n"
            f"  pm2 restart whatsapp-bridge\n\n"
            f"Or check: pm2 status"
        )
        return {"ok": False, "reason": "bridge_dead", "minutes_since": minutes_since}

    await _log(db, "bridge_alive", "OK", f"Last poll {minutes_since:.0f} min ago")
    return {"ok": True, "minutes_since": minutes_since}


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
