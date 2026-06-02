"""Morning digest — AI-generated daily pipeline briefing sent to the recruiter."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate
from app.models.interview import Interview, InterviewStatus
from app.models.job import Job
from app.models.shortlist import ShortlistEntry, ShortlistStatus

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)


async def generate_digest(db: AsyncSession) -> tuple[str, str]:
    """Generate subject + body for the morning pipeline digest.

    Uses Claude AI if configured, otherwise produces a plain text summary.
    Returns (subject, body).
    """
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + IST_OFFSET
    today_ist = now_ist.date()

    # Gather data
    jobs_res = await db.execute(select(Job))
    jobs = jobs_res.scalars().all()
    active_jobs = [j for j in jobs if j.status.value == "ACTIVE"]

    sl_res = await db.execute(select(ShortlistEntry))
    all_entries = sl_res.scalars().all()

    int_res = await db.execute(select(Interview))
    all_interviews = int_res.scalars().all()

    # Pipeline counts
    status_counts: dict[str, int] = {}
    for e in all_entries:
        k = e.status.value
        status_counts[k] = status_counts.get(k, 0) + 1

    # Today's and tomorrow's interviews
    upcoming = []
    for iv in all_interviews:
        if iv.scheduled_at and iv.status in (InterviewStatus.CONFIRMED, InterviewStatus.PROPOSED):
            diff_days = (iv.scheduled_at.date() - today_ist).days
            if 0 <= diff_days <= 1:
                upcoming.append(iv)

    # Candidates pending follow-up (CONTACTED 3+ days ago)
    follow_up_due = []
    for e in all_entries:
        if e.status == ShortlistStatus.CONTACTED:
            diff = (now_utc - e.created_at.replace(tzinfo=timezone.utc)).days if e.created_at else 0
            if diff >= 3:
                follow_up_due.append(e)

    shortlisted_count = status_counts.get("SHORTLISTED", 0)
    contacted_count = status_counts.get("CONTACTED", 0)
    interested_count = status_counts.get("INTERESTED", 0)
    hired_count = status_counts.get("HIRED", 0)

    # Build plain text summary
    lines = [
        f"Good morning! Here is your HR pipeline briefing for {today_ist.strftime('%A, %d %B %Y')}.",
        "",
        "=== PIPELINE STATUS ===",
        f"Active Jobs: {len(active_jobs)}",
        f"Shortlisted (awaiting outreach): {shortlisted_count}",
        f"Contacted (awaiting reply): {contacted_count}",
        f"Interested (interview pending): {interested_count}",
        f"Hired this cycle: {hired_count}",
        "",
    ]

    if upcoming:
        lines.append("=== TODAY / TOMORROW'S INTERVIEWS ===")
        for iv in upcoming:
            dt_str = iv.scheduled_at.strftime("%a %d %b, %I:%M %p IST") if iv.scheduled_at else "TBD"
            lines.append(f"• Interview scheduled: {dt_str} (Status: {iv.status.value})")
        lines.append("")

    if follow_up_due:
        lines.append(f"=== FOLLOW-UP REQUIRED ({len(follow_up_due)} candidates) ===")
        lines.append("These candidates were contacted 3+ days ago with no response:")
        for e in follow_up_due[:5]:
            lines.append(f"• Candidate ID {e.candidate_id} (Job #{e.job_id})")
        if len(follow_up_due) > 5:
            lines.append(f"  ... and {len(follow_up_due)-5} more")
        lines.append("")

    if shortlisted_count > 0:
        lines.append(f"ACTION: {shortlisted_count} candidate(s) are shortlisted and waiting for outreach.")
        lines.append("Run outreach from the dashboard: https://kgirdharlal-recruitment.vercel.app/ui/")
        lines.append("")

    lines.extend([
        "Dashboard: https://kgirdharlal-recruitment.vercel.app/ui/",
        "",
        "— AI HR Agent | K. Girdharlal International",
    ])

    body = "\n".join(lines)

    # Try to enhance with Claude AI
    from app.config import get_settings
    settings = get_settings()
    if settings.anthropic_api_key and (upcoming or follow_up_due or shortlisted_count > 0):
        try:
            enhanced = await _enhance_digest_with_ai(body, settings)
            if enhanced:
                body = enhanced
        except Exception as exc:
            logger.warning("AI digest enhancement failed: %s", exc)

    subject = f"HR Digest — {today_ist.strftime('%d %b %Y')} | {len(upcoming)} Interview(s) | {shortlisted_count} Awaiting Outreach"
    return subject, body


async def _enhance_digest_with_ai(plain_text: str, settings) -> str:
    """Use Claude to write a cleaner, more actionable digest."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=settings.claude_model,
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": (
                "Rewrite this HR pipeline digest as a clean, actionable morning briefing "
                "for Kirti Chand, HR Manager at K. Girdharlal International. "
                "Keep it concise — 150-200 words. Highlight the most urgent action.\n\n"
                + plain_text
            ),
        }],
    )
    return msg.content[0].text.strip()
