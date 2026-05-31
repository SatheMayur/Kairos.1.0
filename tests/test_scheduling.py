"""Tests for scheduling service."""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from app.models.candidate import Candidate, CandidateSource
from app.models.job import Job
from app.models.interview import Interview, InterviewStatus
from app.services.scheduling import (
    propose_interview_slots,
    confirm_interview_slot,
    generate_slots,
    send_interview_reminders,
)


@pytest_asyncio.fixture
async def candidate(db_session):
    c = Candidate(
        name="Arjun Patel",
        email="arjun@example.com",
        phone="+919876543211",
        whatsapp="+919876543211",
        skills=["Python"],
        source=CandidateSource.MOCK,
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def job(db_session):
    j = Job(title="Backend Engineer", company="ACME Ltd")
    db_session.add(j)
    await db_session.flush()
    return j


def test_generate_slots_returns_business_hours():
    slots = generate_slots(num_slots=3)
    assert len(slots) == 3
    for slot in slots:
        assert slot > datetime.utcnow()
        assert slot.weekday() < 5  # Mon–Fri


@pytest.mark.asyncio
async def test_propose_creates_interview(candidate, job, db_session):
    interview = await propose_interview_slots(
        candidate=candidate,
        job=job,
        db=db_session,
    )
    assert interview.id is not None
    assert interview.status == InterviewStatus.PROPOSED
    assert interview.confirmation_token is not None
    assert interview.proposed_slots is not None


@pytest.mark.asyncio
async def test_confirm_slot_updates_status(candidate, job, db_session):
    interview = await propose_interview_slots(
        candidate=candidate,
        job=job,
        db=db_session,
    )
    confirmed = await confirm_interview_slot(
        token=interview.confirmation_token,
        selected_slot_index=0,
        db=db_session,
    )
    assert confirmed is not None
    assert confirmed.status == InterviewStatus.CONFIRMED
    assert confirmed.meet_link is not None


@pytest.mark.asyncio
async def test_invalid_token_returns_none(db_session):
    result = await confirm_interview_slot(
        token="invalid-token-xyz",
        selected_slot_index=0,
        db=db_session,
    )
    assert result is None


@pytest.mark.asyncio
async def test_reminders_sent_for_upcoming(candidate, job, db_session):
    # Create a confirmed interview scheduled 12h from now
    interview = Interview(
        candidate_id=candidate.id,
        job_id=job.id,
        status=InterviewStatus.CONFIRMED,
        scheduled_at=datetime.utcnow() + timedelta(hours=12),
        reminder_sent=False,
    )
    db_session.add(interview)
    await db_session.flush()

    count = await send_interview_reminders(db_session)
    assert count == 1
    assert interview.reminder_sent is True


@pytest.mark.asyncio
async def test_reminders_not_sent_twice(candidate, job, db_session):
    interview = Interview(
        candidate_id=candidate.id,
        job_id=job.id,
        status=InterviewStatus.CONFIRMED,
        scheduled_at=datetime.utcnow() + timedelta(hours=12),
        reminder_sent=True,  # already sent
    )
    db_session.add(interview)
    await db_session.flush()

    count = await send_interview_reminders(db_session)
    assert count == 0
