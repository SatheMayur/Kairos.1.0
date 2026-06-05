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

    # ── Fallback (no API key): reuse the existing keyword classifier ──────────
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
        return {
            "intent": c.get("intent", "GENERAL"),
            "reply": c.get("auto_response", ""),
            "collected": collected,
            "needs_human": c.get("needs_human", False),
        }

    # ── Reasoning path: Claude over the full thread ───────────────────────────
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        first = candidate.name.split()[0]
        prompt = f"""You are the WhatsApp recruiting assistant for {job.company or 'K. Girdharlal International'}, \
a diamond manufacturer in Surat. You are chatting with a candidate, {candidate.name}, about the \
*{job.title}* role (location: {job.location or 'Surat'}; salary: {salary_info}).

Your goals, in order:
1. Be warm, brief (WhatsApp style, under 110 words), and human. Use their first name ({first}).
2. Answer any question they ask using the role facts above.
3. Naturally collect anything still missing: expected CTC, current CTC, notice period, location, availability.
4. If they're keen and you have enough info, encourage them toward an interview (we'll send slots separately).
5. If the message is confusing, emotional, a complaint, or something you shouldn't answer, set needs_human true and reply gently that a team member will follow up.

What we already know (do NOT ask again): {json.dumps(collected) if collected else '(nothing yet)'}

Conversation so far:
{_format_history(history)}

Candidate's new message:
"{new_text}"

Return ONLY valid JSON:
{{
  "intent": "<INTERESTED|NOT_INTERESTED|SALARY_QUERY|SCHEDULE_QUERY|MORE_INFO|WITHDRAWAL|GENERAL>",
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

        logger.info("Conversation agent: candidate=%s intent=%s needs_human=%s",
                    candidate.name, result.get("intent"), result.get("needs_human"))
        return {
            "intent": result.get("intent", "GENERAL"),
            "reply": result.get("reply", ""),
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
        return {
            "intent": c.get("intent", "GENERAL"),
            "reply": c.get("auto_response", ""),
            "collected": collected,
            "needs_human": c.get("needs_human", False),
        }
