"""Recruitment-manager orchestrator.

Looks at the real state of hiring each morning, reasons about what matters most
(via Claude, with a deterministic rule-based fallback), and produces a plain-
English plan for Kirti. It does NOT execute actions — the deterministic daily
steps still do that, with their own guardrails. This layer adds judgment and
explanation on top, and decides priority order.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate
from app.models.interview import Interview, InterviewStatus
from app.models.job import Job, JobStatus
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.wa_queue import WAQueue, WAQueueStatus
from app.models.wa_connection import WaConnection
from app.models.daily_plan import DailyPlan

logger = logging.getLogger(__name__)


async def build_situation(db: AsyncSession) -> dict:
    """Gather the numbers that describe today's hiring state."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    yesterday = today_start - timedelta(days=1)
    three_days_ago = now - timedelta(days=3)

    entries = (await db.execute(select(ShortlistEntry))).scalars().all()

    def count(status):
        return sum(1 for e in entries if e.status == status)

    stale_followups = sum(
        1 for e in entries
        if e.status == ShortlistStatus.CONTACTED and e.updated_at and e.updated_at <= three_days_ago
    )

    todays_interviews = (await db.execute(
        select(func.count()).select_from(Interview).where(
            Interview.status.in_([InterviewStatus.CONFIRMED, InterviewStatus.PROPOSED]),
            Interview.scheduled_at >= today_start,
            Interview.scheduled_at < today_end,
        )
    )).scalar() or 0

    awaiting_outcome = (await db.execute(
        select(func.count()).select_from(Interview).where(
            Interview.status == InterviewStatus.CONFIRMED,
            Interview.scheduled_at < now,
        )
    )).scalar() or 0

    new_candidates = (await db.execute(
        select(func.count()).select_from(Candidate).where(Candidate.created_at >= yesterday)
    )).scalar() or 0

    # Active jobs
    jobs = (await db.execute(select(Job).where(Job.status == JobStatus.ACTIVE))).scalars().all()

    # Integrity + data-quality (reuse the existing analysers)
    all_cands = (await db.execute(select(Candidate))).scalars().all()
    from app.services.duplicates import find_duplicates
    from app.services.data_quality import analyze_candidates
    from app.models.outreach import OutreachLog, OutreachStatus
    bounced = frozenset(
        r[0] for r in (await db.execute(
            select(OutreachLog.candidate_id).where(OutreachLog.status == OutreachStatus.BOUNCED)
        )).all()
    )
    dup = find_duplicates(all_cands)["summary"]
    dq = analyze_candidates(all_cands, bounced)["summary"]

    # WhatsApp health
    conn = await db.get(WaConnection, 1)
    wa_live = bool(
        conn and conn.status == "CONNECTED" and conn.last_poll_at
        and (now - conn.last_poll_at) < timedelta(minutes=3)
    )
    wq = (await db.execute(select(WAQueue))).scalars().all()

    return {
        "date": now.strftime("%A, %d %B %Y"),
        "new_candidates_last_24h": new_candidates,
        "pending_review": count(ShortlistStatus.PENDING),
        "shortlisted_not_contacted": count(ShortlistStatus.SHORTLISTED),
        "interested_awaiting_action": count(ShortlistStatus.INTERESTED),
        "stale_followups": stale_followups,
        "interviews_today": todays_interviews,
        "interviews_awaiting_outcome": awaiting_outcome,
        "hired_total": count(ShortlistStatus.HIRED),
        "active_jobs": [j.title for j in jobs],
        "duplicate_groups": dup["resume_clusters"] + dup["contact_clusters"],
        "copy_pasted_resume_groups": dup["resume_clusters"],
        "records_needing_fixing": dq["with_issues"],
        "urgent_record_problems": dq["high"],
        "whatsapp_online": wa_live,
        "whatsapp_failed_messages": sum(1 for m in wq if m.status == WAQueueStatus.FAILED),
    }


def _fallback_plan(s: dict) -> dict:
    """Deterministic prioritisation when Claude isn't available."""
    p = []
    if s["interviews_today"]:
        p.append({
            "title": f"Get ready for today's {s['interviews_today']} interview(s)",
            "why": "Interviews are time-bound and the most important thing on the calendar.",
            "action": "Open the Interviews page to see times and join links.",
        })
    if s["interviews_awaiting_outcome"]:
        p.append({
            "title": f"Log results for {s['interviews_awaiting_outcome']} finished interview(s)",
            "why": "Until you record how they went, those candidates are stuck.",
            "action": "Open Interviews and mark each as hired, rejected, or next round.",
        })
    if s["interested_awaiting_action"]:
        p.append({
            "title": f"{s['interested_awaiting_action']} candidate(s) said YES — move them forward",
            "why": "People who already replied 'interested' are your warmest leads.",
            "action": "Open the Kanban board and schedule their interviews.",
        })
    if s["copy_pasted_resume_groups"]:
        p.append({
            "title": f"Check {s['copy_pasted_resume_groups']} possible copy-pasted resume group(s)",
            "why": "Same resume on different people is a fraud risk — verify before offering.",
            "action": "Open Duplicate Check.",
        })
    if s["urgent_record_problems"]:
        p.append({
            "title": f"Fix {s['urgent_record_problems']} candidate(s) you can't contact",
            "why": "If there's no working email or phone, you can't reach them at all.",
            "action": "Open Needs Fixing.",
        })
    if s["pending_review"]:
        p.append({
            "title": f"Review {s['pending_review']} candidate(s) waiting for a decision",
            "why": "Clearing the review pile keeps good candidates from going cold.",
            "action": "Open Quick Review.",
        })
    if not s["whatsapp_online"]:
        p.append({
            "title": "WhatsApp is offline",
            "why": "Messages are going out by email until it's back. Candidates still get reached.",
            "action": "Start the bridge on your computer when convenient.",
        })

    bits = []
    if s["interviews_today"]:
        bits.append(f"{s['interviews_today']} interview(s) today")
    if s["interested_awaiting_action"]:
        bits.append(f"{s['interested_awaiting_action']} warm candidate(s) to move forward")
    if s["pending_review"]:
        bits.append(f"{s['pending_review']} waiting for review")
    summary = ", ".join(bits) if bits else "things are quiet"
    note = f"Good morning, Kirti! Today: {summary}. I've ordered what matters most below."
    if not p:
        p.append({
            "title": "You're all caught up",
            "why": "Nothing urgent needs your attention right now.",
            "action": "Enjoy a lighter day — the system will keep working in the background.",
        })
    return {"manager_note": note, "priorities": p[:5], "source": "rules"}


async def generate_plan(db: AsyncSession, *, persist: bool = True) -> dict:
    """Build today's situation, reason about it, and (optionally) save the plan."""
    situation = await build_situation(db)

    from app.services.ai_scoring import ai_plan_day
    ai = await ai_plan_day(situation)
    if ai and ai.get("priorities"):
        plan = {
            "manager_note": ai.get("manager_note", ""),
            "priorities": ai.get("priorities", [])[:5],
            "source": "ai",
        }
    else:
        plan = _fallback_plan(situation)

    plan["situation"] = situation

    if persist:
        row = DailyPlan(
            manager_note=plan["manager_note"],
            priorities=plan["priorities"],
            situation=situation,
            source=plan["source"],
        )
        db.add(row)
        await db.flush()
        plan["id"] = row.id
        plan["created_at"] = row.created_at.isoformat()
    return plan


async def get_latest_plan(db: AsyncSession) -> dict | None:
    row = (await db.execute(
        select(DailyPlan).order_by(DailyPlan.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not row:
        return None
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "manager_note": row.manager_note,
        "priorities": row.priorities or [],
        "situation": row.situation or {},
        "source": row.source,
    }
