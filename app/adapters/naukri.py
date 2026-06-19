"""Naukri CSV adapter — parses the standard Naukri employer CSV download.

Naukri CSV columns (typical format, columns may vary by download type):
  Candidate Name, Email ID, Mobile Number, Total Experience,
  Current CTC, Expected CTC, Notice Period, Current Location,
  Key Skills, Current Employer, Current Designation, Summary/Headline

The parser is tolerant of column ordering — it matches by header name.
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from app.adapters.base import BasePortalAdapter, RawCandidate
from app.models.candidate import CandidateSource
from app.utils.phone import normalize_indian_mobile

logger = logging.getLogger(__name__)

# Header aliases — Naukri uses slightly different names across export types
_NAME_KEYS    = {"candidate name", "name", "applicant name"}
_EMAIL_KEYS   = {"email id", "email", "email address"}
_PHONE_KEYS   = {"mobile number", "mobile", "phone", "contact number"}
_EXP_KEYS     = {"total experience", "experience", "exp", "experience (years)", "experience(years)"}
_CUR_SAL_KEYS = {"current ctc", "current salary", "current ctc (lpa)", "current ctc(lpa)"}
_EXP_SAL_KEYS = {"expected ctc", "expected salary", "expected ctc (lpa)", "expected ctc(lpa)"}
_NOTICE_KEYS  = {"notice period", "notice period (days)", "availability"}
_LOC_KEYS     = {"current location", "location", "city", "preferred location"}
_SKILLS_KEYS  = {"key skills", "skills", "skill set"}
_EMPLOYER_KEYS = {"current employer", "employer", "company", "current company"}
_ROLE_KEYS    = {"current designation", "designation", "job title", "current role"}
_SUMMARY_KEYS = {"summary", "headline", "profile summary", "about"}


def _find_col(headers: list[str], keys: set[str]) -> Optional[int]:
    for i, h in enumerate(headers):
        if h.strip().lower() in keys:
            return i
    return None


def _parse_salary(value: str) -> Optional[float]:
    """Handle '₹4,50,000', '4.5 LPA', '45000', '4.5' (assumed LPA)."""
    if not value:
        return None
    v = value.replace("₹", "").replace(",", "").replace("LPA", "").strip().lower()
    try:
        num = float(v.split()[0])
        # Values below 1000 are treated as LPA (Lakhs Per Annum → convert to monthly)
        if num < 1000:
            return round(num * 100_000 / 12, 2)
        return num
    except (ValueError, IndexError):
        return None


def _parse_experience(value: str) -> Optional[float]:
    """Handle '4 Years', '4.5', '4 yrs 6 months', '4+'."""
    if not value:
        return None
    v = value.replace("+", "").strip().lower()
    try:
        parts = v.split()
        years = float(parts[0])
        months = 0.0
        for i, p in enumerate(parts):
            if p in ("months", "month", "mths") and i > 0:
                try:
                    months = float(parts[i - 1])
                except ValueError:
                    pass
        return round(years + months / 12, 1)
    except (ValueError, IndexError):
        return None


def _parse_notice(value: str) -> Optional[int]:
    """Handle 'Immediate', '15 Days', '1 Month', '30'."""
    if not value:
        return None
    v = value.strip().lower()
    if v in ("immediate", "0", "currently serving", "immediate joiner"):
        return 0
    try:
        parts = v.split()
        num = float(parts[0])
        if "month" in v:
            return int(num * 30)
        return int(num)
    except (ValueError, IndexError):
        return None


class NaukriCSVAdapter(BasePortalAdapter):
    """Parses a Naukri employer CSV download into RawCandidate records.

    Not a live API adapter — call `parse_csv(text)` directly.
    The `search()` method is a no-op (returns empty list).
    CSV data is fed via the import endpoint.
    """

    @property
    def source(self) -> CandidateSource:
        return CandidateSource.NAUKRI

    async def search(self, keywords, location=None,
                     experience_min=None, experience_max=None, limit=50):
        return []  # no live API — data comes via CSV import

    def parse_csv(self, csv_text: str) -> list[RawCandidate]:
        """Parse Naukri CSV text and return normalised candidates."""
        reader = csv.reader(io.StringIO(csv_text.strip()))
        rows = list(reader)
        if not rows:
            return []

        headers = [h.strip() for h in rows[0]]

        # Locate columns
        col = {
            "name":    _find_col(headers, _NAME_KEYS),
            "email":   _find_col(headers, _EMAIL_KEYS),
            "phone":   _find_col(headers, _PHONE_KEYS),
            "exp":     _find_col(headers, _EXP_KEYS),
            "cur_sal": _find_col(headers, _CUR_SAL_KEYS),
            "exp_sal": _find_col(headers, _EXP_SAL_KEYS),
            "notice":  _find_col(headers, _NOTICE_KEYS),
            "loc":     _find_col(headers, _LOC_KEYS),
            "skills":  _find_col(headers, _SKILLS_KEYS),
            "employer":_find_col(headers, _EMPLOYER_KEYS),
            "role":    _find_col(headers, _ROLE_KEYS),
            "summary": _find_col(headers, _SUMMARY_KEYS),
        }

        def get(row: list[str], key: str) -> str:
            idx = col[key]
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        candidates: list[RawCandidate] = []
        for row in rows[1:]:
            if not row or not any(row):
                continue
            name = get(row, "name")
            if not name:
                continue

            skills_raw = get(row, "skills")
            skills = [s.strip() for s in skills_raw.replace(";", ",").split(",") if s.strip()]

            candidates.append(
                RawCandidate(
                    name=name,
                    source=CandidateSource.NAUKRI,
                    email=get(row, "email") or None,
                    phone=normalize_indian_mobile(get(row, "phone")),
                    skills=skills,
                    experience_years=_parse_experience(get(row, "exp")),
                    current_salary=_parse_salary(get(row, "cur_sal")),
                    expected_salary=_parse_salary(get(row, "exp_sal")),
                    notice_period_days=_parse_notice(get(row, "notice")),
                    location=get(row, "loc") or None,
                    current_employer=get(row, "employer") or None,
                    current_role=get(row, "role") or None,
                    raw_profile=get(row, "summary") or None,
                    source_ref=f"naukri:{get(row, 'email') or name}",
                )
            )

        logger.info("NaukriCSVAdapter: parsed %d candidates", len(candidates))
        return candidates
