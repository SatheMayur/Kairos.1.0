"""Tests for the data-quality scan — the 'Needs Fixing' analyzer.

Focus: candidates who are shortlisted / pending but have no phone and no email
(especially Apna-source, where the contact details are hidden until unlocked)
must be surfaced under the LOCKED_CONTACT category so they never silently sit
there un-contacted — and must not be double-counted as plain NO_CONTACT.
"""
from app.models.candidate import Candidate, CandidateSource
from app.services.data_quality import analyze_candidate, analyze_candidates


def _cand(**kw):
    """Build a lightweight Candidate object for pure-function tests."""
    c = Candidate(name=kw.pop("name", "Test Person"))
    c.id = kw.pop("id", 1)
    c.email = kw.pop("email", None)
    c.phone = kw.pop("phone", None)
    c.whatsapp = kw.pop("whatsapp", None)
    c.source = kw.pop("source", CandidateSource.APNA)
    c.skills = kw.pop("skills", ["AutoCAD"])
    c.experience_years = kw.pop("experience_years", 2.0)
    c.current_role = kw.pop("current_role", "Design Engineer")
    c.current_employer = kw.pop("current_employer", None)
    c.expected_salary = kw.pop("expected_salary", None)
    c.current_salary = kw.pop("current_salary", None)
    return c


def _codes(issues):
    return {i["code"] for i in issues}


def test_shortlisted_apna_no_contact_is_locked_contact():
    """A shortlisted Apna candidate with no phone/email gets the Apna unlock message."""
    c = _cand(id=42, source=CandidateSource.APNA, phone=None, email=None)
    issues = analyze_candidate(c, awaiting_contact_ids=frozenset({42}))

    codes = _codes(issues)
    assert "LOCKED_CONTACT" in codes
    # Must NOT be double-counted as the generic "Can't be contacted".
    assert "NO_CONTACT" not in codes

    locked = next(i for i in issues if i["code"] == "LOCKED_CONTACT")
    assert locked["severity"] == "high"
    assert "Apna" in locked["title"]
    assert "Unlock" in locked["fix"]


def test_pending_non_apna_no_contact_is_generic_locked_contact():
    """A pending (lined-up) non-Apna candidate with no contact uses the generic message."""
    c = _cand(id=7, source=CandidateSource.NAUKRI, phone=None, email=None)
    issues = analyze_candidate(c, awaiting_contact_ids=frozenset({7}))

    codes = _codes(issues)
    assert "LOCKED_CONTACT" in codes
    assert "NO_CONTACT" not in codes
    locked = next(i for i in issues if i["code"] == "LOCKED_CONTACT")
    assert "Apna" not in locked["title"]


def test_unscored_apna_no_contact_stays_no_contact():
    """An Apna candidate with no contact who is NOT shortlisted/pending stays NO_CONTACT."""
    c = _cand(id=99, source=CandidateSource.APNA, phone=None, email=None)
    issues = analyze_candidate(c, awaiting_contact_ids=frozenset())

    codes = _codes(issues)
    assert "NO_CONTACT" in codes
    assert "LOCKED_CONTACT" not in codes


def test_shortlisted_with_phone_is_not_flagged_as_locked():
    """Having a phone means no contact problem at all — not flagged."""
    c = _cand(id=5, source=CandidateSource.APNA, phone="9876543210", email=None)
    issues = analyze_candidate(c, awaiting_contact_ids=frozenset({5}))
    codes = _codes(issues)
    assert "LOCKED_CONTACT" not in codes
    assert "NO_CONTACT" not in codes


def test_summary_counts_locked_contact():
    """The aggregate summary surfaces LOCKED_CONTACT in by_type and high count."""
    candidates = [
        _cand(id=1, source=CandidateSource.APNA, phone=None, email=None),
        _cand(id=2, source=CandidateSource.APNA, phone="9876543210"),
    ]
    out = analyze_candidates(candidates, awaiting_contact_ids=frozenset({1, 2}))
    assert out["summary"]["by_type"].get("LOCKED_CONTACT") == 1
    assert out["summary"]["high"] >= 1
    flagged_ids = {f["id"] for f in out["flagged"]}
    assert 1 in flagged_ids
    assert 2 not in flagged_ids
