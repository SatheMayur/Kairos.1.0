"""Tests for the outreach service (dev-mode mock sending)."""
import pytest
import pytest_asyncio
from app.models.candidate import Candidate, CandidateSource
from app.models.job import Job
from app.models.outreach import OutreachChannel, OutreachStatus, OutreachType
from app.services.outreach import send_outreach


@pytest_asyncio.fixture
async def candidate(db_session):
    c = Candidate(
        name="Priya Sharma",
        email="priya@example.com",
        phone="+919876543210",
        whatsapp="+919876543210",
        skills=["Python", "FastAPI"],
        experience_years=3.0,
        expected_salary=1_000_000,
        location="Bangalore",
        source=CandidateSource.MOCK,
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def job(db_session):
    j = Job(
        title="Python Developer",
        company="Test Corp",
        skills=["Python", "FastAPI"],
        salary_min=800_000,
        salary_max=1_200_000,
        location="Bangalore",
    )
    db_session.add(j)
    await db_session.flush()
    return j


@pytest.mark.asyncio
async def test_initial_contact_email(candidate, job, db_session):
    log = await send_outreach(
        candidate=candidate,
        job=job,
        channel=OutreachChannel.EMAIL,
        outreach_type=OutreachType.INITIAL_CONTACT,
        db=db_session,
    )
    assert log.id is not None
    assert log.status == OutreachStatus.SENT
    assert log.subject is not None
    assert candidate.name in log.message


@pytest.mark.asyncio
async def test_whatsapp_outreach(candidate, job, db_session):
    log = await send_outreach(
        candidate=candidate,
        job=job,
        channel=OutreachChannel.WHATSAPP,
        outreach_type=OutreachType.INITIAL_CONTACT,
        db=db_session,
    )
    assert log.status == OutreachStatus.SENT
    assert log.channel == OutreachChannel.WHATSAPP


@pytest.mark.asyncio
async def test_outreach_no_contact_fails_gracefully():
    """Candidate with no email/phone should result in FAILED log, not an exception."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        c = Candidate(name="Ghost User", source=CandidateSource.MOCK)
        j = Job(title="Dev", company="Corp")
        db.add(c)
        db.add(j)
        await db.flush()
        log = await send_outreach(
            candidate=c,
            job=j,
            channel=OutreachChannel.EMAIL,
            outreach_type=OutreachType.INITIAL_CONTACT,
            db=db,
        )
        assert log.status == OutreachStatus.FAILED
        assert log.error_detail is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_sms_outreach(candidate, job, db_session):
    log = await send_outreach(
        candidate=candidate,
        job=job,
        channel=OutreachChannel.SMS,
        outreach_type=OutreachType.INITIAL_CONTACT,
        db=db_session,
    )
    assert log.status == OutreachStatus.SENT
