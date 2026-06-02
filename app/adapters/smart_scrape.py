"""Smart URL scraper — ScrapeGraphAI-style extraction using httpx + Claude.

Works on Vercel serverless (no playwright, no browser).
Pipeline:
  1. Fetch URL → raw HTML via httpx
  2. Strip tags → readable text
  3. Claude extracts structured candidate JSON
  4. Returns RawCandidate

Use cases:
  - Enrich CAD Crowd / portfolio profiles (source_ref URLs)
  - Import any candidate profile page by URL
  - Scrape public job-seeker pages on WorkIndia, Apna, Shine etc.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from app.adapters.base import BasePortalAdapter, RawCandidate
from app.models.candidate import CandidateSource

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_EXTRACT_PROMPT = """Extract structured candidate information from this web page text.

Return ONLY valid JSON with these fields (use null if not found):
{{
  "name": "<full name>",
  "email": "<email or null>",
  "phone": "<phone digits only or null>",
  "current_role": "<job title / designation>",
  "current_employer": "<company name or null>",
  "experience_years": <number or null>,
  "expected_salary": <monthly INR number or null>,
  "current_salary": <monthly INR number or null>,
  "location": "<city, state or null>",
  "education": "<highest degree or null>",
  "skills": ["skill1", "skill2", ...],
  "notice_period_days": <number or null>,
  "profile_summary": "<2-3 sentence summary of candidate>"
}}

Page URL: {url}
Page content:
{content}"""


def _strip_html(html: str) -> str:
    """Remove tags, scripts, styles. Keep readable text."""
    # Remove script/style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]  # Claude context limit safety — 8k chars is plenty for a profile


async def fetch_and_extract(url: str, source: CandidateSource = CandidateSource.MANUAL) -> Optional[RawCandidate]:
    """Fetch a URL and extract candidate data using Claude. Returns None on failure."""
    from app.config import get_settings
    settings = get_settings()

    # Step 1: fetch HTML
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.warning("SmartScrape fetch failed for %s: %s", url, exc)
        return None

    page_text = _strip_html(html)
    if len(page_text) < 100:
        logger.warning("SmartScrape: page too short for %s (%d chars)", url, len(page_text))
        return None

    # Step 2: Claude extraction
    if not settings.anthropic_api_key:
        logger.warning("SmartScrape: ANTHROPIC_API_KEY not set — cannot extract from %s", url)
        return None

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        msg = await client.messages.create(
            model=settings.claude_model,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": _EXTRACT_PROMPT.format(url=url, content=page_text),
            }],
        )
        raw_text = msg.content[0].text.strip()
        # Strip markdown code fences if present
        raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.warning("SmartScrape: Claude returned non-JSON for %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.error("SmartScrape: Claude extraction failed for %s: %s", url, exc)
        return None

    name = (data.get("name") or "").strip()
    if not name or name.lower() in ("unknown", "n/a", "null"):
        logger.warning("SmartScrape: no valid name extracted from %s", url)
        return None

    # Step 3: Build RawCandidate
    skills = data.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]

    return RawCandidate(
        name=name,
        source=source,
        email=data.get("email"),
        phone=_clean_phone(data.get("phone")),
        current_role=data.get("current_role"),
        current_employer=data.get("current_employer"),
        experience_years=_safe_float(data.get("experience_years")),
        expected_salary=_safe_float(data.get("expected_salary")),
        current_salary=_safe_float(data.get("current_salary")),
        location=data.get("location"),
        education=data.get("education"),
        skills=skills[:20],
        notice_period_days=_safe_int(data.get("notice_period_days")),
        raw_profile=data.get("profile_summary", "")[:500],
        source_ref=url,
    )


def _clean_phone(phone) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) >= 10:
        return digits[-10:]
    return None


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


class SmartScrapeAdapter(BasePortalAdapter):
    """Portal adapter that scrapes any URL using Claude for extraction.

    Used for portals without official APIs: CAD Crowd, WorkIndia, Apna,
    Shine, Internshala, and any candidate portfolio / personal site.
    """

    def __init__(self, source: CandidateSource = CandidateSource.MANUAL):
        self._source = source

    @property
    def source(self) -> CandidateSource:
        return self._source

    async def search(
        self,
        keywords: list[str],
        location: Optional[str] = None,
        experience_min: Optional[float] = None,
        experience_max: Optional[float] = None,
        limit: int = 20,
    ) -> list[RawCandidate]:
        """Not implemented — SmartScrapeAdapter works via direct URL import, not keyword search."""
        return []

    async def enrich_from_url(self, url: str) -> Optional[RawCandidate]:
        """Extract a candidate profile from a direct URL."""
        return await fetch_and_extract(url, self._source)

    async def health_check(self) -> bool:
        from app.config import get_settings
        return bool(get_settings().anthropic_api_key)
