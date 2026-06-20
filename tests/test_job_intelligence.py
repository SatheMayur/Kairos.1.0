"""Tests for job-centric talent intelligence — bands, recommended actions,
per-job stats, and the discovery API endpoints.

The guiding question is "for THIS job, who are my best candidates right now?",
and the hard rule is: a candidate with no usable phone/email is never offered an
outreach action — they get "Needs contact info" instead.
"""
import pytest

from app.models.candidate import Candidate, CandidateSource
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.job import Job
from app.services.job_intelligence import (
    band_of, match_dimensions, recommended_action,
    compute_stats, build_insights, build_recommendations,
)


# ── pure functions ─────────────────────────────────────────────────────────

def test_band_thresholds():
    assert band_of(80) == "strong"
    assert band_of(95) == "strong"
    assert band_of(79.9) == "medium"
    assert band_of(60) == "medium"
    assert band_of(59.9) == "weak"
    assert band_of(None) == "weak"


def test_match_dimensions_normalize_to_percent():
    # raw weighted scores → 0–100 of each dimension's weight
    bd = {"skills_match": 40, "experience_fit": 12.5, "location_fit": 0, "salary_fit": 15, "role_fit": 5}
    m = match_dimensions(bd)
    assert m["skills"] == 100          # 40/40
    assert m["experience"] == 50       # 12.5/25
    assert m["location"] == 0          # 0/10
    assert m["salary"] == 100          # 15/15
    assert m["role"] == 50             # 5/10


def test_unreachable_candidate_never_gets_outreach_action():
    # strong, pending, but unreachable → must be blocked, not "Shortlist"
    a = recommended_action(ShortlistStatus.PENDING, 90, reachable=False)
    assert a["kind"] == "blocked"
    assert a["key"] == "GET_CONTACT"
    # shortlisted but unreachable → also blocked (no "Contact")
    a2 = recommended_action(ShortlistStatus.SHORTLISTED, 90, reachable=False)
    assert a2["kind"] == "blocked"


def test_recommended_actions_by_status_when_reachable():
    assert recommended_action(ShortlistStatus.PENDING, 90, True)["key"] == "SHORTLIST"
    assert recommended_action(ShortlistStatus.PENDING, 70, True)["key"] == "REVIEW"
    assert recommended_action(ShortlistStatus.SHORTLISTED, 90, True)["key"] == "CONTACT"
    assert recommended_action(ShortlistStatus.INTERESTED, 90, True)["key"] == "SCHEDULE"
    assert recommended_action(ShortlistStatus.HIRED, 90, True)["key"] == "HIRED"


def _entry(cid, score, status=ShortlistStatus.PENDING):
    e = ShortlistEntry(candidate_id=cid, job_id=1, score=score, status=status)
    return e


def _cand(cid, **kw):
    c = Candidate(name=kw.get("name", f"C{cid}"))
    c.id = cid
    c.email = kw.get("email")
    c.phone = kw.get("phone")
    c.whatsapp = kw.get("whatsapp")
    c.source = kw.get("source", CandidateSource.NAUKRI)
    return c


def test_compute_stats_bands_and_reachability():
    entries = [
        _entry(1, 90, ShortlistStatus.PENDING),       # strong, reachable
        _entry(2, 70, ShortlistStatus.SHORTLISTED),   # medium, reachable
        _entry(3, 30, ShortlistStatus.PENDING),       # weak, unreachable
        _entry(4, 85, ShortlistStatus.PENDING),       # strong, unreachable → needs contact
    ]
    cands = {
        1: _cand(1, phone="9876543210"),
        2: _cand(2, email="x@y.com"),
        3: _cand(3),                  # no contact
        4: _cand(4, source=CandidateSource.APNA),     # locked / no contact
    }
    s = compute_stats(entries, cands)
    assert s["total"] == 4
    assert s["bands"] == {"strong": 2, "medium": 1, "weak": 1}
    assert s["reachable"] == 2
    assert s["needs_contact"] == 2     # cands 3 and 4 (open, unreachable)
    assert s["best_now"] == 1          # only cand 1 is strong + reachable + open
    assert s["pipeline"]["PENDING"] == 3
    assert s["pipeline"]["SHORTLISTED"] == 1


def test_insights_and_recommendations_plain_english():
    s = compute_stats(
        [_entry(1, 90, ShortlistStatus.PENDING)],
        {1: _cand(1, phone="9876543210")},
    )
    insights = build_insights(s)
    assert any("strong" in t.lower() for t in insights)
    recs = build_recommendations(s, {"pending_strong_reachable": 1})
    assert recs and recs[0]["title"].startswith("Shortlist")
    assert "filter" in recs[0]


def test_empty_job_insight():
    s = compute_stats([], {})
    assert build_insights(s) == ["No candidates for this job yet. Source candidates or import a CSV to get started."]
    recs = build_recommendations(s, {})
    assert recs[0]["action_url"] == "/ui/import"


# ── API endpoints ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_stats_and_candidates_endpoints(client, db_session):
    job = Job(title="Design Engineer", company="KGL", location="Surat",
              skills=["AutoCAD", "SolidWorks"], experience_min=1, experience_max=3)
    db_session.add(job)
    await db_session.flush()

    strong = Candidate(name="Strong Reachable", phone="9876543210",
                       source=CandidateSource.NAUKRI, skills=["AutoCAD"])
    locked = Candidate(name="Strong NoContact", source=CandidateSource.APNA, skills=["AutoCAD"])
    db_session.add_all([strong, locked])
    await db_session.flush()

    db_session.add_all([
        ShortlistEntry(job_id=job.id, candidate_id=strong.id, score=88,
                       status=ShortlistStatus.PENDING,
                       score_breakdown={"skills_match": 40, "experience_fit": 25, "location_fit": 10}),
        ShortlistEntry(job_id=job.id, candidate_id=locked.id, score=85,
                       status=ShortlistStatus.PENDING, score_breakdown={"skills_match": 40}),
    ])
    await db_session.flush()

    r = await client.get(f"/api/v1/jobs/{job.id}/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["bands"]["strong"] == 2
    assert data["needs_contact"] == 1      # the locked Apna candidate
    assert data["insights"]

    r2 = await client.get(f"/api/v1/jobs/{job.id}/candidates")
    assert r2.status_code == 200
    rows = r2.json()["candidates"]
    assert len(rows) == 2
    # sorted by score desc
    assert rows[0]["name"] == "Strong Reachable"
    by_name = {c["name"]: c for c in rows}
    # reachable strong pending → Shortlist; locked → needs contact (blocked)
    assert by_name["Strong Reachable"]["recommended_action"]["key"] == "SHORTLIST"
    assert by_name["Strong NoContact"]["needs_contact"] is True
    assert by_name["Strong NoContact"]["recommended_action"]["kind"] == "blocked"
    # per-dimension match surfaced as percentages
    assert by_name["Strong Reachable"]["match"]["skills"] == 100


@pytest.mark.asyncio
async def test_job_candidates_filters(client, db_session):
    job = Job(title="HR Executive", company="KGL")
    db_session.add(job)
    await db_session.flush()
    weak = Candidate(name="Weak", phone="9876543210", source=CandidateSource.NAUKRI)
    db_session.add(weak)
    await db_session.flush()
    db_session.add(ShortlistEntry(job_id=job.id, candidate_id=weak.id, score=30,
                                  status=ShortlistStatus.PENDING))
    await db_session.flush()

    # band filter excludes the weak candidate when asking for strong
    r = await client.get(f"/api/v1/jobs/{job.id}/candidates?band=strong")
    assert r.json()["count"] == 0
    r2 = await client.get(f"/api/v1/jobs/{job.id}/candidates?band=weak")
    assert r2.json()["count"] == 1
