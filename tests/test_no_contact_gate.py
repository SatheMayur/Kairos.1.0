"""Tests for the no-contact ingestion gate + cleanup.

Rule (owner): a candidate with no usable email and no usable phone/WhatsApp can
never be contacted, so they must not be added to the system — at import, at
sourcing, or via manual add. Existing ones can be cleaned up safely.
"""
import pytest

from app.adapters.base import RawCandidate
from app.models.candidate import Candidate, CandidateSource
from app.models.job import Job
from app.api.import_csv import _run_import_pipeline
from app.services.data_quality import is_reachable_contact


def test_is_reachable_contact():
    assert is_reachable_contact(email="a@b.com")
    assert is_reachable_contact(phone="9876543210")
    assert is_reachable_contact(whatsapp="+91 98765 43210")
    # None of these are contactable:
    assert not is_reachable_contact()
    assert not is_reachable_contact(email="not-an-email", phone="NA")
    assert not is_reachable_contact(phone="123")           # too short
    assert not is_reachable_contact(phone="04012345678")   # landline-ish, not a mobile


@pytest.mark.asyncio
async def test_import_skips_no_contact(db_session):
    job = Job(title="Design Engineer", company="KGL")
    db_session.add(job); await db_session.flush()
    raws = [
        RawCandidate(name="HasPhone", source=CandidateSource.APNA, phone="9876543210", skills=["AutoCAD"]),
        RawCandidate(name="LockedApna", source=CandidateSource.APNA, skills=["AutoCAD"]),  # no contact
    ]
    res = await _run_import_pipeline(raws, job.id, auto_outreach=False, db=db_session)
    assert res.skipped_no_contact == 1
    assert res.inserted == 1
    from sqlalchemy import select
    names = {c.name for c in (await db_session.execute(select(Candidate))).scalars().all()}
    assert "HasPhone" in names
    assert "LockedApna" not in names          # never added


@pytest.mark.asyncio
async def test_manual_add_no_contact_refused(client):
    r = await client.post("/api/v1/candidates", json={"name": "Ghost"})
    assert r.status_code == 422
    assert "contacted" in r.json()["detail"].lower()
    r2 = await client.post("/api/v1/candidates", json={"name": "Real", "phone": "9876543210"})
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_cleanup_no_contact_dry_run_then_confirm(client, db_session):
    db_session.add_all([
        Candidate(name="Ghost", source=CandidateSource.APNA),                       # no contact
        Candidate(name="Reachable", source=CandidateSource.NAUKRI, phone="9876543210"),
    ])
    await db_session.commit()

    dry = (await client.post("/api/v1/candidates/cleanup-no-contact")).json()
    assert dry["dry_run"] is True
    assert dry["would_remove"] == 1
    assert any(c["name"] == "Ghost" for c in dry["candidates"])

    done = (await client.post("/api/v1/candidates/cleanup-no-contact?confirm=true")).json()
    assert done["removed"] == 1

    from sqlalchemy import select
    names = {c.name for c in (await db_session.execute(select(Candidate))).scalars().all()}
    assert "Ghost" not in names
    assert "Reachable" in names
