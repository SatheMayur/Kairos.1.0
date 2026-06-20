"""Data-integrity / business-logic guards.

Covers the fixes for:
  • one entry per (candidate, job) + upsert on re-score
  • is_reachable() true/false
  • an unreachable candidate is never marked CONTACTED
  • INTERVIEW_SCHEDULED requires a backing interview
  • dedupe keeps the most-advanced entry
"""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from sqlalchemy import select

from app.models.candidate import Candidate, CandidateSource
from app.models.job import Job
from app.models.interview import Interview, InterviewStatus
from app.models.outreach import OutreachLog, OutreachChannel, OutreachStatus, OutreachType
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.services.data_quality import is_reachable
from app.database import _dedupe_and_reconcile_shortlist


@pytest_asyncio.fixture
async def job(db_session):
    j = Job(title="Design Engineer", company="K. Girdharlal International",
            skills=["SolidWorks"], location="Surat")
    db_session.add(j)
    await db_session.flush()
    return j


@pytest_asyncio.fixture
async def legacy_session():
    """A DB built WITHOUT the unique (candidate_id, job_id) constraint.

    Mirrors the live database before this fix, where duplicate shortlist rows
    were possible (candidate 14 had two entries for job 3). Lets the dedupe
    tests insert duplicates the way they really exist in production, then verify
    the startup cleanup collapses them.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy import MetaData, Table
    from app.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    # Build a metadata copy of every table, but drop the unique constraint from
    # the shortlist table so duplicates can be inserted.
    legacy_meta = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name == "shortlist":
            cols = [c._copy() for c in table.columns]
            Table("shortlist", legacy_meta, *cols)  # no UniqueConstraint copied
        else:
            table.to_metadata(legacy_meta)

    async with engine.begin() as conn:
        await conn.run_sync(legacy_meta.create_all)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def legacy_job(legacy_session):
    j = Job(title="Design Engineer", company="K. Girdharlal International",
            skills=["SolidWorks"], location="Surat")
    legacy_session.add(j)
    await legacy_session.flush()
    return j


# ── is_reachable ─────────────────────────────────────────────────────────────

def test_is_reachable_with_valid_email():
    c = Candidate(name="A", email="a@example.com", source=CandidateSource.MANUAL)
    assert is_reachable(c) is True


def test_is_reachable_with_valid_mobile():
    c = Candidate(name="B", phone="+91 98765 43210", source=CandidateSource.MANUAL)
    assert is_reachable(c) is True


def test_is_reachable_with_whatsapp_only():
    c = Candidate(name="C", whatsapp="9123456780", source=CandidateSource.MANUAL)
    assert is_reachable(c) is True


def test_not_reachable_no_contact():
    c = Candidate(name="D", source=CandidateSource.APNA)
    assert is_reachable(c) is False


def test_not_reachable_junk_phone_no_email():
    # "NA" and landlines do not normalize to a valid Indian mobile.
    c = Candidate(name="E", phone="NA", source=CandidateSource.APNA)
    assert is_reachable(c) is False
    c2 = Candidate(name="F", phone="0612345678", source=CandidateSource.APNA)
    assert is_reachable(c2) is False


def test_not_reachable_bad_email_no_phone():
    c = Candidate(name="G", email="not-an-email", source=CandidateSource.MANUAL)
    assert is_reachable(c) is False


# ── one entry per (candidate, job) + upsert on re-score ──────────────────────

@pytest.mark.asyncio
async def test_rescore_updates_single_row(job, db_session):
    from app.services.sourcing import _score_and_shortlist

    cand = Candidate(name="Rescore", email="rescore@example.com",
                     skills=["SolidWorks"], experience_years=2.0,
                     location="Surat", source=CandidateSource.NAUKRI)
    db_session.add(cand)
    await db_session.flush()

    e1 = await _score_and_shortlist(cand, job, db_session)
    await db_session.flush()
    assert e1 is not None

    # Second score of the same (candidate, job) must update, not insert.
    e2 = await _score_and_shortlist(cand, job, db_session)
    await db_session.flush()
    assert e2 is not None
    assert e2.id == e1.id

    rows = (await db_session.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.candidate_id == cand.id,
            ShortlistEntry.job_id == job.id,
        )
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_rescore_does_not_downgrade_advanced(job, db_session):
    """A candidate who already advanced (e.g. INTERESTED) keeps their status on re-score."""
    from app.services.sourcing import _score_and_shortlist

    cand = Candidate(name="Advanced", email="adv@example.com",
                     skills=["SolidWorks"], location="Surat",
                     source=CandidateSource.NAUKRI)
    db_session.add(cand)
    await db_session.flush()

    entry = ShortlistEntry(job_id=job.id, candidate_id=cand.id, score=90.0,
                           status=ShortlistStatus.INTERESTED)
    db_session.add(entry)
    await db_session.flush()

    updated = await _score_and_shortlist(cand, job, db_session)
    await db_session.flush()
    assert updated.id == entry.id
    # Status preserved (not moved back to PENDING/SHORTLISTED/REJECTED).
    assert updated.status == ShortlistStatus.INTERESTED


# ── unreachable candidate is not ADDED on import ────────────────────────────

@pytest.mark.asyncio
async def test_unreachable_candidate_not_added_on_import(job, db_session, mock_adapters, monkeypatch):
    from app.adapters.base import RawCandidate
    from app.api.import_csv import _run_import_pipeline
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "imports_enabled", True)  # exercise the pipeline logic

    # No email, hidden phone (Apna-style) → unreachable. Per the data-quality
    # gate it must NOT be added to the system at all (not just "not contacted").
    raw = RawCandidate(name="Locked Apna", source=CandidateSource.APNA,
                       skills=["SolidWorks"], location="Surat",
                       source_ref="apna:locked-1")
    result = await _run_import_pipeline([raw], job.id, auto_outreach=True, db=db_session)
    assert result.outreach_queued == 0
    assert result.skipped_no_contact == 1
    assert result.inserted == 0

    entry = (await db_session.execute(
        select(ShortlistEntry).where(ShortlistEntry.job_id == job.id)
    )).scalars().first()
    assert entry is None
    cand = (await db_session.execute(
        select(Candidate).where(Candidate.name == "Locked Apna")
    )).scalars().first()
    assert cand is None


# ── INTERVIEW_SCHEDULED requires a backing interview ─────────────────────────

@pytest.mark.asyncio
async def test_interview_scheduled_without_interview_is_reconciled(job, db_session):
    cand = Candidate(name="Ghost Schedule", email="ghost@example.com",
                     source=CandidateSource.NAUKRI)
    db_session.add(cand)
    await db_session.flush()

    # Marked scheduled but NO interview row → should be downgraded.
    entry = ShortlistEntry(job_id=job.id, candidate_id=cand.id, score=80.0,
                           status=ShortlistStatus.INTERVIEW_SCHEDULED)
    db_session.add(entry)
    # A sent outreach log exists → should fall back to CONTACTED.
    db_session.add(OutreachLog(candidate_id=cand.id, job_id=job.id,
                               channel=OutreachChannel.EMAIL,
                               outreach_type=OutreachType.INITIAL_CONTACT,
                               message="hi", status=OutreachStatus.SENT))
    await db_session.commit()

    await _dedupe_and_reconcile_shortlist(db_session)
    await db_session.refresh(entry)
    assert entry.status == ShortlistStatus.CONTACTED


@pytest.mark.asyncio
async def test_interview_scheduled_with_interview_is_kept(job, db_session):
    cand = Candidate(name="Real Schedule", email="real@example.com",
                     source=CandidateSource.NAUKRI)
    db_session.add(cand)
    await db_session.flush()

    entry = ShortlistEntry(job_id=job.id, candidate_id=cand.id, score=80.0,
                           status=ShortlistStatus.INTERVIEW_SCHEDULED)
    db_session.add(entry)
    db_session.add(Interview(candidate_id=cand.id, job_id=job.id,
                             status=InterviewStatus.CONFIRMED,
                             scheduled_at=datetime.utcnow() + timedelta(days=1)))
    await db_session.commit()

    await _dedupe_and_reconcile_shortlist(db_session)
    await db_session.refresh(entry)
    assert entry.status == ShortlistStatus.INTERVIEW_SCHEDULED


@pytest.mark.asyncio
async def test_interview_scheduled_no_interview_no_outreach_falls_to_shortlisted(job, db_session):
    cand = Candidate(name="No Trail", email="notrail@example.com",
                     source=CandidateSource.NAUKRI)
    db_session.add(cand)
    await db_session.flush()
    entry = ShortlistEntry(job_id=job.id, candidate_id=cand.id, score=80.0,
                           status=ShortlistStatus.INTERVIEW_SCHEDULED)
    db_session.add(entry)
    await db_session.commit()

    await _dedupe_and_reconcile_shortlist(db_session)
    await db_session.refresh(entry)
    assert entry.status == ShortlistStatus.SHORTLISTED


# ── dedupe keeps the most-advanced entry ─────────────────────────────────────

@pytest.mark.asyncio
async def test_dedupe_keeps_most_advanced(legacy_job, legacy_session):
    """Mirrors the live bug: candidate has two entries for one job (scores 92 & 63)."""
    cand = Candidate(name="Dup Candidate", email="dup@example.com",
                     source=CandidateSource.NAUKRI)
    legacy_session.add(cand)
    await legacy_session.flush()

    # Two rows: score 92 (PENDING) and score 63 (CONTACTED). The most-ADVANCED
    # status (CONTACTED) must win even though its score is lower.
    hi = ShortlistEntry(job_id=legacy_job.id, candidate_id=cand.id, score=92.0,
                        status=ShortlistStatus.PENDING)
    lo = ShortlistEntry(job_id=legacy_job.id, candidate_id=cand.id, score=63.0,
                        status=ShortlistStatus.CONTACTED)
    legacy_session.add_all([hi, lo])
    await legacy_session.commit()

    await _dedupe_and_reconcile_shortlist(legacy_session)

    rows = (await legacy_session.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.candidate_id == cand.id,
            ShortlistEntry.job_id == legacy_job.id,
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == ShortlistStatus.CONTACTED
    assert rows[0].score == 63.0


@pytest.mark.asyncio
async def test_dedupe_tiebreak_by_score_then_id(legacy_job, legacy_session):
    """Same status → highest score wins; same score → lowest id wins."""
    cand = Candidate(name="Tie", email="tie@example.com", source=CandidateSource.NAUKRI)
    legacy_session.add(cand)
    await legacy_session.flush()

    a = ShortlistEntry(job_id=legacy_job.id, candidate_id=cand.id, score=70.0,
                       status=ShortlistStatus.SHORTLISTED)
    b = ShortlistEntry(job_id=legacy_job.id, candidate_id=cand.id, score=85.0,
                       status=ShortlistStatus.SHORTLISTED)
    legacy_session.add_all([a, b])
    await legacy_session.commit()

    await _dedupe_and_reconcile_shortlist(legacy_session)
    rows = (await legacy_session.execute(
        select(ShortlistEntry).where(ShortlistEntry.candidate_id == cand.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].score == 85.0
