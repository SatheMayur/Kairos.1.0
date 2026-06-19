"""Unit tests for duplicate detection (pure functions, no DB)."""
from datetime import datetime

from app.models.candidate import Candidate, CandidateSource
from app.services.duplicates import find_duplicates


def _cand(cid, name, **kw):
    """Build an in-memory Candidate without touching the DB."""
    c = Candidate(
        id=cid,
        name=name,
        email=kw.get("email"),
        phone=kw.get("phone"),
        whatsapp=kw.get("whatsapp"),
        location=kw.get("location"),
        current_employer=kw.get("current_employer"),
        current_role=kw.get("current_role"),
        education=kw.get("education"),
        skills=kw.get("skills"),
        raw_profile=kw.get("raw_profile"),
        source=kw.get("source", CandidateSource.APNA),
        source_ref=kw.get("source_ref"),
    )
    c.created_at = kw.get("created_at", datetime(2026, 6, 1))
    return c


def _contact_ids(result):
    """All candidate-id sets across the 'possibly same person' clusters."""
    return [frozenset(m["id"] for m in cl["candidates"]) for cl in result["same_contact"]]


# ── Existing behaviour still works ───────────────────────────────────────────

def test_same_email_still_grouped():
    cands = [
        _cand(1, "Asha Patel", email="asha@x.com"),
        _cand(2, "Asha P", email="asha@x.com"),
    ]
    res = find_duplicates(cands)
    assert frozenset({1, 2}) in _contact_ids(res)


def test_same_phone_still_grouped():
    cands = [
        _cand(1, "Ravi Shah", phone="9876543210"),
        _cand(2, "Different Person", whatsapp="91 9876543210"),
    ]
    res = find_duplicates(cands)
    assert frozenset({1, 2}) in _contact_ids(res)


# ── New rule: name + context across two Apna paths ───────────────────────────

def test_same_name_and_employer_is_flagged():
    """CSV import (has email) + Apna live-search (locked contact) for one person."""
    cands = [
        _cand(1, "Priya Sharma", email="priya@x.com",
              current_employer="Tanishq Jewels", source_ref="apna:priya@x.com"),
        _cand(2, "Priya Sharma", current_employer="Tanishq Jewels",
              source=CandidateSource.APNA, source_ref="apna:u-99127"),
    ]
    res = find_duplicates(cands)
    assert frozenset({1, 2}) in _contact_ids(res)
    cl = next(cl for cl in res["same_contact"]
              if frozenset(m["id"] for m in cl["candidates"]) == frozenset({1, 2}))
    assert "name" in cl["match"]
    assert cl["shared_email"] is None and cl["shared_phone"] is None


def test_same_name_and_location_is_flagged():
    cands = [
        _cand(1, "Karan Mehta", email="karan@x.com", location="Surat"),
        _cand(2, "Karan Mehta", location="Surat", source_ref="apna:u-555"),
    ]
    res = find_duplicates(cands)
    assert frozenset({1, 2}) in _contact_ids(res)


def test_same_name_only_is_NOT_flagged():
    """Name alone must never group people — too common to be reliable."""
    cands = [
        _cand(1, "Amit Patel", email="amit1@x.com",
              current_employer="Reliance", location="Surat"),
        _cand(2, "Amit Patel", current_employer="Tata", location="Mumbai",
              source_ref="apna:u-1"),
    ]
    res = find_duplicates(cands)
    for ids in _contact_ids(res):
        assert ids != frozenset({1, 2})


def test_different_people_same_employer_different_name_NOT_flagged():
    """Same employer but different names — coworkers, not duplicates."""
    cands = [
        _cand(1, "Neha Joshi", current_employer="Kalyan Jewellers", location="Surat"),
        _cand(2, "Rohan Desai", current_employer="Kalyan Jewellers", location="Surat",
              source_ref="apna:u-2"),
    ]
    res = find_duplicates(cands)
    assert _contact_ids(res) == []


def test_single_token_name_not_enough():
    """A bare first name like 'Raj' must not match even with shared employer."""
    cands = [
        _cand(1, "Raj", current_employer="Shobha Gems", location="Surat"),
        _cand(2, "Raj", current_employer="Shobha Gems", location="Surat",
              source_ref="apna:u-3"),
    ]
    res = find_duplicates(cands)
    assert _contact_ids(res) == []


def test_name_match_is_case_and_punctuation_insensitive():
    cands = [
        _cand(1, "Dr. Meera  Nair", email="meera@x.com", location="Surat"),
        _cand(2, "dr meera nair", location="surat", source_ref="apna:u-7"),
    ]
    res = find_duplicates(cands)
    assert frozenset({1, 2}) in _contact_ids(res)
