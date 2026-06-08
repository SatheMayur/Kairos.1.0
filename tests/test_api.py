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

