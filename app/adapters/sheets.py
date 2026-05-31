"""Google Sheets adapter — reads candidate data from HR Master Sheet.

Requires a Google Service Account JSON with Sheets API access, or falls back
to the Drive MCP integration when running inside the Claude agent environment.

Configure via environment variables:
  GOOGLE_SA_CREDENTIALS_JSON = path to service account JSON file
  HR_MASTER_SHEET_ID         = Google Sheets spreadsheet ID
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.adapters.base import BasePortalAdapter, RawCandidate
from app.models.candidate import CandidateSource

logger = logging.getLogger(__name__)

# Column indices in "Resume Text DB" sheet (0-based)
_COL_NAME = 0
_COL_EMAIL = 1
_COL_PHONE = 2
_COL_EXPERIENCE = 3
_COL_CURRENT_SALARY = 4
_COL_EXPECTED_SALARY = 5
_COL_NOTICE = 6
_COL_LOCATION = 7
_COL_SKILLS = 8
_COL_EMPLOYER = 9
_COL_ROLE = 10
_COL_RESUME_TEXT = 11


def _parse_salary(value: str) -> Optional[float]:
    """Convert '₹45,000' or '45000' to float."""
    if not value:
        return None
    cleaned = value.replace("₹", "").replace(",", "").replace("K", "000").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_notice(value: str) -> Optional[int]:
    """Convert 'Immediate', '15 days', '1 month', '2 months' to int days."""
    if not value:
        return None
    v = value.strip().lower()
    if v in ("immediate", "0", ""):
        return 0
    try:
        parts = v.split()
        num = float(parts[0])
        if "month" in v:
            return int(num * 30)
        if "day" in v or "days" in v:
            return int(num)
        return int(num)
    except (ValueError, IndexError):
        return None


def _parse_experience(value: str) -> Optional[float]:
    """Convert '4 years', '6+', '3' to float years."""
    if not value:
        return None
    cleaned = value.replace("+", "").replace("years", "").replace("yrs", "").strip()
    try:
        return float(cleaned.split()[0])
    except (ValueError, IndexError):
        return None


class GoogleSheetsAdapter(BasePortalAdapter):
    """Reads candidates from a Google Sheets HR Master Sheet.

    Falls back gracefully with an empty list if credentials are not configured,
    so the rest of the pipeline continues unaffected.
    """

    def __init__(self, sheet_id: str, tab_name: str = "Resume Text DB"):
        self._sheet_id = sheet_id
        self._tab_name = tab_name
        self._creds_path = os.getenv("GOOGLE_SA_CREDENTIALS_JSON", "")

    @property
    def source(self) -> CandidateSource:
        return CandidateSource.MANUAL

    async def search(
        self,
        keywords: list[str],
        location: Optional[str] = None,
        experience_min: Optional[float] = None,
        experience_max: Optional[float] = None,
        limit: int = 200,
    ) -> list[RawCandidate]:
        """Read all rows from the sheet and filter by keywords + experience."""
        rows = await self._fetch_rows()
        if not rows:
            return []

        kw_lower = [k.lower() for k in keywords]
        results: list[RawCandidate] = []

        for row in rows[1:]:  # skip header
            if len(row) < 4:
                continue

            name = row[_COL_NAME] if len(row) > _COL_NAME else ""
            if not name:
                continue

            skills_raw = row[_COL_SKILLS] if len(row) > _COL_SKILLS else ""
            skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

            resume_text = row[_COL_RESUME_TEXT] if len(row) > _COL_RESUME_TEXT else ""
            searchable = " ".join([name, skills_raw, resume_text]).lower()

            if kw_lower and not any(k in searchable for k in kw_lower):
                continue

            exp = _parse_experience(row[_COL_EXPERIENCE] if len(row) > _COL_EXPERIENCE else "")
            if experience_min is not None and exp is not None and exp < experience_min:
                continue
            if experience_max is not None and exp is not None and exp > experience_max:
                continue

            results.append(
                RawCandidate(
                    name=name,
                    source=CandidateSource.MANUAL,
                    email=row[_COL_EMAIL] if len(row) > _COL_EMAIL else None,
                    phone=row[_COL_PHONE] if len(row) > _COL_PHONE else None,
                    skills=skills,
                    experience_years=exp,
                    current_salary=_parse_salary(
                        row[_COL_CURRENT_SALARY] if len(row) > _COL_CURRENT_SALARY else ""
                    ),
                    expected_salary=_parse_salary(
                        row[_COL_EXPECTED_SALARY] if len(row) > _COL_EXPECTED_SALARY else ""
                    ),
                    notice_period_days=_parse_notice(
                        row[_COL_NOTICE] if len(row) > _COL_NOTICE else ""
                    ),
                    location=row[_COL_LOCATION] if len(row) > _COL_LOCATION else None,
                    current_employer=row[_COL_EMPLOYER] if len(row) > _COL_EMPLOYER else None,
                    current_role=row[_COL_ROLE] if len(row) > _COL_ROLE else None,
                    raw_profile=resume_text or None,
                    source_ref=f"sheets:{self._sheet_id}",
                )
            )

            if len(results) >= limit:
                break

        logger.info("GoogleSheetsAdapter: %d candidates returned", len(results))
        return results

    async def _fetch_rows(self) -> list[list[str]]:
        """Fetch all rows from the configured sheet tab.

        Requires google-api-python-client in the environment and a service
        account JSON at GOOGLE_SA_CREDENTIALS_JSON.
        """
        if not self._creds_path or not os.path.exists(self._creds_path):
            logger.warning(
                "GoogleSheetsAdapter: GOOGLE_SA_CREDENTIALS_JSON not set or file not found. "
                "Returning empty — seed the DB manually via seed_real_data.py."
            )
            return []

        try:
            from googleapiclient.discovery import build  # type: ignore
            from google.oauth2.service_account import Credentials  # type: ignore

            creds = Credentials.from_service_account_file(
                self._creds_path,
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
            )
            service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=self._sheet_id, range=self._tab_name)
                .execute()
            )
            return result.get("values", [])
        except Exception as exc:
            logger.error("GoogleSheetsAdapter: fetch failed: %s", exc)
            return []

    async def health_check(self) -> bool:
        rows = await self._fetch_rows()
        return len(rows) > 0
