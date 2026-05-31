"""Tests for admin UI pages (checks templates render and return 200)."""
import pytest


@pytest.mark.asyncio
async def test_ui_dashboard_renders(client):
    r = await client.get("/ui/")
    assert r.status_code == 200
    assert "Dashboard" in r.text
    assert "Command Center" not in r.text  # that's the HR ops dashboard, not this one


@pytest.mark.asyncio
async def test_ui_jobs_renders(client):
    r = await client.get("/ui/jobs")
    assert r.status_code == 200
    assert "Jobs" in r.text
    assert "New Job" in r.text


@pytest.mark.asyncio
async def test_ui_candidates_renders(client):
    r = await client.get("/ui/candidates")
    assert r.status_code == 200
    assert "Candidates" in r.text


@pytest.mark.asyncio
async def test_ui_shortlist_renders(client):
    r = await client.get("/ui/shortlist")
    assert r.status_code == 200
    assert "Shortlist" in r.text


@pytest.mark.asyncio
async def test_ui_outreach_renders(client):
    r = await client.get("/ui/outreach")
    assert r.status_code == 200
    assert "Outreach" in r.text


@pytest.mark.asyncio
async def test_ui_interviews_renders(client):
    r = await client.get("/ui/interviews")
    assert r.status_code == 200
    assert "Interviews" in r.text


@pytest.mark.asyncio
async def test_root_redirects_to_ui(client):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert "/ui/" in r.headers.get("location", "")


@pytest.mark.asyncio
async def test_static_js_served(client):
    r = await client.get("/static/app.js")
    assert r.status_code == 200
    assert "function api(" in r.text
