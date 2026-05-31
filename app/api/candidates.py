"""Candidates API — CRUD for candidate records."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.candidate import Candidate
from app.schemas.candidate import CandidateCreate, CandidateRead, CandidateUpdate

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.post("", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
async def create_candidate(payload: CandidateCreate, db: AsyncSession = Depends(get_db)):
    candidate = Candidate(**payload.model_dump())
    db.add(candidate)
    await db.flush()
    return candidate


@router.get("", response_model=list[CandidateRead])
async def list_candidates(
    source: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = select(Candidate).offset(skip).limit(limit).order_by(Candidate.created_at.desc())
    if source:
        query = query.where(Candidate.source == source)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{candidate_id}", response_model=CandidateRead)
async def get_candidate(candidate_id: int, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(candidate_id, db)


@router.patch("/{candidate_id}", response_model=CandidateRead)
async def update_candidate(
    candidate_id: int, payload: CandidateUpdate, db: AsyncSession = Depends(get_db)
):
    candidate = await _get_or_404(candidate_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(candidate, field, value)
    return candidate


@router.delete("/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: int, db: AsyncSession = Depends(get_db)):
    candidate = await _get_or_404(candidate_id, db)
    await db.delete(candidate)


async def _get_or_404(candidate_id: int, db: AsyncSession) -> Candidate:
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found")
    return obj
