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


async def generate_digest(db: AsyncSession, run_results: dict | None = None) -> tuple[str, str]:
    """Return (subject, html_body) for the morning digest email.

    When ``run_results`` is provided (from the daily autonomous run), the email
    leads with a plain-English summary of what the system did on its own.
    """

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

    # ── Things that need a human decision ────────────────────────────────────
    from app.models.outreach import OutreachLog, OutreachStatus
    from app.services.duplicates import find_duplicates
    from app.services.data_quality import analyze_candidates

    all_cands_res = await db.execute(select(Candidate))
    all_cands = all_cands_res.scalars().all()

    bounced_res = await db.execute(
        select(OutreachLog.candidate_id).where(OutreachLog.status == OutreachStatus.BOUNCED)
    )
    bounced_ids = frozenset(r[0] for r in bounced_res.all())

    dup_summary = find_duplicates(all_cands)["summary"]
    dq_summary = analyze_candidates(all_cands, bounced_ids)["summary"]

    # Interviews that already happened but have no logged outcome yet
    awaiting_outcome_res = await db.execute(
        select(Interview).where(
            Interview.status == InterviewStatus.CONFIRMED,
            Interview.scheduled_at < now,
        )
    )
    awaiting_outcome = len(awaiting_outcome_res.scalars().all())

    # ── Build email body ─────────────────────────────────────────────────────
    date_str = now.strftime("%A, %d %B %Y")
    subject = f"📋 Daily HR Briefing — {date_str}"

    sections = []

    # What the system did on its own this morning (daily autonomous run only)
    if run_results:
        def _n(step, *keys):
            d = run_results.get(step) or {}
            if not isinstance(d, dict) or "error" in d:
                return None
            for k in keys:
                if k in d:
                    return d[k]
            return None

        sourced = 0
        src = run_results.get("source") or {}
        if isinstance(src, dict) and "jobs" in src:
            for jr in src["jobs"].values():
                sourced += (jr or {}).get("new_entries", 0) if isinstance(jr, dict) else 0
        contacted = _n("outreach", "sent_total") or 0
        _out = run_results.get("outreach") or {}
        _wa_live_run = _out.get("whatsapp_live", True) if isinstance(_out, dict) else True
        _channel_note = "" if _wa_live_run else " <span style='color:#fbbf24'>(by email — WhatsApp was offline)</span>"
        fu = run_results.get("followup") or {}
        followups = (fu.get("followup1_sent", 0) + fu.get("followup2_sent", 0)) if isinstance(fu, dict) and "error" not in fu else 0
        reminders = _n("reminders", "reminders_sent") or 0
        pi = run_results.get("post_interview") or {}
        wrapped = pi.get("auto_completed", 0) if isinstance(pi, dict) and "error" not in pi else 0

        did_items = "".join(
            f"<li style='margin-bottom:4px'>{txt}</li>" for txt in [
                f"Found <b>{sourced}</b> new matching candidate(s) and scored them",
                f"Sent first contact to <b>{contacted}</b> shortlisted candidate(s){_channel_note}",
                f"Sent <b>{followups}</b> follow-up message(s) to people who hadn't replied",
                f"Sent <b>{reminders}</b> interview reminder(s)",
                f"Wrapped up <b>{wrapped}</b> finished interview(s)",
            ]
        )
        sections.append(f"""
<div style='background:#0c1f17;border:1px solid #10b98140;border-radius:10px;padding:14px 16px;margin-bottom:8px'>
<h2 style='color:#10b981;font-size:14px;margin:0 0 8px'>🤖 What I did automatically this morning</h2>
<ul style='font-size:13px;color:#d4d4d8;padding-left:18px;margin:0'>{did_items}</ul>
<p style='font-size:11px;color:#71717a;margin:8px 0 0'>You didn't have to do anything for the above — it ran on its own at 10:00 AM.</p>
</div>""")

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

    # Needs your decision — the short list of things only a human can settle
    base_url = "https://kgirdharlal-recruitment.vercel.app"
    decision_rows = []
    if pipeline["pending"] > 0:
        decision_rows.append((f"{pipeline['pending']} candidate(s) waiting for your review",
                              "Quick Review", f"{base_url}/ui/triage"))
    if awaiting_outcome > 0:
        decision_rows.append((f"{awaiting_outcome} interview(s) finished — log how they went",
                              "Interviews", f"{base_url}/ui/interviews"))
    if dup_summary["resume_clusters"] > 0 or dup_summary["contact_clusters"] > 0:
        total_dup = dup_summary["resume_clusters"] + dup_summary["contact_clusters"]
        decision_rows.append((f"{total_dup} possible duplicate group(s) to check "
                              f"({dup_summary['resume_clusters']} copy-pasted résumé groups)",
                              "Duplicate Check", f"{base_url}/ui/duplicates"))
    if dq_summary["with_issues"] > 0:
        decision_rows.append((f"{dq_summary['with_issues']} candidate record(s) need fixing "
                              f"({dq_summary['high']} urgent)",
                              "Needs Fixing", f"{base_url}/ui/needs-fixing"))

    if decision_rows:
        items = "".join(
            f"<li style='margin-bottom:6px'>{txt} — "
            f"<a href='{url}' style='color:#10b981'>{label} →</a></li>"
            for txt, label, url in decision_rows
        )
        sections.append(f"""
<h2 style='color:#fb923c;font-size:14px;margin:20px 0 8px'>🔔 Needs Your Decision</h2>
<ul style='font-size:13px;color:#d4d4d8;padding-left:18px;margin:0'>{items}</ul>
<p style='font-size:11px;color:#71717a;margin-top:6px'>These are the only things the system cannot decide on its own.</p>""")
    else:
        sections.append("<p style='font-size:13px;color:#34d399;margin-top:16px'>🔔 Nothing needs your decision right now — you're all caught up.</p>")

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
