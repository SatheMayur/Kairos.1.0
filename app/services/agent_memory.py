"""Persistent memory + self-learning for the WhatsApp agent.

Public API:
  set_memory / get_memory / merge_memory   — low-level tree access
  record_candidate_learning(...)           — agent writes what it learned (self-learning)
  build_tree(db)                            — the whole memory tree (for the UI)
  run_sync(db)                              — the every-20-min snapshot + deltas
  build_morning_brief(db)                   — "what happened while you slept" summary

Honest scope: the deployed app can read WhatsApp (DB), outreach (sent emails),
interviews (calendar), and pipeline — so the brief is built from those. Live
Gmail-inbox / Google-Calendar sync needs Google API credentials server-side;
until then those sections summarise what the system itself sent/scheduled.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_memory import AgentMemory
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ── low-level tree access ────────────────────────────────────────────────────

async def get_memory(db: AsyncSession, scope: str, key: str) -> dict | None:
    row = (await db.execute(
        select(AgentMemory).where(AgentMemory.scope == scope, AgentMemory.key == key)
    )).scalar_one_or_none()
    return row.value if row else None


async def set_memory(db: AsyncSession, scope: str, key: str, value: dict) -> None:
    row = (await db.execute(
        select(AgentMemory).where(AgentMemory.scope == scope, AgentMemory.key == key)
    )).scalar_one_or_none()
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(AgentMemory(scope=scope, key=key, value=value))
    await db.flush()


async def merge_memory(db: AsyncSession, scope: str, key: str, partial: dict) -> dict:
    """Merge a partial dict into the existing value (shallow). Returns the merged value."""
    current = await get_memory(db, scope, key) or {}
    current = {**current, **{k: v for k, v in partial.items() if v is not None}}
    await set_memory(db, scope, key, current)
    return current


# ── self-learning: the agent remembers what it learns about each candidate ───

async def record_candidate_learning(
    db: AsyncSession,
    candidate_id: int,
    *,
    collected: dict | None = None,
    intent: str | None = None,
    last_message: str | None = None,
) -> None:
    """Persist what the agent just learned about a candidate so it carries across
    conversations (self-learning). Never raises — memory must not break a reply."""
    try:
        scope = f"candidate:{candidate_id}"
        facts = {k: v for k, v in (collected or {}).items() if not k.startswith("_")}
        mem = await get_memory(db, scope, "profile") or {}
        learned = {**mem.get("facts", {}), **facts}
        seen = int(mem.get("interactions", 0)) + 1
        await set_memory(db, scope, "profile", {
            "facts": learned,
            "last_intent": intent or mem.get("last_intent"),
            "last_message": (last_message or "")[:300] or mem.get("last_message"),
            "interactions": seen,
            "updated_at": datetime.utcnow().isoformat(),
        })
        # Aggregate global signal: count intents seen across all candidates.
        if intent:
            g = await get_memory(db, "global", "intent_counts") or {}
            g[intent] = int(g.get(intent, 0)) + 1
            await set_memory(db, "global", "intent_counts", g)
    except Exception as exc:  # self-learning is best-effort
        logger.warning("record_candidate_learning failed for %s: %s", candidate_id, exc)


async def build_tree(db: AsyncSession) -> dict:
    """Return the whole memory as a nested tree {scope: {key: value}}."""
    rows = (await db.execute(select(AgentMemory))).scalars().all()
    tree: dict[str, dict] = {}
    for r in rows:
        tree.setdefault(r.scope, {})[r.key] = {"value": r.value,
                                               "updated_at": r.updated_at.isoformat() if r.updated_at else None}
    return tree


# ── the every-20-minutes sync ────────────────────────────────────────────────

async def run_sync(db: AsyncSession) -> dict:
    """Snapshot recent activity into memory and compute deltas since the last sync.
    Read-only against the business tables; only writes the 'sync' memory branch.
    Sends nothing — safe to run as often as every few minutes."""
    from app.models.outreach import OutreachLog, OutreachChannel, OutreachStatus
    from app.models.interview import Interview, InterviewStatus
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.conversation import Conversation
    from app.models.candidate import Candidate
    from app.models.job import Job, JobStatus

    now = datetime.utcnow()
    meta = await get_memory(db, "sync", "meta") or {}
    last_sync = meta.get("last_sync_at")
    last_dt = None
    if last_sync:
        try:
            last_dt = datetime.fromisoformat(last_sync)
        except Exception:
            last_dt = None
    window_start = last_dt or (now - timedelta(minutes=20))

    # New inbound replies (candidates who messaged us) since the window start.
    convs = (await db.execute(select(Conversation))).scalars().all()
    new_replies = 0
    repliers: list[dict] = []
    for c in convs:
        for turn in (c.history or []):
            if turn.get("dir") == "in" and turn.get("ts", "") >= window_start.isoformat():
                new_replies += 1
                repliers.append({"candidate_id": c.candidate_id,
                                 "text": (turn.get("text") or "")[:160],
                                 "ts": turn.get("ts")})
    repliers = repliers[-25:]

    # Outreach sent in the window, by channel.
    logs = (await db.execute(select(OutreachLog))).scalars().all()
    sent_window = [l for l in logs
                   if l.sent_at and l.sent_at >= window_start
                   and l.status == OutreachStatus.SENT]
    wa_sent = sum(1 for l in sent_window if l.channel == OutreachChannel.WHATSAPP)
    em_sent = sum(1 for l in sent_window if l.channel == OutreachChannel.EMAIL)

    # Pipeline snapshot (current status mix).
    entries = (await db.execute(select(ShortlistEntry))).scalars().all()
    pipeline: dict[str, int] = {}
    for e in entries:
        pipeline[e.status.value] = pipeline.get(e.status.value, 0) + 1

    # Interviews coming up (confirmed, future) = the calendar branch.
    ivs = (await db.execute(select(Interview))).scalars().all()
    upcoming = sorted(
        [i for i in ivs if i.status == InterviewStatus.CONFIRMED
         and i.scheduled_at and i.scheduled_at >= now],
        key=lambda i: i.scheduled_at,
    )
    overdue = sum(1 for i in ivs if i.status == InterviewStatus.CONFIRMED
                  and i.scheduled_at and i.scheduled_at < now)

    snapshot = {
        "at": now.isoformat(),
        "since": window_start.isoformat(),
        "new_replies": new_replies,
        "whatsapp_sent": wa_sent,
        "email_sent": em_sent,
        "pipeline": pipeline,
        "active_jobs": sum(1 for j in (await db.execute(select(Job))).scalars().all()
                           if j.status == JobStatus.ACTIVE),
        "interested_now": pipeline.get("INTERESTED", 0),
        "upcoming_interviews": len(upcoming),
        "overdue_interviews": overdue,
    }
    await set_memory(db, "sync", "latest", snapshot)
    await set_memory(db, "sync", "recent_replies", {"items": repliers})
    await set_memory(db, "sync", "meta", {"last_sync_at": now.isoformat(),
                                          "synced_count": int(meta.get("synced_count", 0)) + 1})
    await db.commit()
    logger.info("[memory-sync] replies=%d wa=%d email=%d interested=%d",
                new_replies, wa_sent, em_sent, snapshot["interested_now"])
    return snapshot


# ── the morning briefing ─────────────────────────────────────────────────────

async def build_morning_brief(db: AsyncSession, hours: int = 16) -> dict:
    """What happened while you were away — built from WhatsApp, outreach (emails),
    interviews (calendar), and the pipeline. Reads memory for learned insights."""
    from app.models.outreach import OutreachLog, OutreachChannel, OutreachStatus
    from app.models.interview import Interview, InterviewStatus
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.conversation import Conversation
    from app.models.candidate import Candidate
    from app.models.job import Job

    now = datetime.utcnow()
    since = now - timedelta(hours=hours)
    cand_ids: set[int] = set()

    # Overnight WhatsApp replies
    convs = (await db.execute(select(Conversation))).scalars().all()
    wa_replies = []
    for c in convs:
        for turn in (c.history or []):
            if turn.get("dir") == "in" and turn.get("ts", "") >= since.isoformat():
                wa_replies.append({"candidate_id": c.candidate_id,
                                   "text": (turn.get("text") or "")[:200], "ts": turn.get("ts")})
                cand_ids.add(c.candidate_id)

    # Emails the system sent/those that bounced (live Gmail inbox needs creds)
    logs = (await db.execute(select(OutreachLog))).scalars().all()
    emails_sent = [l for l in logs if l.channel == OutreachChannel.EMAIL
                   and l.sent_at and l.sent_at >= since and l.status == OutreachStatus.SENT]
    bounced = [l for l in logs if l.channel == OutreachChannel.EMAIL
               and l.status == OutreachStatus.BOUNCED]

    # Calendar = interviews today + upcoming confirmed
    ivs = (await db.execute(select(Interview))).scalars().all()
    today = now.date()
    todays = [i for i in ivs if i.status == InterviewStatus.CONFIRMED
              and i.scheduled_at and i.scheduled_at.date() == today]
    upcoming = sorted([i for i in ivs if i.status == InterviewStatus.CONFIRMED
                       and i.scheduled_at and i.scheduled_at > now],
                      key=lambda i: i.scheduled_at)[:5]
    for i in todays + upcoming:
        cand_ids.add(i.candidate_id)

    # Warm leads: candidates who said yes (INTERESTED) needing a slot
    entries = (await db.execute(select(ShortlistEntry))).scalars().all()
    interested = [e for e in entries if e.status == ShortlistStatus.INTERESTED]
    for e in interested:
        cand_ids.add(e.candidate_id)
    completed_need_outcome = sum(1 for i in ivs if i.status == InterviewStatus.COMPLETED)

    # Resolve candidate names
    names: dict[int, str] = {}
    if cand_ids:
        cr = await db.execute(select(Candidate).where(Candidate.id.in_(list(cand_ids))))
        names = {c.id: c.name for c in cr.scalars().all()}
    jobs = {j.id: j for j in (await db.execute(select(Job))).scalars().all()}

    def _iv(i):
        return {"candidate": names.get(i.candidate_id, f"#{i.candidate_id}"),
                "role": jobs[i.job_id].title if i.job_id in jobs else "",
                "when": i.scheduled_at.isoformat() if i.scheduled_at else None,
                "round": i.round.value if i.round else None}

    insights = await get_memory(db, "global", "intent_counts") or {}
    sync_meta = await get_memory(db, "sync", "meta") or {}

    # Live Gmail / Google-Calendar snapshot, if an agent with Google access has
    # pushed one (via /memory/external-snapshot). Falls back to the system-only
    # view (sent emails + scheduled interviews) when no snapshot exists.
    ext_gmail = await get_memory(db, "external", "gmail") or {}
    ext_cal = await get_memory(db, "external", "calendar") or {}
    gmail_items = ext_gmail.get("items") or []
    gmail_unread = sum(1 for m in gmail_items if m.get("unread"))

    email_section = {
        "sent": len(emails_sent),
        "bounced": len(bounced),
    }
    if gmail_items:
        email_section["inbox"] = {
            "unread": gmail_unread,
            "recent": gmail_items[:15],
            "fetched_at": ext_gmail.get("fetched_at"),
        }
        email_section["note"] = f"Live inbox snapshot ({len(gmail_items)} recent, {gmail_unread} unread)."
    else:
        email_section["note"] = "Live Gmail inbox sync needs Google credentials; showing emails the system sent."

    calendar_section = {
        "today": [_iv(i) for i in todays],
        "upcoming": [_iv(i) for i in upcoming],
    }
    if ext_cal.get("items") is not None:
        calendar_section["google_events"] = ext_cal.get("items") or []
        calendar_section["fetched_at"] = ext_cal.get("fetched_at")
        calendar_section["note"] = "Includes a live Google Calendar snapshot + scheduled interviews."
    else:
        calendar_section["note"] = "Built from scheduled interviews; live Google Calendar sync needs credentials."

    return {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "greeting": "Good morning, Kirti!",
        "whatsapp": {
            "new_replies": len(wa_replies),
            "items": [{"candidate": names.get(r["candidate_id"], f"#{r['candidate_id']}"),
                       "text": r["text"], "ts": r["ts"]} for r in wa_replies[-15:]],
        },
        "email": email_section,
        "calendar": calendar_section,
        "action_needed": {
            "interested_to_schedule": [{"candidate": names.get(e.candidate_id, f"#{e.candidate_id}"),
                                        "candidate_id": e.candidate_id}
                                       for e in interested[:15]],
            "interviews_to_log_outcome": completed_need_outcome,
        },
        "learned_insights": {"intent_counts": insights},
        "last_sync_at": sync_meta.get("last_sync_at"),
    }
