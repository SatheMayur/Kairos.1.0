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
