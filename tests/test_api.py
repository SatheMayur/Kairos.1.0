"""End-to-end API tests."""
import pytest


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_create_and_get_job(client):
    payload = {
        "title": "Python Developer",
        "company": "Test Corp",
        "skills": ["Python", "FastAPI"],
        "experience_min": 2.0,
        "experience_max": 5.0,
        "salary_min": 800000,
        "salary_max": 1200000,
        "location": "Bangalore",
    }
    r = await client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 201
    job = r.json()
    assert job["title"] == "Python Developer"
    job_id = job["id"]

    r2 = await client.get(f"/api/v1/jobs/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == job_id


@pytest.mark.asyncio
async def test_create_job_from_jd(client):
    jd = "Senior Python Developer\nRequirements: 3-5 years Python, FastAPI, SQL\nSalary: ₹15-20 LPA\nLocation: Bangalore\n"
    r = await client.post("/api/v1/jobs/from-jd", params={"raw_jd": jd})
    assert r.status_code == 201
    job = r.json()
    assert job["id"] is not None


@pytest.mark.asyncio
async def test_analyze_jd_endpoint(client):
    jd = "Software Engineer\n3-6 years experience\nPython, Docker, AWS\nSalary: ₹12-18 LPA"
    r = await client.post("/api/v1/jobs/analyze-jd", params={"raw_jd": jd})
    assert r.status_code == 200
    data = r.json()
    assert "Python" in [s for s in data["skills"]]


@pytest.mark.asyncio
async def test_create_and_list_candidates(client):
    payload = {
        "name": "Priya Sharma",
        "email": "priya.test@example.com",
        "skills": ["Python", "SQL"],
        "experience_years": 3.0,
        "expected_salary": 1000000,
        "location": "Bangalore",
    }
    r = await client.post("/api/v1/candidates", json=payload)
    assert r.status_code == 201
    c = r.json()
    assert c["name"] == "Priya Sharma"

    r2 = await client.get("/api/v1/candidates")
    assert r2.status_code == 200
    assert len(r2.json()) >= 1


@pytest.mark.asyncio
async def test_score_candidate_for_job(client):
    # Create job
    job_r = await client.post("/api/v1/jobs", json={
        "title": "Python Dev",
        "skills": ["Python"],
        "experience_min": 2.0,
        "experience_max": 5.0,
        "salary_min": 800000,
        "salary_max": 1200000,
        "location": "Bangalore",
    })
    job_id = job_r.json()["id"]

    # Create candidate
    cand_r = await client.post("/api/v1/candidates", json={
        "name": "Test Candidate",
        "email": "test.cand@example.com",
        "skills": ["Python", "FastAPI"],
        "experience_years": 3.0,
        "expected_salary": 1000000,
        "location": "Bangalore",
    })
    cand_id = cand_r.json()["id"]

    # Score
    r = await client.post(f"/api/v1/shortlist/score/{job_id}/{cand_id}")
    assert r.status_code == 200
    entry = r.json()
    assert 0 <= entry["score"] <= 100
    assert entry["score_breakdown"] is not None


@pytest.mark.asyncio
async def test_trigger_sourcing(client, mock_adapters):
    job_r = await client.post("/api/v1/jobs", json={
        "title": "Python Dev",
        "skills": ["Python"],
        "location": "Bangalore",
    })
    job_id = job_r.json()["id"]
    r = await client.post(f"/api/v1/jobs/{job_id}/source")
    assert r.status_code == 200
    data = r.json()
    assert "sourced" in data
    assert data["sourced"] > 0


@pytest.mark.asyncio
async def test_list_shortlist_with_filter(client):
    r = await client.get("/api/v1/shortlist?min_score=50")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_shortlist_reachable_flag_and_filter(client, db_session):
    """GET /shortlist tags each entry with `reachable`, and ?reachable_only=true
    hides candidates with no valid email or mobile while keeping contactable ones."""
    from app.models.candidate import Candidate, CandidateSource
    from app.models.job import Job, JobStatus
    from app.models.shortlist import ShortlistEntry, ShortlistStatus

    job = Job(title="Design Engineer", company="KGI", location="Surat", status=JobStatus.ACTIVE)
    db_session.add(job)
    await db_session.flush()

    # Reachable: valid Indian mobile, no email.
    reachable = Candidate(name="Tirth B.", phone="9876543210", source=CandidateSource.NAUKRI)
    # Reachable: valid email, no phone.
    by_email = Candidate(name="Email Only", email="cand@example.com", source=CandidateSource.NAUKRI)
    # Not reachable: junk phone, no email (e.g. locked Apna profile).
    no_contact = Candidate(name="No Contact", phone="NA", source=CandidateSource.APNA)
    db_session.add_all([reachable, by_email, no_contact])
    await db_session.flush()

    for c, score in ((reachable, 80), (by_email, 70), (no_contact, 60)):
        db_session.add(ShortlistEntry(
            job_id=job.id, candidate_id=c.id, score=score, status=ShortlistStatus.SHORTLISTED
        ))
    await db_session.flush()

    # Default: all entries returned, each carrying a `reachable` boolean.
    r = await client.get(f"/api/v1/shortlist?job_id={job.id}")
    assert r.status_code == 200
    entries = {e["candidate_id"]: e for e in r.json()}
    assert len(entries) == 3
    assert all("reachable" in e for e in entries.values())
    assert entries[reachable.id]["reachable"] is True
    assert entries[by_email.id]["reachable"] is True
    assert entries[no_contact.id]["reachable"] is False

    # reachable_only=true: the no-contact candidate is excluded.
    r2 = await client.get(f"/api/v1/shortlist?job_id={job.id}&reachable_only=true")
    assert r2.status_code == 200
    ids = {e["candidate_id"] for e in r2.json()}
    assert reachable.id in ids
    assert by_email.id in ids
    assert no_contact.id not in ids


@pytest.mark.asyncio
async def test_candidate_conversation_still_returns(client, db_session):
    """Conversation endpoint must keep returning successfully (timeline preview /
    failure_reason additions live in the profile timeline, not here)."""
    from app.models.candidate import Candidate, CandidateSource

    c = Candidate(name="Conv Person", phone="9876500000", source=CandidateSource.NAUKRI)
    db_session.add(c)
    await db_session.flush()

    r = await client.get(f"/api/v1/candidates/{c.id}/conversation")
    assert r.status_code == 200
    assert r.json()["exists"] is False


@pytest.mark.asyncio
async def test_candidate_profile_timeline_outreach_fields(client, db_session):
    """A failed outreach surfaces failure_reason + preview on its timeline item."""
    from app.models.candidate import Candidate, CandidateSource
    from app.models.job import Job, JobStatus
    from app.models.outreach import OutreachLog, OutreachChannel, OutreachStatus, OutreachType

    job = Job(title="HR Executive", company="KGI", location="Surat", status=JobStatus.ACTIVE)
    db_session.add(job)
    await db_session.flush()
    c = Candidate(name="Outreach Person", email="o@example.com", source=CandidateSource.NAUKRI)
    db_session.add(c)
    await db_session.flush()
    db_session.add(OutreachLog(
        candidate_id=c.id, job_id=job.id, channel=OutreachChannel.EMAIL,
        outreach_type=OutreachType.INITIAL_CONTACT, status=OutreachStatus.FAILED,
        message="Hi, we would love to chat about the HR Executive role at KGI.",
        error_detail="Mailbox does not exist",
    ))
    await db_session.flush()

    r = await client.get(f"/api/v1/candidates/{c.id}/profile")
    assert r.status_code == 200
    outreach_items = [t for t in r.json()["timeline"] if t["type"] == "outreach"]
    assert outreach_items
    item = outreach_items[0]
    assert item["failure_reason"] == "Mailbox does not exist"
    assert item["preview"] and item["preview"].startswith("Hi, we would love")


@pytest.mark.asyncio
async def test_list_interviews(client):
    r = await client.get("/api/v1/interviews")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_list_outreach(client):
    r = await client.get("/api/v1/outreach")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_merge_reassigns_conversation(db_session):
    """Merging a duplicate that has a WhatsApp conversation must move the
    conversation to the kept record — otherwise deleting the duplicate hits a
    foreign-key violation in Postgres (500) or orphans the row in SQLite."""
    from sqlalchemy import select
    from app.models.candidate import Candidate, CandidateSource
    from app.models.conversation import Conversation
    from app.api.candidates import merge_candidate

    keep = Candidate(name="Ravi Shah", phone="9000011111", source=CandidateSource.NAUKRI)
    dupe = Candidate(name="Ravi S.", phone="9000011111", source=CandidateSource.NAUKRI)
    db_session.add_all([keep, dupe])
    await db_session.flush()

    db_session.add(Conversation(
        candidate_id=dupe.id, job_id=1, collected={}, history=[], status="ACTIVE"
    ))
    await db_session.flush()

    res = await merge_candidate(keep.id, dupe.id, db_session)
    assert res["ok"] is True

    convs = (await db_session.execute(select(Conversation))).scalars().all()
    assert len(convs) == 1
    assert convs[0].candidate_id == keep.id  # reassigned, not orphaned

    cands = (await db_session.execute(select(Candidate))).scalars().all()
    assert len(cands) == 1 and cands[0].id == keep.id


@pytest.mark.asyncio
async def test_delete_candidate_with_history(db_session):
    """Deleting a candidate with dependent rows must clear them too, not crash
    on the foreign keys or leave orphans behind."""
    from sqlalchemy import select
    from app.models.candidate import Candidate, CandidateSource
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.conversation import Conversation
    from app.api.candidates import delete_candidate

    c = Candidate(name="Temp Person", phone="9111122223", source=CandidateSource.NAUKRI)
    db_session.add(c)
    await db_session.flush()
    db_session.add(ShortlistEntry(job_id=1, candidate_id=c.id, score=50, status=ShortlistStatus.PENDING))
    db_session.add(Conversation(candidate_id=c.id, job_id=1, collected={}, history=[], status="ACTIVE"))
    await db_session.flush()

    await delete_candidate(c.id, db_session)

    assert (await db_session.execute(select(Candidate))).scalars().all() == []
    assert (await db_session.execute(select(ShortlistEntry))).scalars().all() == []
    assert (await db_session.execute(select(Conversation))).scalars().all() == []


@pytest.mark.asyncio
async def test_inbound_auto_reply_is_processed_inline(tmp_path, monkeypatch):
    """An inbound candidate reply must produce a queued auto-reply synchronously.

    Regression guard: the handler used to run as a detached asyncio task and
    returned immediately, so on serverless (frozen after response) the auto-reply
    was never sent. inbound_message must AWAIT the handler.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession as _AS
    from app.database import Base
    from app.models.candidate import Candidate, CandidateSource
    from app.models.job import Job, JobStatus
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.wa_queue import WAQueue
    import app.api.webhook as webhook
    from app.api.wa_bridge import inbound_message

    db_file = tmp_path / "inbound.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=_AS, expire_on_commit=False)

    async with Session() as s:
        job = Job(title="Sr. Graphic Designer", company="Facets", location="Surat", status=JobStatus.ACTIVE)
        s.add(job); await s.flush()
        cand = Candidate(name="Kavya Rao", phone="9753124680", whatsapp="9753124680",
                         source=CandidateSource.NAUKRI)
        s.add(cand); await s.flush()
        s.add(ShortlistEntry(job_id=job.id, candidate_id=cand.id, score=72,
                             status=ShortlistStatus.CONTACTED))
        await s.commit()

    # The handler opens its own session via AsyncSessionLocal — point it at our DB.
    monkeypatch.setattr(webhook, "AsyncSessionLocal", Session)

    result = await inbound_message(
        {"from": "919753124680@c.us", "body": "what is the salary?", "session": "default"}
    )
    assert result["status"] == "processed"

    # An auto-reply must have been queued by the time the call returned.
    async with Session() as s:
        queued = (await s.execute(select(WAQueue))).scalars().all()
    assert len(queued) >= 1
    assert "9753124680" in queued[0].phone

