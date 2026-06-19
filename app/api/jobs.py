"""Jobs API — CRUD + JD analysis + trigger sourcing."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.job import Job
from app.schemas.job import JobCreate, JobRead, JobUpdate, JDAnalysisResult
from app.services.jd_analyzer import analyze_jd
from app.services.sourcing import source_candidates_for_job
from app.utils.logging import get_logger

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = get_logger(__name__)


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    """Create a job requisition (fields pre-filled or from JD analysis)."""
    job = Job(**payload.model_dump())
    db.add(job)
    await db.flush()
    return job


@router.post("/analyze-jd", response_model=JDAnalysisResult)
async def analyze_jd_text(raw_jd: str, db: AsyncSession = Depends(get_db)):
    """Parse a JD string and return extracted fields (rules-based; does not save)."""
    return analyze_jd(raw_jd)


@router.post("/analyze-jd-file", response_model=JDAnalysisResult)
async def analyze_jd_file(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Upload a JD file (PDF / DOC / DOCX / TXT), extract its text, and return the
    parsed job fields for the recruiter to review (does NOT create the job)."""
    import io

    filename = (file.filename or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in ("pdf", "doc", "docx", "txt"):
        raise HTTPException(status_code=400,
                            detail="Unsupported file type. Please upload a PDF, DOC, DOCX or TXT file.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="That file is empty — please choose a file with the job description in it.")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="That file is too large (over 5 MB). Please upload a smaller file.")

    text = ""
    try:
        if ext == "txt":
            text = content.decode("utf-8", errors="ignore")
        elif ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        elif ext == "docx":
            import docx
            doc = docx.Document(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif ext == "doc":
            # Old binary .doc — no reliable pure-Python parser; best-effort, else ask for PDF/DOCX.
            text = "".join(ch for ch in content.decode("latin-1", errors="ignore") if ch.isprintable() or ch in "\n\r\t ")
    except Exception as exc:
        logger.warning("JD file extraction failed (%s): %s", ext, exc)
        raise HTTPException(status_code=422,
                            detail="Couldn't read text from this file — it may be corrupt or password-protected. Try re-saving it as a PDF or DOCX.")

    if len((text or "").strip()) < 20:
        raise HTTPException(status_code=422,
                            detail=("Couldn't find readable text in this file."
                                    + (" Old .doc files often don't read well — please save it as PDF or DOCX and try again." if ext == "doc" else "")))

    return analyze_jd(text)


@router.post("/from-jd", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job_from_jd(raw_jd: str, db: AsyncSession = Depends(get_db)):
    """Analyze JD text and immediately create + save a job record."""
    parsed = analyze_jd(raw_jd)
    job = Job(
        title=parsed.title or "Untitled Role",
        raw_jd=raw_jd,
        skills=parsed.skills,
        experience_min=parsed.experience_min,
        experience_max=parsed.experience_max,
        salary_min=parsed.salary_min,
        salary_max=parsed.salary_max,
        location=parsed.location,
        notice_period_days=parsed.notice_period_days,
        education=parsed.education,
        job_type=parsed.job_type,
        description=parsed.description,
    )
    db.add(job)
    await db.flush()
    return job


@router.get("", response_model=list[JobRead])
async def list_jobs(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    query = select(Job).offset(skip).limit(limit).order_by(Job.created_at.desc())
    if status:
        query = query.where(Job.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await _get_or_404(job_id, db)
    return job


@router.patch("/{job_id}", response_model=JobRead)
async def update_job(job_id: int, payload: JobUpdate, db: AsyncSession = Depends(get_db)):
    job = await _get_or_404(job_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(job, field, value)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await _get_or_404(job_id, db)
    await db.delete(job)


@router.post("/{job_id}/source", summary="Trigger candidate sourcing for a job")
async def trigger_sourcing(job_id: int, db: AsyncSession = Depends(get_db)):
    """Fan out to all portal adapters, score, and persist candidates."""
    job = await _get_or_404(job_id, db)
    entries = await source_candidates_for_job(job, db)
    return {"sourced": len(entries), "job_id": job_id}


async def _get_or_404(job_id: int, db: AsyncSession) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job
