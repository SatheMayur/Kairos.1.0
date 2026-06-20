"""Tests for the Resume Bank + JD Bank feature."""
import pytest
from sqlalchemy import select, func

from app.models.candidate import Candidate, CandidateSource
from app.models.resume_doc import ResumeDoc
from app.models.jd_doc import JDDoc
from app.services.resume_bank import parse_resume, ingest_resume
from app.api.resumes import store_jd


RESUME_TEXT = """Kavya Rao
Senior Graphic Designer

Email: kavya.rao@gmail.com
Phone: +91 97531 24680

Summary
Creative designer with 6 years of experience in branding and typography.
Skilled in Photoshop, Illustrator and Figma.
"""


def test_parse_resume_extracts_fields():
    parsed = parse_resume(RESUME_TEXT)
    assert parsed["email"] == "kavya.rao@gmail.com"
    assert parsed["phone"] == "9753124680"
    assert parsed["name"] == "Kavya Rao"
    skills_lower = [s.lower() for s in (parsed["skills"] or [])]
    assert "photoshop" in skills_lower
    assert "illustrator" in skills_lower
    assert parsed["experience_years"] == 6.0


@pytest.mark.asyncio
async def test_ingest_creates_resume_and_candidate(db_session):
    res = await ingest_resume(
        file_bytes=RESUME_TEXT.encode("utf-8"),
        filename="kavya.txt",
        source="UPLOAD",
        db=db_session,
    )
    assert res["ok"] is True
    assert res.get("duplicate") is not True
    assert res["matched"] is False
    assert res["candidate_name"] == "Kavya Rao"

    # One resume doc, linked to a candidate created from the CV.
    docs = (await db_session.execute(select(ResumeDoc))).scalars().all()
    assert len(docs) == 1
    assert docs[0].candidate_id == res["candidate_id"]

    cand = (await db_session.execute(
        select(Candidate).where(Candidate.id == res["candidate_id"])
    )).scalar_one()
    assert cand.email == "kavya.rao@gmail.com"


@pytest.mark.asyncio
async def test_reingest_same_text_is_duplicate(db_session):
    first = await ingest_resume(
        file_bytes=RESUME_TEXT.encode("utf-8"),
        filename="kavya.txt", source="UPLOAD", db=db_session,
    )
    assert first["ok"] is True

    again = await ingest_resume(
        file_bytes=RESUME_TEXT.encode("utf-8"),
        filename="kavya-copy.txt", source="EMAIL", db=db_session,
    )
    assert again["ok"] is True
    assert again["duplicate"] is True

    count = (await db_session.execute(
        select(func.count()).select_from(ResumeDoc)
    )).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_ingest_matches_existing_candidate_by_phone(db_session):
    existing = Candidate(
        name="Pooja Malhotra",
        email="pooja.m@gmail.com",
        phone="9988776655",
        source=CandidateSource.MANUAL,
    )
    db_session.add(existing)
    await db_session.flush()

    text = (
        "Pooja Malhotra\nGraphic Designer\n"
        "Reach me on 99887 76655 for a chat.\n"
        "4 years experience with Adobe tools.\n"
    )
    res = await ingest_resume(
        file_bytes=text.encode("utf-8"),
        filename="pooja.txt", source="WHATSAPP",
        from_contact="9988776655", db=db_session,
    )
    assert res["ok"] is True
    assert res["matched"] is True
    assert res["candidate_id"] == existing.id

    # No duplicate candidate created.
    count = (await db_session.execute(
        select(func.count()).select_from(Candidate)
    )).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_ingest_no_readable_text(db_session):
    res = await ingest_resume(
        file_bytes=b"hi",
        filename="x.txt", source="UPLOAD", db=db_session,
    )
    assert res["ok"] is False
    assert res["reason"] == "no readable text"


@pytest.mark.asyncio
async def test_store_jd_and_list(db_session, client):
    doc = await store_jd(
        db_session,
        title="Sr. Graphic Designer",
        raw_text="We need a senior graphic designer skilled in Photoshop and branding.",
        source="PASTE",
    )
    await db_session.commit()
    assert doc is not None and doc.id

    saved = (await db_session.execute(select(JDDoc))).scalars().all()
    assert len(saved) == 1

    resp = await client.get("/api/v1/jd-bank")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["title"] == "Sr. Graphic Designer" for r in rows)

    # search filter
    resp2 = await client.get("/api/v1/jd-bank?q=branding")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1


@pytest.mark.asyncio
async def test_list_resumes_endpoint(db_session, client):
    await ingest_resume(
        file_bytes=RESUME_TEXT.encode("utf-8"),
        filename="kavya.txt", source="UPLOAD", db=db_session,
    )
    resp = await client.get("/api/v1/resumes")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["candidate_name"] == "Kavya Rao"
    assert "snippet" in rows[0]

    rid = rows[0]["id"]
    detail = await client.get(f"/api/v1/resumes/{rid}")
    assert detail.status_code == 200
    assert detail.json()["text"]
