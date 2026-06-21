"""Unified LLM helper — one JSON-returning call that works with Gemini or Claude.

Picks the provider by which key is configured (Gemini preferred when both set).
Returns parsed JSON, or None when no provider is configured / the call fails —
callers then fall back to deterministic rule-based logic.

Gemini is called over its REST API with httpx (already a dependency), so no
extra package is needed.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Runtime overrides loaded from app_settings (so an Anthropic key / provider choice
# pasted in the browser takes effect with no redeploy). Cached per process; the
# save endpoint calls invalidate_runtime() so instances pick it up.
_RUNTIME: dict = {"loaded": False, "anthropic_key": "", "provider": ""}


async def ensure_runtime() -> None:
    if _RUNTIME["loaded"]:
        return
    try:
        from app.database import AsyncSessionLocal
        from app.services.app_settings import get_setting
        async with AsyncSessionLocal() as db:
            _RUNTIME["anthropic_key"] = (await get_setting(db, "anthropic_api_key")) or ""
            _RUNTIME["provider"] = (await get_setting(db, "ai_provider")) or ""
    except Exception:
        pass
    _RUNTIME["loaded"] = True


def invalidate_runtime() -> None:
    _RUNTIME["loaded"] = False


def _eff_anthropic_key(s) -> str:
    return _RUNTIME.get("anthropic_key") or s.anthropic_api_key


def _eff_provider_pref(s) -> str:
    return (_RUNTIME.get("provider") or getattr(s, "ai_provider", "") or "auto").lower()


def llm_provider() -> str:
    """Which provider to use. 'auto' prefers Claude (Anthropic) when its key is set,
    else Gemini. An explicit ai_provider ('claude'/'gemini') forces the choice."""
    s = get_settings()
    pref = _eff_provider_pref(s)
    akey = _eff_anthropic_key(s)
    gkey = s.gemini_api_key
    if pref == "claude" and akey:
        return "claude"
    if pref == "gemini" and gkey:
        return "gemini"
    # auto: Anthropic-first (owner's preference), then Gemini.
    if akey:
        return "claude"
    if gkey:
        return "gemini"
    return "none"


def llm_model() -> str | None:
    s = get_settings()
    return s.claude_model if llm_provider() == "claude" else (
        s.gemini_model if llm_provider() == "gemini" else None)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if "```" in text:
        for part in text.split("```"):
            p = part[4:] if part.lstrip().startswith("json") else part
            p = p.strip()
            if p.startswith("{"):
                text = p
                break
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    return json.loads(text)


async def _call_gemini(prompt: str, max_tokens: int, s) -> str:
    model = s.gemini_model or "gemini-2.5-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={s.gemini_api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.4,
            "responseMimeType": "application/json",
            # Disable "thinking" so the token budget goes to the answer (Gemini 2.5
            # otherwise spends tokens thinking and can truncate the JSON).
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


async def _call_claude(prompt: str, max_tokens: int, s) -> str:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=_eff_anthropic_key(s))
    msg = await client.messages.create(
        model=s.claude_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


async def llm_json(prompt: str, max_tokens: int = 600) -> dict | None:
    """Send a prompt, return parsed JSON. None if no provider or on failure."""
    await ensure_runtime()
    s = get_settings()
    provider = llm_provider()
    if provider == "none":
        return None
    try:
        if provider == "gemini":
            text = await _call_gemini(prompt, max_tokens, s)
        else:
            text = await _call_claude(prompt, max_tokens, s)
        return _extract_json(text)
    except Exception as exc:
        logger.warning("LLM (%s) call failed: %s", provider, exc)
        return None
