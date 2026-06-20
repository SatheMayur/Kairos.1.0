"""Tests for the centralized auto-outreach service + on-add automation.

Rules under test:
  • Only PENDING/SHORTLISTED entries are auto-contacted.
  • Unreachable candidates (no usable phone/email) are skipped, never messaged,
    and never flipped to CONTACTED.
  • A real send flips the entry to CONTACTED; re-running is idempotent.
"""
import pytest

from app.models.candidate import Candidate, CandidateSource
from app.models.job import Job
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.outreach import OutreachChannel
from app.services.auto_outreach import contact_job_entries, contact_candidate_now


@pytest.fixture(autouse=True)
def _mock_delivery(monkeypatch):
    """Force the local mock send path: no Sheets queue (needs the google pkg, not
    installed in CI) and no SMTP password → _send_email returns a mock id (SENT).
    WhatsApp already mocks in development. Mirrors prod where delivery is real."""
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "use_sheets_email_queue", False)
    monkeypatch.setattr(s, "smtp_password", "")
    monkeypatch.setattr(s, "app_env", "development")
    monkeypatch.setattr(s, "outreach_delay_seconds", 0)


async def _setup(db, *, reachable=True, status=ShortlistStatus.SHORTLISTED):
    job = Job(title="Design Engineer", company="KGL", skills=["AutoCAD"])
    db.add(job); await db.flush()
    c = Candidate(
        name="Aarti", source=CandidateSource.NAUKRI, skills=["AutoCAD"],
        phone="9876543210" if reachable else None,
        email="aarti@example.com" if reachable else None,
    )
    db.add(c); await db.flush()
    e = ShortlistEntry(job_id=job.id, candidate_id=c.id, score=85, status=status)
    db.add(e); await db.flush()
    return job, c, e


@pytest.mark.asyncio
async def test_contact_job_entries_sends_and_marks_contacted(db_session):
    job, c, e = await _setup(db_session, reachable=True)
    res = await contact_job_entries(db_session, job, [e], channel=OutreachChannel.EMAIL)
    assert res["sent"] == 1
    assert res["contacted"] == 1
    assert e.status == ShortlistStatus.CONTACTED


@pytest.mark.asyncio
async def test_unreachable_is_skipped_not_contacted(db_session):
    job, c, e = await _setup(db_session, reachable=False)
    res = await contact_job_entries(db_session, job, [e], channel=OutreachChannel.EMAIL)
    assert res["sent"] == 0
    assert res["contacted"] == 0
    assert res["skipped_unreachable"] == 1
    # Must remain SHORTLISTED (surfaced in Needs Fixing), never falsely contacted.
    assert e.status == ShortlistStatus.SHORTLISTED


@pytest.mark.asyncio
async def test_already_advanced_entry_is_not_recontacted(db_session):
    job, c, e = await _setup(db_session, reachable=True, status=ShortlistStatus.INTERVIEW_SCHEDULED)
    res = await contact_job_entries(db_session, job, [e], channel=OutreachChannel.EMAIL)
    assert res["sent"] == 0
    assert e.status == ShortlistStatus.INTERVIEW_SCHEDULED


@pytest.mark.asyncio
async def test_idempotent_second_run_contacts_nobody(db_session):
    job, c, e = await _setup(db_session, reachable=True)
    await contact_job_entries(db_session, job, [e], channel=OutreachChannel.EMAIL)
    # second pass — e is now CONTACTED, so it's no longer eligible
    res2 = await contact_job_entries(db_session, job, [e], channel=OutreachChannel.EMAIL)
    assert res2["sent"] == 0


@pytest.mark.asyncio
async def test_contact_candidate_now(db_session):
    job, c, e = await _setup(db_session, reachable=True)
    res = await contact_candidate_now(db_session, c.id)
    assert res["contacted"] == 1
    assert e.status == ShortlistStatus.CONTACTED


@pytest.mark.asyncio
async def test_contact_candidate_now_no_pipeline(db_session):
    c = Candidate(name="Loner", source=CandidateSource.MANUAL, phone="9876543210")
    db_session.add(c); await db_session.flush()
    res = await contact_candidate_now(db_session, c.id)
    assert res["reason"] == "no_open_pipeline_entry"


# ── API endpoints ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contact_all_for_job_endpoint(client, db_session):
    job, c, e = await _setup(db_session, reachable=True)
    await db_session.commit()
    r = await client.post(f"/api/v1/jobs/{job.id}/contact-all")
    assert r.status_code == 200
    assert r.json()["contacted"] == 1


@pytest.mark.asyncio
async def test_contact_candidate_endpoint_refuses_unreachable(client, db_session):
    job, c, e = await _setup(db_session, reachable=False)
    await db_session.commit()
    r = await client.post(f"/api/v1/candidates/{c.id}/contact")
    assert r.status_code == 422
    assert "contact info" in r.json()["detail"].lower()
