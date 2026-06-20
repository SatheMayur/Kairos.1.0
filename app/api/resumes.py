"""Resume Bank + JD Bank API.

Resume Bank: upload a CV, we read the text out of it and file it under the
matching candidate. JD Bank: a record of every job description that came in.
All user-facing messages are plain English (the owner is non-technical).
"""
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.candidate import Candidate
from app.models.resume_doc import ResumeDoc
from app.models.jd_doc import JDDoc
from app.services.resume_bank import ingest_resume
from app.utils.logging import get_logger

router = APIRouter(prefix="/resumes", tags=["resume-bank"])
logger = get_logger(__name__)

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


# ── helpers ────────────────────────────────────────────────────────────────

async def store_jd(
    db: AsyncSession,
    title: Optional[str],
    raw_text: str,
    source: str,
    job_id: Optional[int] = None,
) -> Optional[JDDoc]:
    """Save a job description into the JD Bank. Returns the row, or None on failure.

    Designed to be called from job-creation flows; callers should wrap it in
    try/except so a bank failure never blocks creating the job.
    """
    if not (raw_text or "").strip():
        return None
    doc = JDDoc(
        title=(title or "Untitled Role")[:255],
        raw_text=raw_text,
        source=(source or "PASTE").upper(),
        job_id=job_id,
    )
    db.add(doc)
    await db.flush()
    return doc


# ── Resume Bank ──────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    source: str = Form("UPLOAD"),
    from_contact: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a CV (PDF / DOC / DOCX / TXT). We read the text and file it."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="That file is empty — please choose a CV file.")
    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="That file is too large (over 5 MB). Please upload a smaller file.")

    result = await ingest_resume(
        file_bytes=content,
        filename=file.filename or "resume",
        source=source or "UPLOAD",
        from_contact=from_contact,
        db=db,
    )
    if not result.get("ok"):
        reason = result.get("reason") or "Couldn't read this CV."
        if reason == "no readable text":
            reason = ("Couldn't find readable text in this file. Old .doc files often don't "
                      "read well — please save it as PDF or DOCX and try again.")
        raise HTTPException(status_code=422, detail=reason)
    return result


def _snippet(text: Optional[str], n: int = 220) -> str:
    s = " ".join((text or "").split())
    return s[:n] + ("…" if len(s) > n else "")


@router.get("")
async def list_resumes(
    q: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List Resume Bank entries (newest first). `q` filters by name or text."""
    limit = max(1, min(limit, 500))
    stmt = (
        select(ResumeDoc, Candidate)
        .join(Candidate, Candidate.id == ResumeDoc.candidate_id, isouter=True)
        .order_by(ResumeDoc.received_at.desc())
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Candidate.name.ilike(like), ResumeDoc.text.ilike(like)))
    stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).all()
    out = []
    for doc, cand in rows:
        out.append({
            "id": doc.id,
            "candidate_id": doc.candidate_id,
            "candidate_name": cand.name if cand else None,
            "filename": doc.filename,
            "source": doc.source,
            "from_contact": doc.from_contact,
            "received_at": doc.received_at.isoformat() if doc.received_at else None,
            "snippet": _snippet(doc.text),
        })
    return out


@router.get("/{resume_id}")
async def get_resume(resume_id: int, db: AsyncSession = Depends(get_db)):
    """Full Resume Bank record including the extracted text and candidate basics."""
    doc = (await db.execute(
        select(ResumeDoc).where(ResumeDoc.id == resume_id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="That CV record was not found.")

    cand = None
    if doc.candidate_id:
        cand = (await db.execute(
            select(Candidate).where(Candidate.id == doc.candidate_id)
        )).scalar_one_or_none()

    return {
        "id": doc.id,
        "candidate_id": doc.candidate_id,
        "candidate": ({
            "id": cand.id,
            "name": cand.name,
            "email": cand.email,
            "phone": cand.phone,
            "location": cand.location,
        } if cand else None),
        "filename": doc.filename,
        "source": doc.source,
        "from_contact": doc.from_contact,
        "size_bytes": doc.size_bytes,
        "received_at": doc.received_at.isoformat() if doc.received_at else None,
        "text": doc.text,
    }


# ── JD Bank ──────────────────────────────────────────────────────────────────

jd_router = APIRouter(prefix="/jd-bank", tags=["jd-bank"])


@jd_router.get("")
async def list_jds(q: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """List stored job descriptions (newest first). `q` filters by title or text."""
    stmt = select(JDDoc).order_by(JDDoc.created_at.desc())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(JDDoc.title.ilike(like), JDDoc.raw_text.ilike(like)))
    rows = (await db.execute(stmt.limit(500))).scalars().all()
    return [
        {
            "id": d.id,
            "title": d.title,
            "source": d.source,
            "job_id": d.job_id,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "snippet": _snippet(d.raw_text),
            "raw_text": d.raw_text,
        }
        for d in rows
    ]
