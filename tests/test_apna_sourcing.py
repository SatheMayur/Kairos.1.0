"""Tests for server-side Apna token storage and Apna-in-internal-sourcing wiring.

All network calls are mocked — these tests NEVER hit Apna.
"""
import pytest
import pytest_asyncio

from app.adapters.base import RawCandidate
from app.models.candidate import CandidateSource
from app.models.job import Job
from app.services.app_settings import get_setting, set_setting
from app.services.sourcing import source_candidates_for_job


@pytest.fixture(autouse=True)
def _use_mock_adapters(mock_adapters):
    """Run against the mock registry (same as the other sourcing tests)."""
    yield


@pytest_asyncio.fixture
async def sample_job(db_session):
    job = Job(
        title="HR Executive",
        company="K. Girdharlal International",
        skills=["HR", "Payroll"],
        experience_min=1.0,
        experience_max=3.0,
        location="Surat",
    )
    db_session.add(job)
    await db_session.flush()
    return job


# ── AppSetting round-trip ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_setting_round_trip(db_session):
    assert await get_setting(db_session, "apna_token") is None

    await set_setting(db_session, "apna_token", "eyJabc")
    assert await get_setting(db_session, "apna_token") == "eyJabc"

    # Update overwrites, does not duplicate.
    await set_setting(db_session, "apna_token", "eyJxyz")
    assert await get_setting(db_session, "apna_token") == "eyJxyz"


# ── Apna wired into internal sourcing ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_sourcing_includes_apna_when_token_set(sample_job, db_session, monkeypatch):
    await set_setting(db_session, "apna_token", "eyJtoken")

    apna_candidate = RawCandidate(
        name="Apna Live Candidate",
        source=CandidateSource.APNA,
        skills=["HR", "Payroll"],
        experience_years=2.0,
        location="Surat",
        phone="9876543210",          # unlocked Apna profile → reachable, so it's added
        source_ref="apna:live-123",
    )

    called = {}

    async def fake_search(self, **kwargs):
        called["yes"] = True
        return [apna_candidate]

    # Mock the network search so nothing leaves the process.
    monkeypatch.setattr("app.adapters.apna.ApnaAdapter.search", fake_search)

    entries = await source_candidates_for_job(sample_job, db_session)

    assert called.get("yes") is True

    from sqlalchemy import select
    from app.models.candidate import Candidate
    names = {c.name for c in (await db_session.execute(select(Candidate))).scalars().all()}
    assert "Apna Live Candidate" in names


@pytest.mark.asyncio
async def test_sourcing_skips_apna_when_no_token(sample_job, db_session, monkeypatch):
    # No apna_token setting at all → Apna search must NEVER be called.
    async def boom(self, **kwargs):
        raise AssertionError("ApnaAdapter.search should not be called without a token")

    monkeypatch.setattr("app.adapters.apna.ApnaAdapter.search", boom)

    entries = await source_candidates_for_job(sample_job, db_session)

    from sqlalchemy import select
    from app.models.candidate import Candidate
    names = {c.name for c in (await db_session.execute(select(Candidate))).scalars().all()}
    assert "Apna Live Candidate" not in names


@pytest.mark.asyncio
async def test_sourcing_survives_expired_apna_token(sample_job, db_session, monkeypatch):
    """A 401 from Apna must be swallowed — sourcing keeps working."""
    await set_setting(db_session, "apna_token", "eyJexpired")

    class FakeResp:
        status_code = 401

    async def raise_401(self, **kwargs):
        exc = Exception("Apna says token expired")
        exc.response = FakeResp()
        raise exc

    monkeypatch.setattr("app.adapters.apna.ApnaAdapter.search", raise_401)

    # Should not raise — mock-registry candidates still get sourced.
    entries = await source_candidates_for_job(sample_job, db_session)
    assert isinstance(entries, list)
