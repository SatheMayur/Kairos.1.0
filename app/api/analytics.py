"""Analytics API — pipeline metrics, source quality, time-in-stage."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.models.candidate import Candidate, CandidateSource
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.outreach import OutreachLog, OutreachChannel, OutreachStatus
from app.models.interview import Interview, InterviewStatus
from app.models.job import Job

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
async def get_analytics_overview(db: AsyncSession = Depends(get_db)):
    """Full analytics overview: pipeline funnel, source quality, hiring velocity."""

    # ── All shortlist entries ────────────────────────────────────────────────
    sl_res = await db.execute(select(ShortlistEntry))
    entries = sl_res.scalars().all()

    # ── Pipeline funnel (CUMULATIVE: "ever reached this stage") ───────────────
    # Current status alone is misleading — a candidate shortlisted→contacted no
    # longer has SHORTLISTED status, so Shortlisted would read lower than
    # Contacted. We rank by current status and recover stages lost to status
    # overwrites/resets using durable evidence: an OutreachLog that was actually
    # SENT/REPLIED ⇒ reached Contacted; an Interview row ⇒ reached Interview.
    _ol = (await db.execute(
        select(OutreachLog.candidate_id, OutreachLog.job_id, OutreachLog.status)
    )).all()
    contacted_keys = {
        (cid, jid) for cid, jid, st in _ol
        if st in (OutreachStatus.SENT, OutreachStatus.REPLIED)
    }
    _iv = (await db.execute(select(Interview.candidate_id, Interview.job_id))).all()
    interview_keys = {(cid, jid) for cid, jid in _iv}

    STATUS_RANK = {
        ShortlistStatus.PENDING: 0, ShortlistStatus.REJECTED: 0,
        ShortlistStatus.SHORTLISTED: 1, ShortlistStatus.CONTACTED: 2,
        ShortlistStatus.INTERESTED: 3, ShortlistStatus.INTERVIEW_SCHEDULED: 4,
        ShortlistStatus.HIRED: 5,
    }
    # DROPPED means they were contacted then dropped → still reached Contacted.
    if hasattr(ShortlistStatus, "DROPPED"):
        STATUS_RANK[ShortlistStatus.DROPPED] = 2

    def reached_rank(e) -> int:
        r = STATUS_RANK.get(e.status, 0)
        key = (e.candidate_id, e.job_id)
        if key in contacted_keys:
            r = max(r, 2)
        if key in interview_keys:
            r = max(r, 4)
        return r

    ranks = [reached_rank(e) for e in entries]
    funnel = [
        {"stage": "Shortlisted",         "count": sum(1 for r in ranks if r >= 1)},
        {"stage": "Contacted",           "count": sum(1 for r in ranks if r >= 2)},
        {"stage": "Interested",          "count": sum(1 for r in ranks if r >= 3)},
        {"stage": "Interview Scheduled", "count": sum(1 for r in ranks if r >= 4)},
        {"stage": "Hired",               "count": sum(1 for r in ranks if r >= 5)},
    ]
    # Percentages are relative to the TOP of the funnel (Shortlisted) — proper
    # funnel conversion, not a share-of-sum.
    top = funnel[0]["count"]
    for f in funnel:
        f["pct"] = round(f["count"] / top * 100, 1) if top else 0
    total_in_pipeline = top

    # ── Source quality ───────────────────────────────────────────────────────
    cand_res = await db.execute(select(Candidate))
    candidates = cand_res.scalars().all()
    cand_map = {c.id: c for c in candidates}

    source_stats = {}
    for e in entries:
        c = cand_map.get(e.candidate_id)
        src = c.source.value if c and c.source else "MANUAL"
        if src not in source_stats:
            source_stats[src] = {"total": 0, "shortlisted": 0, "contacted": 0, "hired": 0, "scores": []}
        source_stats[src]["total"] += 1
        if e.status in (ShortlistStatus.SHORTLISTED, ShortlistStatus.CONTACTED,
                        ShortlistStatus.INTERESTED, ShortlistStatus.INTERVIEW_SCHEDULED, ShortlistStatus.HIRED):
            source_stats[src]["shortlisted"] += 1
        if e.status in (ShortlistStatus.CONTACTED, ShortlistStatus.INTERESTED,
                        ShortlistStatus.INTERVIEW_SCHEDULED, ShortlistStatus.HIRED):
            source_stats[src]["contacted"] += 1
        if e.status == ShortlistStatus.HIRED:
            source_stats[src]["hired"] += 1
        if e.score:
            source_stats[src]["scores"].append(e.score)

    source_rows = []
    for src, s in source_stats.items():
        avg_score = round(sum(s["scores"]) / len(s["scores"]), 1) if s["scores"] else 0
        shortlist_rate = round(s["shortlisted"] / s["total"] * 100, 0) if s["total"] else 0
        source_rows.append({
            "source": src,
            "total_candidates": s["total"],
            "shortlisted": s["shortlisted"],
            "shortlist_rate": shortlist_rate,
            "avg_score": avg_score,
            "hired": s["hired"],
        })
    source_rows.sort(key=lambda x: x["shortlist_rate"], reverse=True)

    # ── Per-job stats ────────────────────────────────────────────────────────
    job_res = await db.execute(select(Job))
    jobs = {j.id: j for j in job_res.scalars().all()}

    job_stats = {}
    for e in entries:
        jid = e.job_id
        if jid not in job_stats:
            j = jobs.get(jid)
            job_stats[jid] = {
                "job_id": jid,
                "title": j.title if j else f"Job #{jid}",
                "total": 0, "shortlisted": 0, "contacted": 0,
                "interested": 0, "scheduled": 0, "hired": 0, "rejected": 0,
            }
        job_stats[jid]["total"] += 1
        s = e.status
        if s == ShortlistStatus.SHORTLISTED: job_stats[jid]["shortlisted"] += 1
        elif s == ShortlistStatus.CONTACTED:  job_stats[jid]["contacted"] += 1
        elif s == ShortlistStatus.INTERESTED:  job_stats[jid]["interested"] += 1
        elif s == ShortlistStatus.INTERVIEW_SCHEDULED: job_stats[jid]["scheduled"] += 1
        elif s == ShortlistStatus.HIRED:       job_stats[jid]["hired"] += 1
        elif s == ShortlistStatus.REJECTED:    job_stats[jid]["rejected"] += 1

    # ── WhatsApp reply rate ──────────────────────────────────────────────────
    ol_res = await db.execute(
        select(OutreachLog).where(OutreachLog.channel == OutreachChannel.WHATSAPP)
    )
    wa_logs = ol_res.scalars().all()
    wa_sent    = sum(1 for l in wa_logs if l.status in (OutreachStatus.SENT, OutreachStatus.REPLIED))
    wa_replied = sum(1 for l in wa_logs if l.status == OutreachStatus.REPLIED)
    wa_reply_rate = round(wa_replied / wa_sent * 100, 1) if wa_sent else 0

    # ── Email reply rate ─────────────────────────────────────────────────────
    em_res = await db.execute(
        select(OutreachLog).where(OutreachLog.channel == OutreachChannel.EMAIL)
    )
    em_logs = em_res.scalars().all()
    em_sent    = sum(1 for l in em_logs if l.status in (OutreachStatus.SENT, OutreachStatus.REPLIED))
    em_replied = sum(1 for l in em_logs if l.status == OutreachStatus.REPLIED)
    em_reply_rate = round(em_replied / em_sent * 100, 1) if em_sent else 0

    # ── Interview stats ──────────────────────────────────────────────────────
    iv_res = await db.execute(select(Interview))
    interviews = iv_res.scalars().all()
    iv_total     = len(interviews)
    iv_confirmed = sum(1 for i in interviews if i.status == InterviewStatus.CONFIRMED)
    iv_completed = sum(1 for i in interviews if i.status == InterviewStatus.COMPLETED)
    iv_no_show   = sum(1 for i in interviews if i.status == InterviewStatus.NO_SHOW)

    # ── Score distribution ───────────────────────────────────────────────────
    all_scores = [e.score for e in entries if e.score is not None]
    score_buckets = {"90+": 0, "75-89": 0, "60-74": 0, "40-59": 0, "<40": 0}
    for s in all_scores:
        if s >= 90: score_buckets["90+"] += 1
        elif s >= 75: score_buckets["75-89"] += 1
        elif s >= 60: score_buckets["60-74"] += 1
        elif s >= 40: score_buckets["40-59"] += 1
        else: score_buckets["<40"] += 1

    return {
        "summary": {
            "total_candidates": len(candidates),
            "total_in_pipeline": total_in_pipeline,
            "total_hired": sum(1 for e in entries if e.status == ShortlistStatus.HIRED),
            "total_rejected": sum(1 for e in entries if e.status == ShortlistStatus.REJECTED),
            "avg_score": round(sum(all_scores) / len(all_scores), 1) if all_scores else 0,
        },
        "funnel": funnel,
        "sources": source_rows,
        "jobs": list(job_stats.values()),
        "outreach": {
            "wa_sent": wa_sent, "wa_replied": wa_replied, "wa_reply_rate": wa_reply_rate,
            "em_sent": em_sent, "em_replied": em_replied, "em_reply_rate": em_reply_rate,
        },
        "interviews": {
            "total": iv_total, "confirmed": iv_confirmed,
            "completed": iv_completed, "no_show": iv_no_show,
        },
        "score_distribution": score_buckets,
    }
