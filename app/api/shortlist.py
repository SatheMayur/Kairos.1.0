"""Shortlist API — manage pipeline status for job-candidate pairs."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.candidate import Candidate
from app.models.job import Job
from app.schemas.shortlist import ShortlistEntryCreate, ShortlistEntryRead, ShortlistEntryUpdate
from app.services.scoring import score_candidate
from app.utils.logging import get_logger

router = APIRouter(prefix="/shortlist", tags=["shortlist"])
logger = get_logger(__name__)


@router.post("", response_model=ShortlistEntryRead, status_code=status.HTTP_201_CREATED)
async def create_entry(payload: ShortlistEntryCreate, db: AsyncSession = Depends(get_db)):
    entry = ShortlistEntry(**payload.model_dump())
    db.add(entry)
    await db.flush()
    return entry


@router.get("", response_model=list[ShortlistEntryRead])
async def list_entries(
    job_id: Optional[int] = None,
    status: Optional[str] = None,
    min_score: float = 0.0,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ShortlistEntry)
        .where(ShortlistEntry.score >= min_score)
        .offset(skip)
        .limit(limit)
        .order_by(ShortlistEntry.score.desc())
    )
    if job_id:
        query = query.where(ShortlistEntry.job_id == job_id)
    if status:
        query = query.where(ShortlistEntry.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/triage")
async def get_triage_candidates(
    job_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Return PENDING candidates for rapid triage review, enriched with full candidate data."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    sl_res = await db.execute(
        select(ShortlistEntry)
        .where(
            ShortlistEntry.job_id == job_id,
            ShortlistEntry.status == ShortlistStatus.PENDING,
        )
        .order_by(ShortlistEntry.score.desc())
        .limit(limit)
    )
    entries = sl_res.scalars().all()

    cand_ids = [e.candidate_id for e in entries]
    cands = {}
    if cand_ids:
        cr = await db.execute(select(Candidate).where(Candidate.id.in_(cand_ids)))
        cands = {c.id: c for c in cr.scalars().all()}

    result = []
    for e in entries:
        c = cands.get(e.candidate_id)
        if not c:
            continue
        bd = e.score_breakdown or {}
        result.append({
            "entry_id": e.id,
            "candidate_id": c.id,
            "name": c.name,
            "email": c.email,
            "phone": c.phone,
            "location": c.location,
            "current_role": c.current_role,
            "current_employer": c.current_employer,
            "experience_years": c.experience_years,
            "expected_salary": c.expected_salary,
            "skills": c.skills or [],
            "education": c.education,
            "score": e.score,
            "ai_strengths": bd.get("ai_strengths", []),
            "ai_concerns": bd.get("ai_concerns", []),
            "ai_reasoning": bd.get("ai_reasoning", ""),
        })

    return {
        "job_title": job.title,
        "total_pending": len(result),
        "candidates": result,
    }


@router.get("/compare")
async def compare_candidates(
    job_id: int,
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
):
    """Top candidates for a job, side by side — for making the final call."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    sl_res = await db.execute(
        select(ShortlistEntry)
        .where(
            ShortlistEntry.job_id == job_id,
            ShortlistEntry.status.notin_([
                ShortlistStatus.REJECTED,
                ShortlistStatus.DROPPED,
                ShortlistStatus.NOT_INTERESTED,
            ]),
        )
        .order_by(ShortlistEntry.score.desc())
        .limit(limit)
    )
    entries = sl_res.scalars().all()

    cand_ids = [e.candidate_id for e in entries]
    cands = {}
    if cand_ids:
        cr = await db.execute(select(Candidate).where(Candidate.id.in_(cand_ids)))
        cands = {c.id: c for c in cr.scalars().all()}

    result = []
    for e in entries:
        c = cands.get(e.candidate_id)
        if not c:
            continue
        bd = e.score_breakdown or {}
        result.append({
            "entry_id": e.id,
            "candidate_id": c.id,
            "name": c.name,
            "status": e.status.value,
            "score": e.score,
            "email": c.email,
            "phone": c.phone or c.whatsapp,
            "location": c.location,
            "current_role": c.current_role,
            "current_employer": c.current_employer,
            "experience_years": c.experience_years,
            "expected_salary": c.expected_salary,
            "notice_period_days": c.notice_period_days,
            "skills": c.skills or [],
            "education": c.education,
            "ai_strengths": bd.get("ai_strengths", []),
            "ai_concerns": bd.get("ai_concerns", []),
            "ai_reasoning": bd.get("ai_reasoning", ""),
        })

    return {
        "job_id": job.id,
        "job_title": job.title,
        "job_company": job.company,
        "candidates": result,
    }


@router.get("/{entry_id}", response_model=ShortlistEntryRead)
async def get_entry(entry_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(entry_id, db)


@router.patch("/{entry_id}", response_model=ShortlistEntryRead)
async def update_entry(
    entry_id: int, payload: ShortlistEntryUpdate, db: AsyncSession = Depends(get_db)
):
    entry = await _get_or_404(entry_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(entry, field, value)
    return entry


@router.post("/score/{job_id}/{candidate_id}", response_model=ShortlistEntryRead)
async def score_and_add(job_id: int, candidate_id: int, db: AsyncSession = Depends(get_db)):
    """Score a specific candidate against a job and create/update the shortlist entry."""
    job_result = await db.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    cand_result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = cand_result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    scored = score_candidate(
        candidate_skills=candidate.skills or [],
        candidate_experience=candidate.experience_years,
        candidate_expected_salary=candidate.expected_salary,
        candidate_location=candidate.location,
        candidate_role=candidate.current_role,
        job_title=job.title,
        job_skills=job.skills or [],
        job_experience_min=job.experience_min,
        job_experience_max=job.experience_max,
        job_salary_min=job.salary_min,
        job_salary_max=job.salary_max,
        job_location=job.location,
    )
    status_map = {
        "AUTO_SHORTLIST": ShortlistStatus.SHORTLISTED,
        "MANUAL_REVIEW": ShortlistStatus.PENDING,
        "REJECT": ShortlistStatus.REJECTED,
    }

    # Upsert
    existing_result = await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.job_id == job_id,
            ShortlistEntry.candidate_id == candidate_id,
        )
    )
    entry = existing_result.scalar_one_or_none()
    if entry:
        entry.score = scored.total
        entry.score_breakdown = scored.breakdown
        entry.status = status_map[scored.decision]
    else:
        entry = ShortlistEntry(
            job_id=job_id,
            candidate_id=candidate_id,
            score=scored.total,
            score_breakdown=scored.breakdown,
            status=status_map[scored.decision],
        )
        db.add(entry)
    await db.flush()
    return entry


async def _get_or_404(entry_id: int, db: AsyncSession) -> ShortlistEntry:
    result = await db.execute(select(ShortlistEntry).where(ShortlistEntry.id == entry_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail=f"ShortlistEntry {entry_id} not found")
    return obj
