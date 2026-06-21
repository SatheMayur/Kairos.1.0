"""Tests for the WhatsApp agent's persistent memory + self-learning + morning brief."""
import pytest

from app.models.candidate import Candidate, CandidateSource
from app.models.job import Job, JobStatus
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.conversation import Conversation
from app.services import agent_memory as mem


@pytest.mark.asyncio
async def test_set_get_merge(db_session):
    await mem.set_memory(db_session, "global", "k", {"a": 1})
    assert (await mem.get_memory(db_session, "global", "k")) == {"a": 1}
    merged = await mem.merge_memory(db_session, "global", "k", {"b": 2, "a": 9})
    assert merged == {"a": 9, "b": 2}
    # Unique (scope,key): merging updates the same row, not a second one.
    tree = await mem.build_tree(db_session)
    assert list(tree["global"].keys()) == ["k"]


@pytest.mark.asyncio
async def test_record_candidate_learning_accumulates(db_session):
    await mem.record_candidate_learning(db_session, 7, collected={"expected_ctc": "6 LPA", "_asks": 1}, intent="interested")
    await mem.record_candidate_learning(db_session, 7, collected={"notice_period": "30 days"}, intent="interested")
    prof = await mem.get_memory(db_session, "candidate:7", "profile")
    # Facts accumulate across turns; private "_" keys are not stored.
    assert prof["facts"] == {"expected_ctc": "6 LPA", "notice_period": "30 days"}
    assert prof["interactions"] == 2
    # Global intent signal counted.
    counts = await mem.get_memory(db_session, "global", "intent_counts")
    assert counts["interested"] == 2


@pytest.mark.asyncio
async def test_run_sync_writes_snapshot(db_session):
    job = Job(title="HR", company="K", status=JobStatus.ACTIVE)
    db_session.add(job); await db_session.flush()
    c = Candidate(name="Aarti", phone="9876543210", source=CandidateSource.NAUKRI)
    db_session.add(c); await db_session.flush()
    db_session.add(ShortlistEntry(job_id=job.id, candidate_id=c.id, score=80, status=ShortlistStatus.INTERESTED))
    db_session.add(Conversation(candidate_id=c.id, job_id=job.id,
                                history=[{"dir": "in", "text": "yes interested", "ts": "2999-01-01T00:00:00"}]))
    await db_session.flush()

    snap = await mem.run_sync(db_session)
    assert snap["interested_now"] == 1
    assert snap["new_replies"] >= 1               # the future-dated inbound turn
    assert snap["active_jobs"] == 1
    meta = await mem.get_memory(db_session, "sync", "meta")
    assert meta["last_sync_at"]


@pytest.mark.asyncio
async def test_morning_brief_shape(db_session):
    job = Job(title="HR", company="K"); db_session.add(job); await db_session.flush()
    c = Candidate(name="Bhavna", phone="9876543210", source=CandidateSource.NAUKRI)
    db_session.add(c); await db_session.flush()
    db_session.add(ShortlistEntry(job_id=job.id, candidate_id=c.id, score=80, status=ShortlistStatus.INTERESTED))
    await db_session.flush()

    brief = await mem.build_morning_brief(db_session)
    assert "whatsapp" in brief and "calendar" in brief and "action_needed" in brief
    leads = brief["action_needed"]["interested_to_schedule"]
    assert any(l["candidate"] == "Bhavna" for l in leads)


def test_is_applicant_email():
    from app.services.agent_memory import _is_applicant_email
    assert _is_applicant_email({"from": "info@naukri.com", "subject": "responses summary"})
    assert _is_applicant_email({"from": "support@workindia.in", "subject": "Interview Confirmation"})
    assert _is_applicant_email({"from": "x@gmail.com", "subject": "Application for Designer — resume"})
    # Not recruitment mail:
    assert not _is_applicant_email({"from": "info@educohire.com", "subject": "feedback request"})
    assert not _is_applicant_email({"from": "noreply@google.com", "subject": "Apps Script failed"})
    assert not _is_applicant_email({"from": "monil@kgirdharlal.com", "subject": "email renamed"})


@pytest.mark.asyncio
async def test_morning_brief_inbox_shows_only_applicants(db_session):
    await mem.set_memory(db_session, "external", "gmail", {"fetched_at": "2026-06-21T00:00:00", "items": [
        {"from": "info@naukri.com", "subject": "232 applicants", "unread": True},
        {"from": "info@educohire.com", "subject": "feedback request", "unread": True},
        {"from": "x@y.com", "subject": "Application for Engineer", "unread": False},
    ]})
    brief = await mem.build_morning_brief(db_session)
    recent = brief["email"]["inbox"]["recent"]
    subjects = {m["subject"] for m in recent}
    assert "232 applicants" in subjects
    assert "Application for Engineer" in subjects
    assert "feedback request" not in subjects           # general mail filtered out
    assert brief["email"]["inbox"]["unread"] == 1        # only the unread applicant one


def test_llm_provider_prefers_claude_when_key_present(monkeypatch):
    from app.services import llm
    # Simulate an Anthropic key loaded at runtime + a Gemini key in settings.
    monkeypatch.setitem(llm._RUNTIME, "loaded", True)
    monkeypatch.setitem(llm._RUNTIME, "anthropic_key", "sk-ant-test")
    monkeypatch.setitem(llm._RUNTIME, "provider", "")
    monkeypatch.setattr(llm.get_settings(), "gemini_api_key", "g-key")
    # auto preference → Anthropic-first when its key is present
    assert llm.llm_provider() == "claude"
    monkeypatch.setitem(llm._RUNTIME, "provider", "gemini")
    assert llm.llm_provider() == "gemini"   # explicit override honored


@pytest.mark.asyncio
async def test_ai_engine_endpoint_validates_key(client):
    s = (await client.get("/api/v1/memory/ai-engine")).json()
    assert "provider" in s
    bad = await client.post("/api/v1/memory/ai-engine",
                            json={"anthropic_api_key": "not-a-key", "provider": "claude"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_google_credentials_endpoint(client):
    # Not configured by default.
    s = (await client.get("/api/v1/memory/google-credentials")).json()
    assert s["configured"] is False
    # Garbage JSON is rejected.
    bad = await client.post("/api/v1/memory/google-credentials",
                            json={"service_account_json": "not json", "impersonate_email": "k@x.com"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_memory_endpoints(client, db_session):
    assert (await client.post("/api/v1/memory/sync")).status_code == 200
    assert (await client.get("/api/v1/memory/morning-brief")).status_code == 200
    tree = await client.get("/api/v1/memory/tree")
    assert tree.status_code == 200
    # sync wrote the 'sync' branch
    assert "sync" in tree.json()
