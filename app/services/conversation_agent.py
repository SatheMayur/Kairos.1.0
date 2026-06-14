"""Multi-turn WhatsApp Conversation Agent.

Reasons over the whole thread (not just the latest message): tracks what the
candidate has told us, answers their questions in context, and decides intent.
Falls back to the existing keyword classifier + light regex extraction when
ANTHROPIC_API_KEY is not set, so behaviour never regresses.

Returns: {intent, reply, collected, needs_human}
  intent ∈ INTERESTED | NOT_INTERESTED | SALARY_QUERY | SCHEDULE_QUERY |
           MORE_INFO | WITHDRAWAL | GENERAL
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

COLLECT_KEYS = ("expected_ctc", "current_ctc", "notice_period", "location", "availability")


def _extract_fields(text: str) -> dict:
    """Light heuristic slot-filling for the no-API-key fallback."""
    out: dict = {}
    t = text.lower()

    # Notice period: "30 days", "2 months", "immediate"
    if "immediate" in t or "immediately" in t:
        out["notice_period"] = "Immediate"
    else:
        m = re.search(r"(\d+)\s*(day|days|week|weeks|month|months)", t)
        if m and ("notice" in t or "join" in t or "available" in t or m.group(2).startswith(("month", "day"))):
            out["notice_period"] = f"{m.group(1)} {m.group(2)}"

    # CTC: "12 LPA", "8 lakh", "45k", "₹50000"
    m = re.search(r"(?:₹\s*)?(\d+(?:\.\d+)?)\s*(lpa|lakhs?|k|thousand)", t)
    if m and ("ctc" in t or "salary" in t or "lpa" in t or "lakh" in t or "expect" in t or "current" in t or "package" in t):
        val = f"{m.group(1)} {m.group(2).upper()}"
        if "current" in t:
            out["current_ctc"] = val
        else:
            out["expected_ctc"] = val

    return out


def _format_history(history: list, limit: int = 10) -> str:
    lines = []
    for h in (history or [])[-limit:]:
        who = "Candidate" if h.get("dir") == "in" else "Us"
        lines.append(f"{who}: {h.get('text','')}")
    return "\n".join(lines) if lines else "(no earlier messages)"


def _recruiter_fallback(candidate, job, collected: dict, intent: str,
                        salary_info: str) -> tuple[str, str, dict]:
    """Rule-based recruiter behaviour (no API key): screen BEFORE scheduling.

    Returns (reply, action, collected). action ∈ ask_info | schedule | close | answer.
    A real recruiter acknowledges interest, collects CTC / notice / location, and
    only then offers interview slots — it never jumps straight to 'pick a slot'.
    """
    name = (candidate.name or "").strip()
    first = name.split()[0] if name else "there"
    title = job.title

    if intent in ("NOT_INTERESTED", "WITHDRAWAL"):
        return (
            f"No problem at all, {first} — thank you for letting me know. "
            f"I'll keep your profile on file and reach out if a better-suited role comes up. "
            f"Wishing you all the best! 🙏",
            "close", collected,
        )

    asked = bool(collected.get("_asked"))
    have_core = bool(collected.get("expected_ctc") or collected.get("current_ctc")) \
        and bool(collected.get("notice_period"))

    screening = (
        f"To take this forward for the *{title}* role, could you share a few quick details:\n"
        f"• Current CTC\n• Expected CTC\n• Notice period\n• Current location\n\n"
        f"This helps me line up the right next step for you. 😊"
    )

    if intent == "SALARY_QUERY" and not asked:
        collected["_asked"] = True
        return (
            f"Happy to help, {first}! 💰 For the *{title}* role, the salary is: {salary_info}. "
            f"The final offer depends on your experience and how the interview goes.\n\n{screening}",
            "ask_info", collected,
        )

    if not asked and not have_core:
        collected["_asked"] = True
        return (
            f"Hi {first}, wonderful to hear you're interested! 🎉\n\n{screening}",
            "ask_info", collected,
        )

    # We've already screened (or the candidate volunteered the key details) →
    # acknowledge and move to scheduling. The webhook appends the actual slots.
    return (
        f"Thank you, {first} — that's really helpful! 🙌 Based on this you look like a "
        f"strong fit for the *{title}* role, so let's set up a short interview.",
        "schedule", collected,
    )


async def converse(
    *,
    candidate,
    job,
    history: list,
    collected: dict,
    new_text: str,
    salary_info: str,
) -> dict:
    """Decide intent + craft a context-aware reply, updating collected facts."""
    from app.config import get_settings
    settings = get_settings()

    collected = dict(collected or {})

    # Always run cheap extraction so facts accumulate even on the fallback path.
    collected.update(_extract_fields(new_text))

    # ── Fallback (no API key): keyword intent + recruiter screening flow ──────
    if not settings.anthropic_api_key:
        from app.services.ai_scoring import ai_classify_reply
        c = await ai_classify_reply(
            reply_text=new_text,
            candidate_name=candidate.name,
            job_title=job.title,
            job_company=job.company or "K. Girdharlal International",
            job_location=job.location or "Surat, Gujarat",
            job_salary_info=salary_info,
        )
        intent = c.get("intent", "GENERAL")
        reply, action, collected = _recruiter_fallback(
            candidate, job, collected, intent, salary_info
        )
        return {
            "intent": intent, "reply": reply, "action": action,
            "collected": collected, "needs_human": c.get("needs_human", False),
        }

    # ── Reasoning path: Claude over the full thread ───────────────────────────
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        first = candidate.name.split()[0]
        prompt = f"""You are the WhatsApp recruiting assistant for {job.company or 'K. Girdharlal International'}, \
a diamond manufacturer in Surat. You are chatting with a candidate, {candidate.name}, about the \
*{job.title}* role (location: {job.location or 'Surat'}; salary: {salary_info}).

You behave like a real HR recruiter — warm, professional, and you SCREEN before scheduling.

Your flow, in order:
1. Be warm, brief (WhatsApp style, under 110 words), human. Use their first name ({first}).
2. Answer any question they ask using the role facts above.
3. Before scheduling an interview, make sure you have these screening details:
   current CTC, expected CTC, notice period, current location. Ask for whatever is missing.
   Do NOT offer interview slots until you have them.
4. Once you have those details (or they're clearly already provided), move to scheduling.
5. If the message is confusing, emotional, a complaint, or something you shouldn't answer,
   set needs_human true and reply gently that a colleague will follow up.

Choose an action:
- "ask_info"  : still missing screening details → ask for them (most common early on)
- "schedule"  : you have the screening details → encourage the interview (slots are added automatically)
- "answer"    : just answering a question / general chit-chat, not ready to schedule
- "close"     : they declined or withdrew

What we already know (do NOT ask again): {json.dumps(collected) if collected else '(nothing yet)'}

Conversation so far:
{_format_history(history)}

Candidate's new message:
"{new_text}"

Return ONLY valid JSON:
{{
  "intent": "<INTERESTED|NOT_INTERESTED|SALARY_QUERY|SCHEDULE_QUERY|MORE_INFO|WITHDRAWAL|GENERAL>",
  "action": "<ask_info|schedule|answer|close>",
  "reply": "<your WhatsApp reply, plain text>",
  "collected": {{"expected_ctc": "", "current_ctc": "", "notice_period": "", "location": "", "availability": ""}},
  "needs_human": <true|false>
}}
In "collected", only include keys you are confident about (merge with what we know); omit the rest."""

        msg = await client.messages.create(
            model=settings.claude_model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        merged = dict(collected)
        for k, v in (result.get("collected") or {}).items():
            if k in COLLECT_KEYS and v:
                merged[k] = v

        action = result.get("action")
        if action not in ("ask_info", "schedule", "answer", "close"):
            # Derive a safe action if the model omitted it.
            it = result.get("intent", "GENERAL")
            action = "close" if it in ("NOT_INTERESTED", "WITHDRAWAL") else "answer"
        logger.info("Conversation agent: candidate=%s intent=%s action=%s",
                    candidate.name, result.get("intent"), action)
        return {
            "intent": result.get("intent", "GENERAL"),
            "reply": result.get("reply", ""),
            "action": action,
            "collected": merged,
            "needs_human": bool(result.get("needs_human", False)),
        }

    except Exception as exc:
        logger.warning("Conversation agent failed: %s — keyword fallback", exc)
        from app.services.ai_scoring import ai_classify_reply
        c = await ai_classify_reply(
            reply_text=new_text,
            candidate_name=candidate.name,
            job_title=job.title,
            job_company=job.company or "K. Girdharlal International",
            job_location=job.location or "Surat, Gujarat",
            job_salary_info=salary_info,
        )
        intent = c.get("intent", "GENERAL")
        reply, action, collected = _recruiter_fallback(
            candidate, job, collected, intent, salary_info
        )
        return {
            "intent": intent, "reply": reply, "action": action,
            "collected": collected, "needs_human": c.get("needs_human", False),
        }
