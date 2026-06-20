"""Tests for safe job management — guarded delete, archive, reopen.

The bug being fixed: DELETE /jobs/{id} used to delete only the job row, leaving
shortlist/outreach/interview/conversation rows that FK to it — a foreign-key
violation (500) on Postgres. Delete must now either refuse (when candidates exist)
or cascade cleanly.
"""
import pytest

from app.models.job import Job, JobStatus
from app.models.candidate import Candidate, CandidateSource
from app.models.shortlist import ShortlistEntry, ShortlistStatus


async def _job_with_candidate(db):
    job = Job(title="Design Engineer", company="KGL")
    db.add(job); await db.flush()
    c = Candidate(name="Aarti", phone="9876543210", source=CandidateSource.NAUKRI)
    db.add(c); await db.flush()
    db.add(ShortlistEntry(job_id=job.id, candidate_id=c.id, score=80,
                          status=ShortlistStatus.SHORTLISTED))
    await db.flush()
    return job, c


@pytest.mark.asyncio
async def test_delete_job_with_candidates_is_refused(client, db_session):
    job, c = await _job_with_candidate(db_session)
    await db_session.commit()
    r = await client.delete(f"/api/v1/jobs/{job.id}")
    assert r.status_code == 409
    assert "archive" in r.json()["detail"].lower()
    # Job still there.
    assert (await client.get(f"/api/v1/jobs/{job.id}")).status_code == 200


@pytest.mark.asyncio
async def test_force_delete_cascades(client, db_session):
    job, c = await _job_with_candidate(db_session)
    await db_session.commit()
    r = await client.delete(f"/api/v1/jobs/{job.id}?force=true")
    assert r.status_code == 204
    assert (await client.get(f"/api/v1/jobs/{job.id}")).status_code == 404
    # Dependent shortlist row is gone (no orphan / FK violation).
    from sqlalchemy import select, func
    n = (await db_session.execute(
        select(func.count()).select_from(ShortlistEntry).where(ShortlistEntry.job_id == job.id)
    )).scalar_one()
    assert n == 0


@pytest.mark.asyncio
async def test_delete_empty_job_succeeds(client, db_session):
    job = Job(title="Empty Role")
    db_session.add(job); await db_session.flush()
    await db_session.commit()
    r = await client.delete(f"/api/v1/jobs/{job.id}")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_archive_then_reopen(client, db_session):
    job, c = await _job_with_candidate(db_session)
    await db_session.commit()
    a = await client.post(f"/api/v1/jobs/{job.id}/archive")
    assert a.status_code == 200
    assert a.json()["status"] == "CLOSED"
    rr = await client.post(f"/api/v1/jobs/{job.id}/reopen")
    assert rr.status_code == 200
    assert rr.json()["status"] == "ACTIVE"
