"""Data quality scan — surface candidate records that need a human to fix them.

Plain-English issues a non-technical recruiter can act on:
  • LOCKED_CONTACT    — they're shortlisted/being lined up but their phone & email
                        are hidden (e.g. Apna hides contact details until unlocked)
  • NO_CONTACT        — no email and no phone (can't be reached at all)
  • BAD_EMAIL         — email is present but malformed
  • BOUNCED           — a real email to them bounced (address dead)
  • SHORT_PHONE       — phone has fewer than 10 digits
  • SUSPICIOUS_SALARY — salary value looks like junk / wrong currency
  • MISSING_DETAILS   — nothing to score on (no skills, experience, or role)

The pure functions take already-loaded Candidate objects; the bounced-email
signal is passed in as a set of candidate IDs (computed from OutreachLog), and
the "lined up but not yet contacted" signal is passed in as a set of candidate
IDs (computed from ShortlistEntry status).
"""
from __future__ import annotations

import re

from app.models.candidate import Candidate
from app.utils.phone import normalize_indian_mobile

# Sources that hide a candidate's phone/email until the employer spends credits
# to unlock them. Apna is the main one; this can grow if others behave the same.
_LOCKED_CONTACT_SOURCES = {"APNA"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str | None) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


def is_reachable_contact(email: str | None = None,
                         phone: str | None = None,
                         whatsapp: str | None = None) -> bool:
    """True iff these raw contact fields give a real way to reach someone:
    a well-formed email OR a phone/whatsapp that normalizes to a valid Indian
    mobile. Used at ingestion to refuse candidates with no contact info, and by
    is_reachable() for already-loaded Candidate rows (one source of truth)."""
    if _valid_email(email):
        return True
    if normalize_indian_mobile(phone) or normalize_indian_mobile(whatsapp):
        return True
    return False


def is_reachable(candidate: Candidate) -> bool:
    """True iff we have a real way to contact this candidate.

    Reachable = a well-formed email OR a phone/whatsapp that normalizes to a
    valid Indian mobile. A locked Apna profile (no email, hidden phone), a junk
    "NA" number, or a landline is NOT reachable — so we never mark such a
    candidate CONTACTED when no message could actually have reached them.
    """
    return is_reachable_contact(candidate.email, candidate.phone, candidate.whatsapp)


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def analyze_candidate(
    c: Candidate,
    bounced_ids: frozenset[int] = frozenset(),
    awaiting_contact_ids: frozenset[int] = frozenset(),
) -> list[dict]:
    issues: list[dict] = []
    has_email = bool(c.email and c.email.strip())
    raw_phone = c.phone or c.whatsapp
    phone_digits = _digits(c.phone) or _digits(c.whatsapp)
    # A phone counts as reachable only if it normalizes to a valid Indian
    # mobile — a landline, an 11-digit junk run, or "NA" is NOT a usable phone.
    valid_mobile = normalize_indian_mobile(raw_phone)
    has_phone = bool(valid_mobile)
    source = c.source.value if c.source else None

    # Treat a present-but-unusable phone (landline / wrong length / "NA") as
    # "no phone" for the can't-be-reached decision below, but only when there
    # are NO phone digits at all do we say "no phone number on record". A junk
    # number gets its own BAD_PHONE flag further down.
    if not has_email and not has_phone and not phone_digits:
        # Special case: this person has been shortlisted (or is pending review)
        # but has no way to reach them — and on sources like Apna that's because
        # the contact details are hidden until you unlock them. Surface that with
        # a clear, do-this-next message instead of the generic "Can't be contacted",
        # so a good candidate never silently sits there un-contacted.
        if c.id in awaiting_contact_ids and source in _LOCKED_CONTACT_SOURCES:
            issues.append({
                "code": "LOCKED_CONTACT", "severity": "high",
                "title": "Phone hidden by Apna — unlock to contact",
                "detail": ("This person looks good and is lined up to be contacted, but Apna is "
                           "hiding their phone number and email. Right now there is no way to reach them."),
                "fix": ("Click 'Open Apna to unlock' below, find this person, and click Unlock on Apna "
                        "(this uses Apna credits) to see their phone number — then add it here so we can message them."),
                "action_url": "https://employer.apna.co/database",
                "action_label": "Open Apna to unlock ↗",
            })
        elif c.id in awaiting_contact_ids:
            issues.append({
                "code": "LOCKED_CONTACT", "severity": "high",
                "title": "Lined up to contact, but no phone or email",
                "detail": ("This person is shortlisted or waiting for review, but there is no phone "
                           "number or email on record — so they can't be contacted yet."),
                "fix": ("Find their phone number or email (check the job site they came from) and "
                        "add it here so we can reach out."),
            })
        else:
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

    # A phone is present but not a usable Indian mobile (too short, a landline,
    # an 11-digit junk run, or text like "NA"). If there's no email either, this
    # person can't be reached at all — flag it as high severity so it shows up
    # in Needs Fixing exactly like a no-contact record.
    if phone_digits and not valid_mobile:
        short = len(phone_digits) < 10
        issues.append({
            "code": "SHORT_PHONE" if short else "BAD_PHONE",
            "severity": "high" if not has_email else "medium",
            "title": "Phone number looks incomplete" if short else "Phone number isn't a valid mobile",
            "detail": (f'The phone number "{c.phone or c.whatsapp}" '
                       + ("has fewer than 10 digits." if short
                          else "isn't a usable 10-digit Indian mobile (it may be a landline or have extra digits).")),
            "fix": "Add the full 10-digit mobile number." + ("" if has_email else " Without it (and with no email) we can't reach this person."),
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
                       bounced_ids: frozenset[int] = frozenset(),
                       awaiting_contact_ids: frozenset[int] = frozenset()) -> dict:
    flagged: list[dict] = []
    counts = {"high": 0, "medium": 0, "low": 0}
    by_type: dict[str, int] = {}

    for c in candidates:
        issues = analyze_candidate(c, bounced_ids, awaiting_contact_ids)
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
