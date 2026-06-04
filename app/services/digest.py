"""Morning HR digest — sent every day at 8 AM.

Covers:
  1. Today's interviews (who, when, what role, Google Meet link)
  2. Candidates needing follow-up (no reply in 3+ days)
  3. Pipeline snapshot (count per stage)
  4. WhatsApp queue health (pending / failed)
  5. New candidates imported in the last 24 hours
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.models.candidate import Candidate
from app.models.interview import Interview, InterviewStatus
from app.models.job import Job
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.wa_queue import WAQueue, WAQueueStatus

logger = logging.getLogger(__name__)


async def generate_digest(db: AsyncSession) -> tuple[str, str]:
    """Return (subject, html_body) for the morning digest email."""

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)
    yesterday   = today_start - timedelta(days=1)
    three_days_ago = now - timedelta(days=3)

    # ── Today's interviews ───────────────────────────────────────────────────
    iv_res = await db.execute(
        select(Interview).where(
            Interview.status.in_([InterviewStatus.CONFIRMED, InterviewStatus.PROPOSED]),
            Interview.scheduled_at >= today_start,
            Interview.scheduled_at < today_end,
        ).order_by(Interview.scheduled_at)
    )
    todays_interviews = iv_res.scalars().all()

    cand_ids = list({i.candidate_id for i in todays_interviews})
    job_ids  = list({i.job_id for i in todays_interviews})
    cand_map = {}
    job_map  = {}
    if cand_ids:
        cr = await db.execute(select(Candidate).where(Candidate.id.in_(cand_ids)))
        cand_map = {c.id: c for c in cr.scalars().all()}
    if job_ids:
        jr = await db.execute(select(Job).where(Job.id.in_(job_ids)))
        job_map = {j.id: j for j in jr.scalars().all()}

    # ── Candidates needing follow-up (CONTACTED, no reply in 3+ days) ────────
    contacted_res = await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.status == ShortlistStatus.CONTACTED,
            ShortlistEntry.updated_at <= three_days_ago,
        ).order_by(ShortlistEntry.updated_at)
        .limit(20)
    )
    needs_followup = contacted_res.scalars().all()

    fu_cand_ids = list({e.candidate_id for e in needs_followup})
    fu_job_ids  = list({e.job_id for e in needs_followup})
    fu_cands = {}
    fu_jobs  = {}
    if fu_cand_ids:
        fcr = await db.execute(select(Candidate).where(Candidate.id.in_(fu_cand_ids)))
        fu_cands = {c.id: c for c in fcr.scalars().all()}
    if fu_job_ids:
        fjr = await db.execute(select(Job).where(Job.id.in_(fu_job_ids)))
        fu_jobs = {j.id: j for j in fjr.scalars().all()}

    # ── Pipeline snapshot ────────────────────────────────────────────────────
    all_entries_res = await db.execute(select(ShortlistEntry))
    all_entries = all_entries_res.scalars().all()
    pipeline = {
        "shortlisted":   sum(1 for e in all_entries if e.status == ShortlistStatus.SHORTLISTED),
        "contacted":     sum(1 for e in all_entries if e.status == ShortlistStatus.CONTACTED),
        "interested":    sum(1 for e in all_entries if e.status == ShortlistStatus.INTERESTED),
        "scheduled":     sum(1 for e in all_entries if e.status == ShortlistStatus.INTERVIEW_SCHEDULED),
        "pending":       sum(1 for e in all_entries if e.status == ShortlistStatus.PENDING),
        "hired":         sum(1 for e in all_entries if e.status == ShortlistStatus.HIRED),
    }

    # ── WhatsApp queue health ────────────────────────────────────────────────
    wq_res = await db.execute(select(WAQueue))
    wq_all = wq_res.scalars().all()
    wq_pending = sum(1 for m in wq_all if m.status == WAQueueStatus.PENDING)
    wq_failed  = sum(1 for m in wq_all if m.status == WAQueueStatus.FAILED)

    # ── New candidates (last 24 h) ───────────────────────────────────────────
    new_cands_res = await db.execute(
        select(Candidate).where(Candidate.created_at >= yesterday).order_by(Candidate.created_at.desc())
    )
    new_candidates = new_cands_res.scalars().all()

    # ── Build email body ─────────────────────────────────────────────────────
    date_str = now.strftime("%A, %d %B %Y")
    subject = f"📋 Daily HR Briefing — {date_str}"

    sections = []

    # Today's interviews
    if todays_interviews:
        rows = ""
        for iv in todays_interviews:
            c = cand_map.get(iv.candidate_id)
            j = job_map.get(iv.job_id)
            time_str = iv.scheduled_at.strftime("%I:%M %p IST") if iv.scheduled_at else "TBD"
            meet = f'<a href="{iv.meet_link}">Join Meet</a>' if iv.meet_link else "Link TBD"
            rows += f"<tr><td style='padding:6px 10px'><b>{time_str}</b></td><td style='padding:6px 10px'>{c.name if c else 'Unknown'}</td><td style='padding:6px 10px'>{j.title if j else 'Unknown role'}</td><td style='padding:6px 10px'>{iv.round.value}</td><td style='padding:6px 10px'>{meet}</td></tr>"
        sections.append(f"""
<h2 style='color:#10b981;font-size:14px;margin:20px 0 8px'>📅 Today's Interviews ({len(todays_interviews)})</h2>
<table border='0' cellpadding='0' cellspacing='0' style='width:100%;border-collapse:collapse;font-size:13px'>
  <tr style='background:#1c1c1f;color:#a1a1aa;font-size:11px;text-transform:uppercase'>
    <th style='padding:6px 10px;text-align:left'>Time</th>
    <th style='padding:6px 10px;text-align:left'>Candidate</th>
    <th style='padding:6px 10px;text-align:left'>Role</th>
    <th style='padding:6px 10px;text-align:left'>Round</th>
    <th style='padding:6px 10px;text-align:left'>Link</th>
  </tr>
  {rows}
</table>""")
    else:
        sections.append("<p style='color:#a1a1aa;font-size:13px'>📅 No interviews scheduled for today.</p>")

    # Needs follow-up
    if needs_followup:
        items = ""
        for e in needs_followup[:10]:
            c = fu_cands.get(e.candidate_id)
            j = fu_jobs.get(e.job_id)
            days_since = int((now - e.updated_at).total_seconds() / 86400) if e.updated_at else "?"
            items += f"<li style='margin-bottom:4px'><b>{c.name if c else '#'+str(e.candidate_id)}</b> — {j.title if j else 'Unknown role'} ({days_since} days, no reply)</li>"
        sections.append(f"""
<h2 style='color:#fbbf24;font-size:14px;margin:20px 0 8px'>⚠️ Needs Follow-Up ({len(needs_followup)})</h2>
<ul style='font-size:13px;color:#d4d4d8;padding-left:18px;margin:0'>{items}</ul>
<p style='font-size:11px;color:#71717a;margin-top:6px'>These candidates were contacted 3+ days ago with no reply. Consider sending a WhatsApp follow-up.</p>""")

    # Pipeline snapshot
    sections.append(f"""
<h2 style='color:#60a5fa;font-size:14px;margin:20px 0 8px'>📊 Pipeline Snapshot</h2>
<table border='0' style='font-size:13px;color:#d4d4d8'>
  <tr><td style='padding:3px 20px 3px 0;color:#a1a1aa'>Pending Review</td><td><b>{pipeline['pending']}</b></td></tr>
  <tr><td style='padding:3px 20px 3px 0;color:#a1a1aa'>Shortlisted</td><td><b style='color:#60a5fa'>{pipeline['shortlisted']}</b></td></tr>
  <tr><td style='padding:3px 20px 3px 0;color:#a1a1aa'>Contacted</td><td><b style='color:#fbbf24'>{pipeline['contacted']}</b></td></tr>
  <tr><td style='padding:3px 20px 3px 0;color:#a1a1aa'>Interested</td><td><b style='color:#a78bfa'>{pipeline['interested']}</b></td></tr>
  <tr><td style='padding:3px 20px 3px 0;color:#a1a1aa'>Interview Scheduled</td><td><b style='color:#34d399'>{pipeline['scheduled']}</b></td></tr>
  <tr><td style='padding:3px 20px 3px 0;color:#a1a1aa'>Hired ✓</td><td><b style='color:#10b981'>{pipeline['hired']}</b></td></tr>
</table>""")

    # WhatsApp health
    if wq_failed > 0:
        sections.append(f"""
<h2 style='color:#f87171;font-size:14px;margin:20px 0 8px'>📵 WhatsApp Alert</h2>
<p style='font-size:13px;color:#d4d4d8'>{wq_failed} message(s) failed to send. {wq_pending} waiting to send.</p>
<p style='font-size:12px;color:#a1a1aa'>Go to <a href='https://kgirdharlal-recruitment.vercel.app/ui/whatsapp'>WhatsApp page</a> to retry or check connection.</p>""")
    elif wq_pending > 0:
        sections.append(f"<p style='font-size:13px;color:#a1a1aa;margin-top:12px'>📱 WhatsApp: {wq_pending} message(s) queued to send, {wq_failed} failed.</p>")
    else:
        sections.append("<p style='font-size:13px;color:#34d399;margin-top:12px'>📱 WhatsApp: All clear — no pending or failed messages.</p>")

    # New candidates
    if new_candidates:
        names = ", ".join(c.name for c in new_candidates[:5])
        extra = f" + {len(new_candidates)-5} more" if len(new_candidates) > 5 else ""
        sections.append(f"""
<h2 style='color:#a78bfa;font-size:14px;margin:20px 0 8px'>🆕 New Candidates Yesterday ({len(new_candidates)})</h2>
<p style='font-size:13px;color:#d4d4d8'>{names}{extra}</p>""")

    body = f"""
<div style='font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;background:#09090b;color:#f4f4f5;max-width:640px;margin:0 auto;padding:32px 24px;border-radius:12px'>
  <div style='border-bottom:1px solid #27272a;padding-bottom:16px;margin-bottom:20px'>
    <h1 style='font-size:18px;font-weight:700;margin:0;color:#f4f4f5'>Good morning, Kirti! ☀️</h1>
    <p style='color:#71717a;font-size:12px;margin:4px 0 0'>HR Intelligence Daily — {date_str}</p>
  </div>
  {''.join(sections)}
  <div style='border-top:1px solid #27272a;margin-top:28px;padding-top:14px;font-size:11px;color:#52525b'>
    K. Girdharlal International · Automated HR Intelligence ·
    <a href='https://kgirdharlal-recruitment.vercel.app' style='color:#10b981'>Open Dashboard</a>
  </div>
</div>"""

    logger.info("[DIGEST] Generated digest with %d interviews, %d follow-ups needed", len(todays_interviews), len(needs_followup))
    return subject, body
