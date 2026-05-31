"""Integration tests for sourcing service (uses mock adapters)."""
import pytest
import pytest_asyncio
from sqlalchemy import select
from app.models.job import Job
from app.models.candidate import Candidate
from app.models.shortlist import ShortlistEntry
from app.services.sourcing import source_candidates_for_job


@pytest_asyncio.fixture
async def sample_job(db_session):
    job = Job(
        title="Python Developer",
        company="Test Co",
        skills=["Python", "FastAPI"],
        experience_min=2.0,
        experience_max=5.0,
        salary_min=800_000,
        salary_max=1_200_000,
        location="Bangalore",
    )
    db_session.add(job)
    await db_session.flush()
    return job


@pytest.mark.asyncio
async def test_sourcing_creates_candidates(sample_job, db_session):
    entries = await source_candidates_for_job(sample_job, db_session)
    assert len(entries) > 0

    result = await db_session.execute(select(Candidate))
    candidates = result.scalars().all()
    assert len(candidates) > 0


@pytest.mark.asyncio
async def test_sourcing_creates_shortlist_entries(sample_job, db_session):
    entries = await source_candidates_for_job(sample_job, db_session)
    assert all(e.job_id == sample_job.id for e in entries)
    assert all(0.0 <= e.score <= 100.0 for e in entries)


@pytest.mark.asyncio
async def test_sourcing_idempotent(sample_job, db_session):
    """Running sourcing twice should not duplicate candidates."""
    entries1 = await source_candidates_for_job(sample_job, db_session)
    entries2 = await source_candidates_for_job(sample_job, db_session)
    # Second run: all candidates already exist, no new shortlist entries
    assert len(entries2) == 0


@pytest.mark.asyncio
async def test_shortlist_entries_have_score_breakdown(sample_job, db_session):
    entries = await source_candidates_for_job(sample_job, db_session)
    for entry in entries:
        assert entry.score_breakdown is not None
        assert "skills_match" in entry.score_breakdown
