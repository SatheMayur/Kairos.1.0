"""Claude AI-powered candidate scoring and personalized outreach generation.

Falls back to rule-based scoring when ANTHROPIC_API_KEY is not set.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.models.candidate import Candidate
from app.models.job import Job

logger = logging.getLogger(__name__)


async def ai_score_candidate(
    candidate: Candidate,
    job: Job,
) -> dict:
    """Score a candidate against a job using Claude AI.

    Returns dict with keys:
      score (0-10), decision, strengths, concerns, reasoning, personalized_opener
    Falls back gracefully to rule-based score if API key not set.
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.anthropic_api_key:
        return {}  # caller uses rule-based fallback

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        prompt = f"""You are an expert HR screener for K. Girdharlal International, a 40+ year diamond manufacturing company in Surat, India.

Evaluate this candidate for the job opening and return a JSON response.

JOB:
Title: {job.title}
Company: {job.company or 'K. Girdharlal International'}
Location: {job.location or 'Surat, Gujarat'}
Experience Required: {job.experience_min or 0}–{job.experience_max or 5} years
Skills Required: {', '.join(job.skills or [])}
Description: {(job.description or '')[:400]}

CANDIDATE:
Name: {candidate.name}
Current Role: {candidate.current_role or 'Unknown'}
Current Employer: {candidate.current_employer or 'Unknown'}
Experience: {candidate.experience_years or 'Unknown'} years
Location: {candidate.location or 'Unknown'}
Skills: {', '.join(candidate.skills or [])}
Expected Salary: ₹{int(candidate.expected_salary or 0):,}/month
Education: {candidate.education or 'Unknown'}

Score this candidate on a scale of 0-10 and decide:
- AUTO_SHORTLIST: score >= 7, strong match, worth contacting immediately
- MANUAL_REVIEW: score 5-6.9, possible match but needs review
- REJECT: score < 5, not a fit

Also write a 1-2 sentence personalized outreach opener referencing something specific about their profile.

RESPOND WITH ONLY VALID JSON:
{{
  "score": <float 0-10>,
  "decision": "<AUTO_SHORTLIST|MANUAL_REVIEW|REJECT>",
  "strengths": ["<strength1>", "<strength2>"],
  "concerns": ["<concern1>"],
  "reasoning": "<2-3 sentence explanation>",
  "personalized_opener": "<1-2 sentences referencing their specific background>"
}}"""

        msg = await client.messages.create(
            model=settings.claude_model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # Extract JSON from response
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        logger.info("AI score for %s: %.1f (%s)", candidate.name, result.get("score", 0), result.get("decision"))
        return result

    except Exception as exc:
        logger.warning("AI scoring failed for %s: %s — using rule-based fallback", candidate.name, exc)
        return {}


async def ai_generate_outreach(
    candidate: Candidate,
    job: Job,
    channel: str = "EMAIL",
) -> tuple[str, str]:
    """Generate a personalized outreach subject + body using Claude AI.

    Returns (subject, body). Falls back to default template if API key not set.
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.anthropic_api_key:
        return "", ""  # caller uses default template

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        channel_note = "email (professional tone)" if channel == "EMAIL" else "WhatsApp (conversational, concise, under 200 words)"

        prompt = f"""Write a personalized {channel_note} outreach to this candidate for a job opening.

JOB: {job.title} at {job.company or 'K. Girdharlal International'}, {job.location or 'Surat'}
Salary: No bar for right candidate | Experience: {job.experience_min or 1}–{job.experience_max or 3} yrs

CANDIDATE:
Name: {candidate.name}
Current Role: {candidate.current_role or ''} at {candidate.current_employer or ''}
Skills: {', '.join((candidate.skills or [])[:5])}
Experience: {candidate.experience_years or ''} years
Location: {candidate.location or ''}

Rules:
- Reference 1-2 specific aspects of their background
- Ask 3-4 screening questions (CTC, notice period, location, experience)
- Sign off as: Kirti Chand | HR Manager | K. Girdharlal International | Ph: 9033410606
- For email: include subject line

Return JSON only:
{{"subject": "<email subject or empty for WhatsApp>", "body": "<full message>"}}"""

        msg = await client.messages.create(
            model=settings.claude_model,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        return result.get("subject", ""), result.get("body", "")

    except Exception as exc:
        logger.warning("AI outreach generation failed for %s: %s", candidate.name, exc)
        return "", ""
