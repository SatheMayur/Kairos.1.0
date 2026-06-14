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


_OBJECTION_MARKERS = (
    "didn't apply", "didnt apply", "did not apply", "never applied", "not apply",
    "wrong number", "wrong person", "not me", "who is this", "how did you get",
    "by mistake", "not mayur", "don't know", "dont know", "not looking",
)


def _recruiter_fallback(candidate, job, collected: dict, intent: str,
                        salary_info: str, new_text: str) -> tuple[str, str, dict, bool]:
    """Rule-based recruiter behaviour (no API key): screen BEFORE scheduling.

    Returns (reply, action, collected, needs_human). action ∈ ask_info | schedule
    | close | answer. Rules cannot truly understand free text, so anything that
    looks like an objection/confusion is handed to a human instead of forced
    down the scheduling path — and the bot never repeats the same line.
    """
    name = (candidate.name or "").strip()
    first = name.split()[0] if name else "there"
    title = job.title
    text = (new_text or "").lower()

    if intent in ("NOT_INTERESTED", "WITHDRAWAL"):
        return (
            f"No problem at all, {first} — thank you for letting me know. "
            f"I'll keep your profile on file and reach out if a better-suited role comes up. "
            f"Wishing you all the best! 🙏",
            "close", collected, False,
        )

    # Objection / wrong-person / confusion — rules can't reason about these, so
    # escalate to a human rather than barrelling on to scheduling.
    if any(m in text for m in _OBJECTION_MARKERS):
        return (
            f"Apologies for any confusion, {first} 🙏 — let me have someone from our HR team "
            f"look into this and get back to you personally. Thank you for flagging it.",
            "answer", collected, True,
        )

    asked = bool(collected.get("_asked"))
    asks = int(collected.get("_asks", 0))
    have_core = bool(collected.get("expected_ctc") or collected.get("current_ctc")) \
        and bool(collected.get("notice_period"))
    gave_info = bool(re.search(r"\d", text)) or any(
        w in text for w in ("lpa", "lakh", "ctc", "notice", "immediate", "month",
                            "surat", "relocat", "yes", "sure", "ok", "ready", "interested")
    )

    screening = (
        f"To take this forward for the *{title}* role, could you share a few quick details:\n"
        f"• Current CTC\n• Expected CTC\n• Notice period\n• Current location\n\n"
        f"This helps me line up the right next step for you. 😊"
    )

    if intent == "SALARY_QUERY" and not asked:
        collected["_asked"] = True
        collected["_asks"] = asks + 1
        return (
            f"Happy to help, {first}! 💰 For the *{title}* role, the salary is: {salary_info}. "
            f"The final offer depends on your experience and how the interview goes.\n\n{screening}",
            "ask_info", collected, False,
        )

    if not asked:
        collected["_asked"] = True
        collected["_asks"] = asks + 1
        return (
            f"Hi {first}, wonderful to hear you're interested! 🎉\n\n{screening}",
            "ask_info", collected, False,
        )

    # Already screened once. Only schedule if they actually gave info / agreed.
    if have_core or gave_info:
        return (
            f"Thank you, {first} — that's really helpful! 🙌 Based on this you look like a "
            f"strong fit for the *{title}* role, so let's set up a short interview.",
            "schedule", collected, False,
        )

    # They replied but didn't give details and it's not an objection. Ask once
    # more; if they still don't, hand to a human rather than repeat forever.
    if asks >= 2:
        return (
            f"No problem, {first} — let me connect you with our HR team who can help you "
            f"directly from here. 🙏",
            "answer", collected, True,
        )
    collected["_asks"] = asks + 1
    return (
        f"No worries, {first}! Whenever you're ready, just share your current & expected CTC, "
        f"notice period and current location and I'll take it forward. 😊",
        "ask_info", collected, False,
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
    """Decide intent + craft a context-aware reply, updating collected facts.

    Uses whichever AI provider is configured (Gemini or Claude). With no provider,
    or if the AI call fails, falls back to the rule-based recruiter screening flow.
    """
    from app.services.llm import llm_provider, llm_json

    collected = dict(collected or {})
    # Always run cheap extraction so facts accumulate even on the fallback path.
    collected.update(_extract_fields(new_text))

    name = (candidate.name or "").strip()
    first = name.split()[0] if name else "there"

    async def _fallback() -> dict:
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
        reply, action, coll, needs_human = _recruiter_fallback(
            candidate, job, collected, intent, salary_info, new_text
        )
        return {"intent": intent, "reply": reply, "action": action,
                "collected": coll, "needs_human": needs_human}

    # No AI provider configured → rule-based recruiter screening
    if llm_provider() == "none":
        return await _fallback()

    # ── Reasoning path (Gemini or Claude) ─────────────────────────────────────
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
5. If the message is confusing, emotional, off-topic, a complaint, says they did NOT apply,
   or is something you shouldn't answer — set needs_human true, choose action "answer", and
   reply gently that a colleague will follow up. Never push scheduling in that case.

Choose an action:
- "ask_info"  : still missing screening details → ask for them (most common early on)
- "schedule"  : you have the screening details → encourage the interview (slots are added automatically)
- "answer"    : answering a question, handling confusion/objection, or general chat — not ready to schedule
- "close"     : they declined or withdrew

What we already know (do NOT ask again): {json.dumps({k: v for k, v in collected.items() if not k.startswith('_')}) or '(nothing yet)'}

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

    result = await llm_json(prompt, max_tokens=600)
    if not result or "reply" not in result:
        return await _fallback()

    merged = dict(collected)
    for k, v in (result.get("collected") or {}).items():
        if k in COLLECT_KEYS and v:
            merged[k] = v

    action = result.get("action")
    if action not in ("ask_info", "schedule", "answer", "close"):
        it = result.get("intent", "GENERAL")
        action = "close" if it in ("NOT_INTERESTED", "WITHDRAWAL") else "answer"

    logger.info("Conversation agent (%s): candidate=%s intent=%s action=%s",
                llm_provider(), candidate.name, result.get("intent"), action)
    return {
        "intent": result.get("intent", "GENERAL"),
        "reply": result.get("reply", ""),
        "action": action,
        "collected": merged,
        "needs_human": bool(result.get("needs_human", False)),
    }
