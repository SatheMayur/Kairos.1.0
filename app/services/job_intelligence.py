"""Job-centric talent intelligence.

Answers the one question a recruiter actually has: "For THIS job, who are my
best candidates right now?" — and turns the raw shortlist/score data into
plain-English bands, a recommended next action per candidate, and a short list
of insights + recommendations the owner can act on without knowing any jargon.

Pure functions only (no DB, no I/O) so they're trivially testable. The API layer
loads the rows and hands them in.
"""
from __future__ import annotations

from app.models.candidate import Candidate
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.data_quality import is_reachable
from app.services.scoring import WEIGHTS

# Score bands. "Strong" candidates clear 80; "medium" 60–79; everything else weak.
STRONG_MIN = 80.0
MEDIUM_MIN = 60.0

# Human labels for each pipeline status.
STATUS_LABEL = {
    ShortlistStatus.PENDING: "Needs review",
    ShortlistStatus.SHORTLISTED: "Shortlisted",
    ShortlistStatus.REJECTED: "Rejected",
    ShortlistStatus.CONTACTED: "Contacted",
    ShortlistStatus.INTERESTED: "Interested",
    ShortlistStatus.NOT_INTERESTED: "Not interested",
    ShortlistStatus.INTERVIEW_SCHEDULED: "Interview scheduled",
    ShortlistStatus.HIRED: "Hired",
    ShortlistStatus.DROPPED: "Dropped",
}

# Statuses that mean "this candidate is no longer in play" — excluded from the
# "best candidates right now" view by default.
CLOSED_STATUSES = frozenset({
    ShortlistStatus.REJECTED,
    ShortlistStatus.NOT_INTERESTED,
    ShortlistStatus.DROPPED,
})


def band_of(score: float | None) -> str:
    """strong / medium / weak for a 0–100 match score."""
    s = score or 0.0
    if s >= STRONG_MIN:
        return "strong"
    if s >= MEDIUM_MIN:
        return "medium"
    return "weak"


def _pct(raw: float | None, weight: float) -> int:
    """A raw weighted dimension score (0..weight) → a 0–100 percentage."""
    if not weight:
        return 0
    return max(0, min(100, round((raw or 0.0) / weight * 100)))


def match_dimensions(breakdown: dict | None) -> dict:
    """Per-dimension match as easy 0–100 percentages (Skills / Experience / Location)."""
    bd = breakdown or {}
    return {
        "skills": _pct(bd.get("skills_match"), WEIGHTS["skills_match"]),
        "experience": _pct(bd.get("experience_fit"), WEIGHTS["experience_fit"]),
        "location": _pct(bd.get("location_fit"), WEIGHTS["location_fit"]),
        "salary": _pct(bd.get("salary_fit"), WEIGHTS["salary_fit"]),
        "role": _pct(bd.get("role_fit"), WEIGHTS["role_fit"]),
    }


def recommended_action(status: ShortlistStatus, score: float | None, reachable: bool) -> dict:
    """The single next step a recruiter should take for this candidate.

    Returns {key, label, kind}. kind is 'primary' (do this now), 'neutral'
    (waiting / informational), or 'blocked' (can't act — needs contact info).
    Outreach actions are NEVER suggested for an unreachable candidate.
    """
    # No way to reach them → the only thing to do is get contact info.
    if not reachable and status not in (
        ShortlistStatus.HIRED, ShortlistStatus.REJECTED,
        ShortlistStatus.NOT_INTERESTED, ShortlistStatus.DROPPED,
    ):
        return {"key": "GET_CONTACT", "label": "Needs contact info", "kind": "blocked"}

    if status == ShortlistStatus.PENDING:
        band = band_of(score)
        if band == "strong":
            return {"key": "SHORTLIST", "label": "Shortlist", "kind": "primary"}
        if band == "medium":
            return {"key": "REVIEW", "label": "Review", "kind": "primary"}
        return {"key": "REVIEW_LOW", "label": "Review / reject", "kind": "neutral"}
    if status == ShortlistStatus.SHORTLISTED:
        return {"key": "CONTACT", "label": "Contact", "kind": "primary"}
    if status == ShortlistStatus.CONTACTED:
        return {"key": "FOLLOW_UP", "label": "Awaiting reply", "kind": "neutral"}
    if status == ShortlistStatus.INTERESTED:
        return {"key": "SCHEDULE", "label": "Schedule interview", "kind": "primary"}
    if status == ShortlistStatus.INTERVIEW_SCHEDULED:
        return {"key": "INTERVIEW", "label": "Interview lined up", "kind": "neutral"}
    if status == ShortlistStatus.HIRED:
        return {"key": "HIRED", "label": "Hired ✓", "kind": "neutral"}
    # REJECTED / NOT_INTERESTED / DROPPED
    return {"key": "CLOSED", "label": STATUS_LABEL.get(status, "Closed"), "kind": "neutral"}


def compute_stats(entries: list[ShortlistEntry], cands: dict[int, Candidate]) -> dict:
    """Per-job headline numbers: score bands + pipeline stage counts + reachability."""
    bands = {"strong": 0, "medium": 0, "weak": 0}
    pipeline = {s.value: 0 for s in ShortlistStatus}
    reachable_n = 0
    needs_contact_n = 0
    open_strong_reachable = 0  # strong, still in play, contactable → "best right now"

    for e in entries:
        bands[band_of(e.score)] += 1
        pipeline[e.status.value] = pipeline.get(e.status.value, 0) + 1
        c = cands.get(e.candidate_id)
        ok = is_reachable(c) if c is not None else False
        if ok:
            reachable_n += 1
        elif e.status not in (ShortlistStatus.REJECTED, ShortlistStatus.NOT_INTERESTED,
                              ShortlistStatus.DROPPED, ShortlistStatus.HIRED):
            needs_contact_n += 1
        if ok and e.status not in CLOSED_STATUSES and band_of(e.score) == "strong":
            open_strong_reachable += 1

    return {
        "total": len(entries),
        "bands": bands,
        "pipeline": pipeline,
        "reachable": reachable_n,
        "needs_contact": needs_contact_n,
        "best_now": open_strong_reachable,
    }


def build_insights(stats: dict) -> list[str]:
    """Plain-English read of the talent pool for this job (no jargon)."""
    out: list[str] = []
    total = stats["total"]
    if total == 0:
        return ["No candidates for this job yet. Source candidates or import a CSV to get started."]

    b = stats["bands"]
    out.append(
        f"{total} candidate{'s' if total != 1 else ''} for this job — "
        f"{b['strong']} strong match{'es' if b['strong'] != 1 else ''} (80+), "
        f"{b['medium']} good (60–79), {b['weak']} weak (below 60)."
    )

    if stats["best_now"]:
        out.append(
            f"{stats['best_now']} strong candidate{'s' if stats['best_now'] != 1 else ''} "
            "can be contacted right now — these are your best bets."
        )
    elif b["strong"]:
        out.append("You have strong candidates, but none can be contacted yet (no phone/email or already closed).")

    if stats["needs_contact"]:
        out.append(
            f"{stats['needs_contact']} candidate{'s' if stats['needs_contact'] != 1 else ''} "
            "need contact info before you can reach them (e.g. unlock their phone on Apna)."
        )

    p = stats["pipeline"]
    contacted = p.get("CONTACTED", 0) + p.get("INTERESTED", 0) + p.get("INTERVIEW_SCHEDULED", 0) + p.get("HIRED", 0)
    if contacted == 0 and total:
        out.append("Nobody has been contacted yet for this job.")
    if p.get("HIRED"):
        out.append(f"{p['HIRED']} candidate{'s' if p['HIRED'] != 1 else ''} hired for this role.")
    return out


def build_recommendations(stats: dict, counts: dict) -> list[dict]:
    """A short list of concrete next steps. `counts` carries action-ready tallies
    computed by the API (it knows reachability per candidate)."""
    recs: list[dict] = []
    if stats["total"] == 0:
        recs.append({
            "title": "Find candidates for this job",
            "detail": "There are no candidates yet. Run sourcing or import a candidate list.",
            "action_label": "Import / Source ↗", "action_url": "/ui/import",
        })
        return recs

    if counts.get("pending_strong_reachable"):
        n = counts["pending_strong_reachable"]
        recs.append({
            "title": f"Shortlist {n} strong candidate{'s' if n != 1 else ''}",
            "detail": "These are high-scoring matches waiting for your review — shortlist them to start outreach.",
            "filter": {"band": "strong", "status": "PENDING"},
        })

    if counts.get("shortlisted_reachable"):
        n = counts["shortlisted_reachable"]
        recs.append({
            "title": f"Contact {n} shortlisted candidate{'s' if n != 1 else ''}",
            "detail": "They're shortlisted and reachable — send them a WhatsApp or email now.",
            "filter": {"status": "SHORTLISTED"},
        })

    if counts.get("interested"):
        n = counts["interested"]
        recs.append({
            "title": f"Schedule {n} interested candidate{'s' if n != 1 else ''}",
            "detail": "They replied that they're interested — book an interview slot.",
            "filter": {"status": "INTERESTED"},
        })

    if stats["needs_contact"]:
        n = stats["needs_contact"]
        recs.append({
            "title": f"Get contact info for {n} candidate{'s' if n != 1 else ''}",
            "detail": "Good candidates with no phone or email — unlock them on the job site (e.g. Apna) and add their number.",
            "filter": {"needs_contact": "1"},
        })

    return recs
