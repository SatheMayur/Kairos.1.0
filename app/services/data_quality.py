"""Data quality scan — surface candidate records that need a human to fix them.

Plain-English issues a non-technical recruiter can act on:
  • NO_CONTACT        — no email and no phone (can't be reached at all)
  • BAD_EMAIL         — email is present but malformed
  • BOUNCED           — a real email to them bounced (address dead)
  • SHORT_PHONE       — phone has fewer than 10 digits
  • SUSPICIOUS_SALARY — salary value looks like junk / wrong currency
  • MISSING_DETAILS   — nothing to score on (no skills, experience, or role)

The pure functions take already-loaded Candidate objects; the bounced-email
signal is passed in as a set of candidate IDs (computed from OutreachLog).
"""
from __future__ import annotations

import re

from app.models.candidate import Candidate

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str | None) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def analyze_candidate(c: Candidate, bounced_ids: frozenset[int] = frozenset()) -> list[dict]:
    issues: list[dict] = []
    has_email = bool(c.email and c.email.strip())
    phone_digits = _digits(c.phone) or _digits(c.whatsapp)
    has_phone = bool(phone_digits)

    if not has_email and not has_phone:
        issues.append({
            "code": "NO_CONTACT", "severity": "high",
            "title": "Can't be contacted",
            "detail": "This candidate has no email and no phone number — there is no way to reach them.",
            "fix": "Add an email or phone number, or remove the record.",
        })

    if has_email and not _valid_email(c.email):
        issues.append({
            "code": "BAD_EMAIL", "severity": "high",
            "title": "Email looks invalid",
            "detail": f'The email "{c.email}" doesn\'t look like a real address.',
            "fix": "Correct the email address.",
        })

    if c.id in bounced_ids:
        issues.append({
            "code": "BOUNCED", "severity": "high",
            "title": "Email bounced — get a new address",
            "detail": "A message to this candidate bounced back. Their email address is not working.",
            "fix": "Find and add a personal email address that works.",
        })

    if has_phone and len(phone_digits) < 10:
        issues.append({
            "code": "SHORT_PHONE", "severity": "medium",
            "title": "Phone number looks incomplete",
            "detail": f'The phone number "{c.phone or c.whatsapp}" has fewer than 10 digits.',
            "fix": "Add the full 10-digit mobile number.",
        })

    for label, val in (("Expected", c.expected_salary), ("Current", c.current_salary)):
        if val is not None and val != 0 and (val < 1000 or val > 100_000_000):
            issues.append({
                "code": "SUSPICIOUS_SALARY", "severity": "medium",
                "title": "Salary looks wrong",
                "detail": (f"{label} salary is recorded as {val:g} — that doesn't look like a normal "
                           f"rupee amount (it may be in the wrong currency or unit)."),
                "fix": "Check the original application and correct the salary.",
            })
            break  # one salary flag per candidate is enough

    if not (c.skills or []) and c.experience_years is None and not (c.current_role or "").strip():
        issues.append({
            "code": "MISSING_DETAILS", "severity": "low",
            "title": "Missing details needed to score",
            "detail": "No skills, experience, or job title on record — the system can't score this candidate well.",
            "fix": "Add skills, years of experience, or current role.",
        })

    return issues


def analyze_candidates(candidates: list[Candidate],
                       bounced_ids: frozenset[int] = frozenset()) -> dict:
    flagged: list[dict] = []
    counts = {"high": 0, "medium": 0, "low": 0}
    by_type: dict[str, int] = {}

    for c in candidates:
        issues = analyze_candidate(c, bounced_ids)
        if not issues:
            continue
        if any(i["severity"] == "high" for i in issues):
            sev = "high"
        elif any(i["severity"] == "medium" for i in issues):
            sev = "medium"
        else:
            sev = "low"
        counts[sev] += 1
        for i in issues:
            by_type[i["code"]] = by_type.get(i["code"], 0) + 1
        flagged.append({
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "phone": c.phone or c.whatsapp,
            "source": c.source.value if c.source else None,
            "current_role": c.current_role,
            "current_employer": c.current_employer,
            "severity": sev,
            "issues": issues,
        })

    order = {"high": 0, "medium": 1, "low": 2}
    flagged.sort(key=lambda x: order[x["severity"]])

    return {
        "flagged": flagged,
        "summary": {
            "total_candidates": len(candidates),
            "with_issues": len(flagged),
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "by_type": by_type,
        },
    }
