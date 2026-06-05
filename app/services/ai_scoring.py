"""Claude AI-powered candidate scoring and personalized outreach generation.

Falls back to rule-based scoring when ANTHROPIC_API_KEY is not set.
"""
from __future__ import annotations

import json
import logging
import re
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


async def ai_classify_reply(
    reply_text: str,
    candidate_name: str,
    job_title: str,
    job_company: str,
    job_location: str,
    job_salary_info: str,
) -> dict:
    """Classify a WhatsApp reply intent using Claude AI.

    Returns dict with keys:
      intent: INTERESTED | NOT_INTERESTED | SALARY_QUERY | SCHEDULE_QUERY |
              MORE_INFO | WITHDRAWAL | GENERAL
      confidence: 0.0–1.0
      auto_response: ready-to-send WhatsApp reply (plain language, under 120 words)
      needs_human: True if Kirti should review manually
    Falls back to keyword-based classification if API key not set.
    """
    from app.config import get_settings
    settings = get_settings()

    # Fast keyword fallback (no API key needed)
    text_lower = reply_text.lower().strip()

    # Explicit negatives — checked FIRST so "not interested" beats the "interested" token
    strong_negatives = {"not interested", "not looking", "no thanks", "withdraw",
                        "nahi", "nope", "cancel", "please remove", "don't contact"}
    positive_words = {"yes", "haan", "interested", "ok", "okay", "sure", "proceed", "agree",
                      "ready", "available", "confirm", "want", "join"}
    lone_negatives = {"no", "stop", "quit", "remove"}
    salary_words = {"salary", "ctc", "pay", "package", "lakh", "lpa", "compensation",
                    "stipend", "hike", "money"}
    schedule_words = {"when", "time", "date", "slot", "schedule", "interview", "meet",
                      "call", "reschedule", "timing"}

    def _kw(words: set) -> bool:
        # Whole-word match for single words (so "no" doesn't match "notice"/"know"),
        # substring match for multi-word phrases ("not interested").
        for w in words:
            if " " in w or "'" in w:
                if w in text_lower:
                    return True
            elif re.search(r"\b" + re.escape(w) + r"\b", text_lower):
                return True
        return False

    keyword_intent = None
    if _kw(strong_negatives):
        keyword_intent = "NOT_INTERESTED"
    elif _kw(positive_words):
        keyword_intent = "INTERESTED"
    elif _kw(lone_negatives):
        keyword_intent = "NOT_INTERESTED"
    elif _kw(salary_words):
        keyword_intent = "SALARY_QUERY"
    elif _kw(schedule_words):
        keyword_intent = "SCHEDULE_QUERY"

    if not settings.anthropic_api_key:
        # Return keyword-based result
        intent = keyword_intent or "GENERAL"
        return _build_auto_response(intent, candidate_name, job_title, job_company, job_location, job_salary_info)

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        prompt = f"""You are classifying a WhatsApp reply from a job candidate.

CONTEXT:
Candidate: {candidate_name}
Job: {job_title} at {job_company}, {job_location}
Salary: {job_salary_info}

CANDIDATE'S REPLY:
"{reply_text}"

Classify the intent and return ONLY valid JSON:
{{
  "intent": "<one of: INTERESTED | NOT_INTERESTED | SALARY_QUERY | SCHEDULE_QUERY | MORE_INFO | WITHDRAWAL | GENERAL>",
  "confidence": <float 0.0-1.0>,
  "needs_human": <true if unclear/complex/emotional, else false>
}}

Intent guide:
- INTERESTED: candidate wants to proceed, positive response, yes/interested/available
- NOT_INTERESTED: clear decline, not available, not looking
- SALARY_QUERY: asking about CTC, pay, package, compensation
- SCHEDULE_QUERY: asking about interview timing, date, reschedule
- MORE_INFO: asking about role details, responsibilities, company, location
- WITHDRAWAL: explicitly withdrawing application
- GENERAL: anything else (greeting, thanks, unclear, mixed signals)"""

        msg = await client.messages.create(
            model=settings.claude_model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        intent = result.get("intent", keyword_intent or "GENERAL")
        confidence = result.get("confidence", 0.8)
        needs_human = result.get("needs_human", False)

        logger.info("AI classified reply from %s as %s (%.0f%%)", candidate_name, intent, confidence * 100)

        response_data = _build_auto_response(intent, candidate_name, job_title, job_company, job_location, job_salary_info)
        response_data["confidence"] = confidence
        response_data["needs_human"] = needs_human
        return response_data

    except Exception as exc:
        logger.warning("AI reply classification failed: %s — using keyword fallback", exc)
        intent = keyword_intent or "GENERAL"
        return _build_auto_response(intent, candidate_name, job_title, job_company, job_location, job_salary_info)


async def ai_plan_day(situation: dict) -> dict:
    """Reason over today's recruitment state and return a prioritised plan.

    Returns dict: {manager_note: str, priorities: [{title, why, action}]}
    Returns {} when no API key or on failure — caller uses a rule-based fallback.
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.anthropic_api_key:
        return {}

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        prompt = f"""You are the recruitment manager for K. Girdharlal International, a diamond \
manufacturing company in Surat. Every morning you review the state of hiring and tell the \
owner, Kirti, what matters most today.

Kirti is NOT technical. Write in plain, warm, simple English. No jargon, no system terms.

Here is today's situation (numbers from the recruitment system):
{json.dumps(situation, indent=2)}

Decide what Kirti should focus on today. Think like a sharp hiring manager: interviews and \
candidates who already said yes matter more than a big pile of unreviewed applicants; people \
the company can't contact or possible fake/duplicate resumes are worth flagging.

Return ONLY valid JSON:
{{
  "manager_note": "<2-4 warm sentences summarising the day and your overall advice>",
  "priorities": [
    {{"title": "<short action title>", "why": "<one plain sentence why it matters>", "action": "<what to do / where to click>"}}
  ]
}}
List at most 5 priorities, most important first. If things are quiet, say so kindly."""

        msg = await client.messages.create(
            model=settings.claude_model,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        if "priorities" in result:
            logger.info("AI day-plan generated with %d priorities", len(result.get("priorities", [])))
            return result
        return {}
    except Exception as exc:
        logger.warning("AI day-plan failed: %s — using rule-based fallback", exc)
        return {}


def _build_auto_response(
    intent: str,
    candidate_name: str,
    job_title: str,
    job_company: str,
    job_location: str,
    job_salary_info: str,
) -> dict:
    """Build a ready-to-send WhatsApp auto-response for a given intent."""
    first = candidate_name.split()[0]

    responses = {
        "INTERESTED": {
            "auto_response": (
                f"Hi {first}, that's great to hear! 😊\n\n"
                f"I'll send you interview slot options shortly for the *{job_title}* role. "
                f"Please keep an eye out for my next message.\n\n"
                f"— Kirti | K. Girdharlal International"
            ),
            "needs_human": False,
        },
        "NOT_INTERESTED": {
            "auto_response": (
                f"Hi {first}, no problem at all! Thank you for letting us know. "
                f"We'll keep you in mind for future opportunities that match your profile. "
                f"Wishing you all the best! 🙏"
            ),
            "needs_human": False,
        },
        "SALARY_QUERY": {
            "auto_response": (
                f"Hi {first}! Great question. 😊\n\n"
                f"For the *{job_title}* role at {job_company}, {job_location}:\n"
                f"💰 *Salary:* {job_salary_info}\n\n"
                f"We offer competitive compensation and the final package is decided based on your "
                f"experience and interview performance. Are you interested in proceeding?"
            ),
            "needs_human": False,
        },
        "SCHEDULE_QUERY": {
            "auto_response": (
                f"Hi {first}! I'll send you 3 available interview slots to choose from right away. "
                f"Please reply with *1*, *2*, or *3* to confirm your preferred time. 📅"
            ),
            "needs_human": False,
        },
        "MORE_INFO": {
            "auto_response": (
                f"Hi {first}! Happy to share more details about the *{job_title}* role. 😊\n\n"
                f"📍 Location: {job_location}\n"
                f"🏢 Company: {job_company}\n"
                f"💰 Salary: {job_salary_info}\n\n"
                f"This is a full-time position. Would you like to proceed with the interview process?"
            ),
            "needs_human": False,
        },
        "WITHDRAWAL": {
            "auto_response": (
                f"Hi {first}, we understand and respect your decision. "
                f"Thank you for your interest in {job_company}. "
                f"We'll close this application on our end. Wishing you great success ahead! 🙏"
            ),
            "needs_human": False,
        },
        "GENERAL": {
            "auto_response": (
                f"Hi {first}, thanks for your message! 😊\n\n"
                f"Are you interested in the *{job_title}* role at {job_company}?\n\n"
                f"Reply *YES* to proceed or *NO* if not interested."
            ),
            "needs_human": True,
        },
    }

    data = responses.get(intent, responses["GENERAL"])
    return {
        "intent": intent,
        "confidence": 0.7,
        "auto_response": data["auto_response"],
        "needs_human": data["needs_human"],
    }
