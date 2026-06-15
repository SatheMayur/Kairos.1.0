"""Apify-powered adapters for LinkedIn and Naukri.

Uses two Apify actors:
  - get-leads/linkedin-scraper   (search_profiles mode — no cookies)
  - makework36/naukri-scraper    (job listings → extract company/skills data)

Both run via the synchronous ApifyClient wrapped in asyncio.to_thread()
so they don't block FastAPI's event loop.

Required env var: APIFY_API_TOKEN
"""
import asyncio
import re
from typing import Optional

from app.adapters.base import BasePortalAdapter, RawCandidate
from app.models.candidate import CandidateSource
from app.utils.logging import get_logger

logger = get_logger(__name__)

LINKEDIN_ACTOR = "get-leads/linkedin-scraper"
NAUKRI_ACTOR   = "makework36/naukri-scraper"

# LinkedIn requires full city+state+country for precise geo-filtering.
# Bare city names like "Surat" match too broadly across India.
_LINKEDIN_LOCATION_MAP = {
    "surat":       "Surat, Gujarat, India",
    "ahmedabad":   "Ahmedabad, Gujarat, India",
    "vadodara":    "Vadodara, Gujarat, India",
    "rajkot":      "Rajkot, Gujarat, India",
    "mumbai":      "Mumbai, Maharashtra, India",
    "pune":        "Pune, Maharashtra, India",
    "bangalore":   "Bengaluru, Karnataka, India",
    "bengaluru":   "Bengaluru, Karnataka, India",
    "delhi":       "Delhi, India",
    "hyderabad":   "Hyderabad, Telangana, India",
    "chennai":     "Chennai, Tamil Nadu, India",
    "kolkata":     "Kolkata, West Bengal, India",
}


def _linkedin_location(job_location: Optional[str]) -> str:
    """Normalize job location to LinkedIn's expected city+state+country format."""
    if not job_location:
        return "Surat, Gujarat, India"
    lower = job_location.lower()
    for city_key, linkedin_fmt in _LINKEDIN_LOCATION_MAP.items():
        if city_key in lower:
            return linkedin_fmt
    # Unknown city — append India if not already present
    return job_location if "india" in lower else f"{job_location}, India"


def _run_actor_sync(api_token: str, actor_id: str, run_input: dict) -> list[dict]:
    """Blocking call — always run via asyncio.to_thread()."""
    from apify_client import ApifyClient
    client = ApifyClient(api_token)
    logger.info("Apify: starting actor %s", actor_id)
    run = client.actor(actor_id).call(run_input=run_input, timeout_secs=120)
    if not run:
        logger.error("Apify: actor %s returned no run object", actor_id)
        return []
    dataset_id = run.get("defaultDatasetId")
    items = list(client.dataset(dataset_id).iterate_items())
    logger.info("Apify: actor %s → %d items", actor_id, len(items))
    return items


def _parse_experience(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(text))
    return float(m.group(1)) if m else None


def _parse_salary_inr(text: Optional[str]) -> Optional[float]:
    """Convert Naukri salary strings like '3-5 Lacs PA' → monthly float."""
    if not text:
        return None
    text = str(text).replace(",", "").lower()
    m = re.search(r"([\d.]+)", text)
    if not m:
        return None
    val = float(m.group(1))
    if "lac" in text or "lpa" in text:
        return round(val * 100_000 / 12, 0)  # annual LPA → monthly ₹
    return val


class ApifyLinkedInAdapter(BasePortalAdapter):
    """Searches LinkedIn for candidate profiles via Apify get-leads/linkedin-scraper."""

    def __init__(self, api_token: str):
        self._token = api_token

    @property
    def source(self) -> CandidateSource:
        return CandidateSource.LINKEDIN

    async def search(
        self,
        keywords: list[str],
        location: Optional[str] = None,
        experience_min: Optional[float] = None,
        experience_max: Optional[float] = None,
        limit: int = 25,
    ) -> list[RawCandidate]:
        query = " ".join(keywords[:3])  # LinkedIn search works best with 2-3 terms
        run_input = {
            "mode": "search_profiles",
            "searchQuery": query,
            "location": _linkedin_location(location),
            "maxResults": min(limit, 50),
            "discoverEmails": True,
        }
        try:
            items = await asyncio.to_thread(
                _run_actor_sync, self._token, LINKEDIN_ACTOR, run_input
            )
        except Exception as exc:
            logger.error("ApifyLinkedIn error: %s", exc)
            try:
                from app.utils.error_log import log_error
                await log_error(
                    message=f"LinkedIn sourcing failed for '{query}': {exc}",
                    source="sourcing:apify_linkedin", exc=exc,
                )
            except Exception:
                pass
            return []

        if not items:
            try:
                from app.utils.error_log import log_error
                await log_error(
                    message=f"LinkedIn sourcing returned 0 profiles for '{query}' in "
                            f"{run_input['location']}",
                    source="sourcing:apify_linkedin", level="WARNING",
                )
            except Exception:
                pass

        return [self._to_raw(item) for item in items if item.get("name")]

    def _to_raw(self, item: dict) -> RawCandidate:
        skills = []
        if item.get("skills"):
            skills = [s.get("name", "") for s in item["skills"] if s.get("name")]
        elif item.get("headline"):
            # Extract skills from headline as fallback
            skills = [w.strip() for w in re.split(r"[|,·]", item["headline"]) if len(w.strip()) > 2][:6]

        exp_years = None
        experience = item.get("experience") or []
        if experience and isinstance(experience, list):
            # Sum up all experience durations
            total_months = 0
            for exp in experience:
                duration = exp.get("duration") or ""
                m = re.search(r"(\d+)\s*yr", duration)
                if m:
                    total_months += int(m.group(1)) * 12
                m2 = re.search(r"(\d+)\s*mo", duration)
                if m2:
                    total_months += int(m2.group(1))
            if total_months:
                exp_years = round(total_months / 12, 1)

        current_exp = experience[0] if experience else {}

        return RawCandidate(
            name=item.get("name", "Unknown"),
            source=CandidateSource.LINKEDIN,
            email=item.get("email"),
            skills=skills,
            experience_years=exp_years,
            location=item.get("location"),
            current_employer=current_exp.get("company") if isinstance(current_exp, dict) else None,
            current_role=current_exp.get("title") if isinstance(current_exp, dict) else item.get("headline", ""),
            education=item.get("education", [{}])[0].get("school") if item.get("education") else None,
            source_ref=item.get("url") or item.get("linkedinUrl"),
            raw_profile=str(item),
        )

    async def health_check(self) -> bool:
        return bool(self._token)


class ApifyNaukriAdapter(BasePortalAdapter):
    """Scrapes Naukri job listings to surface active candidates via Apify."""

    def __init__(self, api_token: str):
        self._token = api_token

    @property
    def source(self) -> CandidateSource:
        return CandidateSource.NAUKRI

    async def search(
        self,
        keywords: list[str],
        location: Optional[str] = None,
        experience_min: Optional[float] = None,
        experience_max: Optional[float] = None,
        limit: int = 30,
    ) -> list[RawCandidate]:
        keyword = " ".join(keywords[:2])
        run_input = {
            "keyword": keyword,
            "location": location or "Surat",
            "experience": f"{int(experience_min or 0)}-{int(experience_max or 3)}",
            "maxResults": min(limit, 50),
        }
        try:
            items = await asyncio.to_thread(
                _run_actor_sync, self._token, NAUKRI_ACTOR, run_input
            )
        except Exception as exc:
            logger.error("ApifyNaukri error: %s", exc)
            return []

        # Naukri returns job listings; extract candidate-like signals from applicants
        # when visible, or at minimum log companies actively hiring for this role
        results = []
        for item in items:
            raw = self._to_raw(item)
            if raw:
                results.append(raw)
        return results

    def _to_raw(self, item: dict) -> Optional[RawCandidate]:
        # Naukri scraper returns job listings — we treat each as a market signal
        # and extract candidate-matching info from the role details
        title = item.get("jobTitle") or item.get("title") or ""
        company = item.get("companyName") or item.get("company") or ""
        if not title:
            return None

        skills_raw = item.get("skills") or item.get("keySkills") or []
        if isinstance(skills_raw, str):
            skills_raw = [s.strip() for s in skills_raw.split(",")]

        return RawCandidate(
            name=f"{company} ({title})",  # job listing — flagged as market data
            source=CandidateSource.NAUKRI,
            skills=skills_raw[:10],
            experience_years=_parse_experience(
                item.get("experienceRequired") or item.get("experience")
            ),
            expected_salary=_parse_salary_inr(
                item.get("salary") or item.get("salaryRange")
            ),
            location=item.get("location") or item.get("jobLocation"),
            current_role=title,
            current_employer=company,
            source_ref=item.get("jobUrl") or item.get("url"),
            raw_profile=str(item),
        )

    async def health_check(self) -> bool:
        return bool(self._token)
