"""Apna (apnaHire) CSV adapter — parses an Apna employer applicant export.

Apna export layouts vary, so columns are matched by header name with broad
aliases. If Apna gives you an Excel file, open it and "Save As → CSV" first.

Not a live API adapter (Apna has no public employer API) — feed the CSV via the
import endpoint, exactly like Naukri/WorkIndia.
"""
from __future__ import annotations

import csv
import io
import logging
import re

from app.adapters.base import BasePortalAdapter, RawCandidate
from app.adapters.naukri import _find_col, _parse_salary, _parse_experience, _parse_notice
from app.models.candidate import CandidateSource

logger = logging.getLogger(__name__)

# Apna uses the literal string "Not Available" as a null placeholder in many columns.
_NA = {"not available", "na", "n/a", ""}


def _apna_experience(value: str):
    """Apna writes experience like '6yrs 7mos' / '5yrs ' / '11yrs 1mos'."""
    if not value:
        return None
    m = re.search(r"(\d+)\s*yrs?(?:\s*(\d+)\s*mos)?", value, re.I)
    if m:
        years = int(m.group(1)) + (int(m.group(2)) / 12 if m.group(2) else 0)
        return round(years, 1)
    return _parse_experience(value)

_NAME   = {"candidate name", "name", "full name", "applicant name", "candidate"}
_EMAIL  = {"email", "email id", "email address", "e-mail"}
_PHONE  = {"mobile number", "mobile", "phone", "phone number", "contact number",
           "contact", "whatsapp number", "whatsapp", "mobile no", "mobile no."}
_EXP    = {"experience", "total experience", "work experience",
           "years of experience", "exp", "experience (years)"}
_LOC    = {"location", "city", "current location", "preferred location",
           "candidate location", "candidate city", "candidate area"}
_SKILLS = {"skills", "key skills", "skill set", "sub department"}
_ROLE   = {"job title", "designation", "current designation", "current job title",
           "applied for", "role", "current role", "job role", "current job role"}
_EMP    = {"company", "current company", "current employer", "employer"}
_CURSAL = {"current salary", "current ctc", "current ctc (monthly)"}
_EXPSAL = {"expected salary", "expected ctc", "expected salary (monthly)", "expected salary (per month)"}
_NOTICE = {"notice period", "availability", "notice"}
_EDU    = {"education", "qualification", "highest qualification", "degree"}


class ApnaCSVAdapter(BasePortalAdapter):
    """Parses an Apna employer CSV export into RawCandidate records."""

    @property
    def source(self) -> CandidateSource:
        return CandidateSource.APNA

    async def search(self, keywords, location=None,
                     experience_min=None, experience_max=None, limit=50):
        return []  # no live API — data comes via CSV import

    def parse_csv(self, csv_text: str) -> list[RawCandidate]:
        reader = csv.reader(io.StringIO(csv_text.strip()))
        rows = list(reader)
        if not rows:
            return []

        headers = [h.strip() for h in rows[0]]
        col = {
            "name":   _find_col(headers, _NAME),
            "email":  _find_col(headers, _EMAIL),
            "phone":  _find_col(headers, _PHONE),
            "exp":    _find_col(headers, _EXP),
            "loc":    _find_col(headers, _LOC),
            "skills": _find_col(headers, _SKILLS),
            "role":   _find_col(headers, _ROLE),
            "emp":    _find_col(headers, _EMP),
            "cursal": _find_col(headers, _CURSAL),
            "expsal": _find_col(headers, _EXPSAL),
            "notice": _find_col(headers, _NOTICE),
            "edu":    _find_col(headers, _EDU),
        }

        def get(row: list[str], key: str) -> str:
            i = col[key]
            if i is None or i >= len(row):
                return ""
            val = row[i].strip()
            return "" if val.lower() in _NA else val  # treat "Not Available" as empty

        out: list[RawCandidate] = []
        for row in rows[1:]:
            if not row or not any(row):
                continue
            name = get(row, "name")
            if not name:
                continue
            skills = [s.strip() for s in get(row, "skills").replace(";", ",").split(",") if s.strip()]
            phone = get(row, "phone") or None
            out.append(
                RawCandidate(
                    name=name,
                    source=CandidateSource.APNA,
                    email=get(row, "email") or None,
                    phone=phone,
                    whatsapp=phone,
                    skills=skills,
                    experience_years=_apna_experience(get(row, "exp")),
                    current_salary=_parse_salary(get(row, "cursal")),
                    expected_salary=_parse_salary(get(row, "expsal")),
                    notice_period_days=_parse_notice(get(row, "notice")),
                    location=get(row, "loc") or None,
                    current_employer=get(row, "emp") or None,
                    current_role=get(row, "role") or None,
                    education=get(row, "edu") or None,
                    source_ref=f"apna:{get(row, 'email') or phone or name}",
                )
            )

        logger.info("ApnaCSVAdapter: parsed %d candidates", len(out))
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Live API adapter — searches Apna Hire's white-collar database in real time.
# Proxied server-side to avoid CORS.  Token = JWT from employer.apna.co
# localStorage.__token__
# ─────────────────────────────────────────────────────────────────────────────
import httpx
from typing import Optional as _Opt

APNA_SEARCH_URL = "https://production.apna.co/cerebro/api/v1/white-collar-search/ic"


class ApnaAdapter(BasePortalAdapter):
    """Searches Apna Hire's white-collar candidate database via live API."""

    def __init__(self, token: str, org_id: str = "2012727", workspace_id: str = ""):
        self.token = token
        self.org_id = org_id
        self.workspace_id = workspace_id

    @property
    def source(self) -> CandidateSource:
        return CandidateSource.APNA

    def _headers(self) -> dict:
        return {
            "Authorization": f"Token {self.token}",
            "orgId": self.org_id,
            "workspaceId": self.workspace_id,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        }

    def _body(self, keywords, location, experience_min, experience_max, page, size) -> dict:
        kw_entities = [
            {"label": kw, "value": kw, "type": "Title", "selected": True, "keyword": True, "mustHave": False}
            for kw in keywords
        ]
        cities = [{"label": location, "value": location}] if location else []
        exp: dict = {}
        if experience_min is not None:
            exp["minExperience"] = experience_min
        if experience_max is not None:
            exp["maxExperience"] = experience_max
        return {
            "baseQuery": {
                "mustKeywordsEntity": [], "keywordsEntity": kw_entities,
                "currentClusterCitiesV2": [], "currentClusterCities": cities,
                "states": [], "preferredStates": [], "preferredClusterCities": [],
                "requestType": "any", "daysSinceLastActivity": 180,
                "disclosedSalary": True, "preferRelocation": False, "candidate_phone_numbers": [],
            },
            "keywordsEntity": kw_entities, "mustKeywordsEntity": [], "excludeKeywords": [],
            "currentClusterCities": cities, "preferredClusterCities": [],
            "daysSinceLastActivity": 180, "industries": [], "requestType": "any",
            "page": page, "size": size, "disclosedSalary": True,
            "sortOrder": "DESC", "sortKey": "relevance",
            "customDepartments": None, "states": [], "preferredStates": [],
            "preferRelocation": False, "degreeSpecializations": [],
            "cityAreasFilter": {"cityAreas": [], "areaSphere": {"label": "5 km", "value": "5"}},
            "unlockedSince": None, "englishLevel": [], **exp,
        }

    async def search_raw(self, keywords, location=None, experience_min=None, experience_max=None, page=1, size=20) -> dict:
        body = self._body(keywords, location, experience_min, experience_max, page, size)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(APNA_SEARCH_URL, json=body, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def search(self, keywords, location=None, experience_min=None, experience_max=None, limit=20, page=1) -> list[RawCandidate]:
        data = await self.search_raw(keywords, location, experience_min, experience_max, page, limit)
        return [rc for rc in (_live_to_raw(c) for c in _extract_live(data)) if rc]


# ── Live search helpers ───────────────────────────────────────────────────────

def _extract_live(data: dict) -> list[dict]:
    # Real Apna response: {statusCode, data: {users: [...], count: N}, ...}
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, dict):
            for key in ("users", "candidates", "results", "items", "docs"):
                if isinstance(inner.get(key), list):
                    return inner[key]
        if isinstance(inner, list):
            return inner
        for key in ("candidates", "results", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return data if isinstance(data, list) else []


def _extract_total(data: dict) -> int:
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, dict):
            for key in ("count", "totalCount", "total", "totalResults"):
                if isinstance(inner.get(key), int):
                    return inner[key]
        for key in ("totalCount", "total", "count", "totalResults"):
            if isinstance(data.get(key), int):
                return data[key]
    return 0


def _strip(v) -> _Opt[str]:
    """Apna wraps matched text in <span> highlight tags — strip them."""
    if v is None:
        return None
    import re as _re
    s = _re.sub(r"<[^>]+>", "", str(v)).strip()
    return s or None


def _s(v) -> _Opt[str]:
    if v is None: return None
    s = str(v).strip(); return s if s else None


def _exp_years(c: dict) -> _Opt[float]:
    raw = (c.get("totalExperienceInYears") or c.get("totalExperience")
           or c.get("workExperience") or c.get("experience"))
    if raw is None: return None
    if isinstance(raw, (int, float)): return round(float(raw), 1)
    if isinstance(raw, dict):
        return round(float(raw.get("years") or 0) + float(raw.get("months") or 0) / 12, 1)
    if isinstance(raw, str):
        import re
        y = int(m.group(1)) if (m := re.search(r"(\\d+)\\s*yr", raw)) else 0
        mo = int(m.group(1)) if (m := re.search(r"(\\d+)\\s*mo", raw)) else 0
        return round(y + mo / 12, 1) if (y or mo) else None
    return None


def _sal_lpa(c: dict) -> _Opt[float]:
    raw = c.get("ctc") or c.get("currentSalary") or c.get("salaryExpectation")
    return round(float(raw), 2) if isinstance(raw, (int, float)) else None


def _live_skills(c: dict) -> list[str]:
    raw = c.get("skills") or c.get("keySkills") or []
    if isinstance(raw, list):
        out = []
        for s in raw:
            val = (s.get("name") or s.get("label")) if isinstance(s, dict) else s
            out.append(_strip(val))
        return [x for x in out if x]
    return [x.strip() for x in _strip(raw).split(",")] if _strip(raw) else []


def _live_location(c: dict) -> _Opt[str]:
    raw = c.get("location") or c.get("currentCity") or c.get("city")
    if isinstance(raw, dict):
        return _s(raw.get("cityNameV2") or raw.get("cityName") or raw.get("label") or raw.get("name"))
    return _s(raw)


def _live_role(c: dict) -> _Opt[str]:
    cur = c.get("currentExperience")
    if isinstance(cur, dict):
        return _strip(cur.get("jobTitle") or cur.get("designation"))
    return _s(c.get("currentDesignation") or c.get("designation") or c.get("currentRole"))


def _live_employer(c: dict) -> _Opt[str]:
    cur = c.get("currentExperience")
    if isinstance(cur, dict):
        return _s(cur.get("companyName"))
    return _s(c.get("currentCompany") or c.get("companyName") or c.get("currentEmployer"))


def _live_education(c: dict) -> _Opt[str]:
    edu = c.get("education")
    if isinstance(edu, dict):
        return _s(edu.get("title") or edu.get("degree"))
    return _s(edu or c.get("highestQualification") or c.get("degree"))


def _live_id(c: dict) -> _Opt[str]:
    for k in ("candidateId", "id", "userId", "profileId"):
        if c.get(k): return str(c[k])
    return None


def _live_to_raw(c: dict) -> _Opt[RawCandidate]:
    name = _strip(c.get("fullName") or c.get("name") or c.get("candidateName"))
    if not name: return None
    cid = _live_id(c)
    return RawCandidate(
        name=name, source=CandidateSource.APNA,
        # Phone is locked in search results until "unlocked" with Apna credits.
        phone=_s(c.get("phone") or c.get("phoneNumber") or c.get("mobile")),
        skills=_live_skills(c), experience_years=_exp_years(c),
        current_salary=_sal_lpa(c), location=_live_location(c),
        current_role=_live_role(c),
        current_employer=_live_employer(c),
        education=_live_education(c),
        source_ref=f"apna:{cid}" if cid else None, raw_profile=str(c),
    )


def to_preview(c: dict) -> dict:
    return {
        "apna_id": _live_id(c) or "",
        "name": _strip(c.get("fullName") or c.get("name") or c.get("candidateName")) or "Unknown",
        "current_role": _live_role(c),
        "current_employer": _live_employer(c),
        "experience_years": _exp_years(c), "location": _live_location(c), "salary_lpa": _sal_lpa(c),
        "skills": _live_skills(c)[:8],
        "education": _live_education(c),
        "active_label": _s(c.get("activeOn") or c.get("lastActiveLabel") or c.get("activeLabel")),
        "raw": c,
    }
