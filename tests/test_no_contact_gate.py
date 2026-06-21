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


def test_has_email_and_phone_requires_both():
    from app.services.data_quality import has_email_and_phone
    # Both present → ok
    assert has_email_and_phone(email="a@b.com", phone="9876543210")
    assert has_email_and_phone(email="a@b.com", whatsapp="+91 98765 43210")
    # Only one → not enough for sourcing
    assert not has_email_and_phone(email="a@b.com")            # no phone
    assert not has_email_and_phone(phone="9876543210")         # no email
    assert not has_email_and_phone(email="a@b.com", phone="NA")  # phone invalid
    assert not has_email_and_phone(email="bad", phone="9876543210")  # email invalid


@pytest.mark.asyncio
async def test_sourcing_requires_email_and_phone(db_session, mock_adapters):
    """Sourcing skips candidates missing either phone or email."""
    from app.models.job import Job
    from app.services.sourcing import source_candidates_for_job
    from app.adapters import registry as reg

    # A custom adapter returning three candidates: both / email-only / phone-only.
    class _Stub:
        async def search(self, **kw):
            return [
                RawCandidate(name="Both", source=CandidateSource.NAUKRI,
                             email="both@x.com", phone="9876543210", skills=["AutoCAD"]),
                RawCandidate(name="EmailOnly", source=CandidateSource.NAUKRI,
                             email="e@x.com", skills=["AutoCAD"]),
                RawCandidate(name="PhoneOnly", source=CandidateSource.NAUKRI,
                             phone="9876500000", skills=["AutoCAD"]),
            ]
    reg._registry = {"stub": _Stub()}
    try:
        job = Job(title="Design Engineer", company="KGL", skills=["AutoCAD"], location=None)
        db_session.add(job); await db_session.flush()
        await source_candidates_for_job(job, db_session)
        from sqlalchemy import select
        names = {c.name for c in (await db_session.execute(select(Candidate))).scalars().all()}
        assert "Both" in names
        assert "EmailOnly" not in names   # missing phone → not sourced
        assert "PhoneOnly" not in names   # missing email → not sourced
    finally:
        reg.reset_registry()


@pytest.mark.asyncio
async def test_import_skips_no_contact(db_session, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "imports_enabled", True)  # exercise the pipeline logic
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
async def test_imports_disabled_by_default(db_session):
    """CSV/Apna/batch imports are turned off — the pipeline refuses with 403."""
    from fastapi import HTTPException
    from app.api.import_csv import _run_import_pipeline
    job = Job(title="X", company="K"); db_session.add(job); await db_session.flush()
    raw = RawCandidate(name="Anyone", source=CandidateSource.NAUKRI,
                       email="a@b.com", phone="9876543210")
    with pytest.raises(HTTPException) as ei:
        await _run_import_pipeline([raw], job.id, auto_outreach=False, db=db_session)
    assert ei.value.status_code == 403
    assert "turned off" in ei.value.detail.lower()


@pytest.mark.asyncio
async def test_manual_add_no_contact_refused(client):
    r = await client.post("/api/v1/candidates", json={"name": "Ghost"})
    assert r.status_code == 422
    assert "contacted" in r.json()["detail"].lower()
    r2 = await client.post("/api/v1/candidates", json={"name": "Real", "phone": "9876543210"})
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_ingest_applicants(client, db_session):
    from app.models.job import Job
    job = Job(title="Design Engineer", company="KGL", skills=["AutoCAD"])
    db_session.add(job); await db_session.flush(); await db_session.commit()

    body = {"job_id": job.id, "applicants": [
        {"name": "Dev Saini", "phone": "9876543210", "source": "WORKINDIA",
         "current_role": "CAD Designer", "experience_years": 1.0},          # reachable (phone)
        {"name": "No Contact Guy", "source": "WORKINDIA"},                    # no contact → skipped
    ]}
    r = await client.post("/api/v1/candidates/ingest-applicants", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["added"] == 1
    assert d["skipped_no_contact"] == 1
    assert d["scored"] == 1          # the reachable one got scored against the job

    from sqlalchemy import select
    names = {c.name for c in (await db_session.execute(select(Candidate))).scalars().all()}
    assert "Dev Saini" in names
    assert "No Contact Guy" not in names


@pytest.mark.asyncio
async def test_ingest_applicants_dedupes(client, db_session):
    db_session.add(Candidate(name="Existing", email="dev@x.com", source=CandidateSource.WORKINDIA))
    await db_session.commit()
    body = {"applicants": [{"name": "Dev (again)", "email": "dev@x.com", "phone": "9876543210"}]}
    d = (await client.post("/api/v1/candidates/ingest-applicants", json=body)).json()
    assert d["duplicates"] == 1 and d["added"] == 0


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
