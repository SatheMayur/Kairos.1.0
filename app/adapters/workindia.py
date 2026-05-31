"""WorkIndia CSV adapter — parses WorkIndia employer CSV download.

WorkIndia CSV columns (typical):
  Name, Mobile Number, Email, Skills, Experience (Years),
  Current Salary, Expected Salary, Current Location, Notice Period

WorkIndia targets blue-collar / semi-skilled workers so salary values
tend to be in ₹/month already (not LPA).
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from app.adapters.base import BasePortalAdapter, RawCandidate
from app.models.candidate import CandidateSource

logger = logging.getLogger(__name__)

_NAME_KEYS    = {"name", "candidate name", "applicant name"}
_PHONE_KEYS   = {"mobile number", "mobile", "phone", "contact"}
_EMAIL_KEYS   = {"email", "email id", "email address"}
_SKILLS_KEYS  = {"skills", "key skills", "skill"}
_EXP_KEYS     = {"experience (years)", "experience", "exp", "total experience"}
_CUR_SAL_KEYS = {"current salary", "current ctc", "salary"}
_EXP_SAL_KEYS = {"expected salary", "expected ctc"}
_LOC_KEYS     = {"current location", "location", "city"}
_NOTICE_KEYS  = {"notice period", "availability", "joining"}
_ROLE_KEYS    = {"current designation", "designation", "role", "job title"}
_EMPLOYER_KEYS = {"current employer", "employer", "company"}


def _find_col(headers: list[str], keys: set[str]) -> Optional[int]:
    for i, h in enumerate(headers):
        if h.strip().lower() in keys:
            return i
    return None


def _parse_salary(value: str) -> Optional[float]:
    if not value:
        return None
    v = value.replace("₹", "").replace(",", "").strip().lower()
    try:
        num = float(v.split()[0])
        # WorkIndia salaries are typically monthly ₹
        # Anything < 500 is likely wrong data; anything > 200000 unlikely for these roles
        return num if 500 <= num <= 500_000 else None
    except (ValueError, IndexError):
        return None


def _parse_experience(value: str) -> Optional[float]:
    if not value:
        return None
    v = value.replace("+", "").replace("years", "").replace("yrs", "").strip()
    try:
        return float(v.split()[0])
    except (ValueError, IndexError):
        return None


def _parse_notice(value: str) -> Optional[int]:
    if not value:
        return None
    v = value.strip().lower()
    if v in ("immediate", "0"):
        return 0
    try:
        num = float(v.split()[0])
        return int(num * 30) if "month" in v else int(num)
    except (ValueError, IndexError):
        return None


class WorkIndiaCSVAdapter(BasePortalAdapter):
    """Parses a WorkIndia employer CSV download.

    No live API — feed data via the CSV import endpoint.
    """

    @property
    def source(self) -> CandidateSource:
        return CandidateSource.WORKINDIA

    async def search(self, keywords, location=None,
                     experience_min=None, experience_max=None, limit=50):
        return []

    def parse_csv(self, csv_text: str) -> list[RawCandidate]:
        reader = csv.reader(io.StringIO(csv_text.strip()))
        rows = list(reader)
        if not rows:
            return []

        headers = [h.strip() for h in rows[0]]
        col = {
            "name":    _find_col(headers, _NAME_KEYS),
            "phone":   _find_col(headers, _PHONE_KEYS),
            "email":   _find_col(headers, _EMAIL_KEYS),
            "skills":  _find_col(headers, _SKILLS_KEYS),
            "exp":     _find_col(headers, _EXP_KEYS),
            "cur_sal": _find_col(headers, _CUR_SAL_KEYS),
            "exp_sal": _find_col(headers, _EXP_SAL_KEYS),
            "loc":     _find_col(headers, _LOC_KEYS),
            "notice":  _find_col(headers, _NOTICE_KEYS),
            "role":    _find_col(headers, _ROLE_KEYS),
            "employer":_find_col(headers, _EMPLOYER_KEYS),
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
                    source=CandidateSource.WORKINDIA,
                    email=get(row, "email") or None,
                    phone=get(row, "phone") or None,
                    skills=skills,
                    experience_years=_parse_experience(get(row, "exp")),
                    current_salary=_parse_salary(get(row, "cur_sal")),
                    expected_salary=_parse_salary(get(row, "exp_sal")),
                    notice_period_days=_parse_notice(get(row, "notice")),
                    location=get(row, "loc") or None,
                    current_role=get(row, "role") or None,
                    current_employer=get(row, "employer") or None,
                    source_ref=f"workindia:{get(row, 'phone') or name}",
                )
            )

        logger.info("WorkIndiaCSVAdapter: parsed %d candidates", len(candidates))
        return candidates
