from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.models.shortlist import ShortlistStatus


class ShortlistEntryCreate(BaseModel):
    job_id: int
    candidate_id: int
    score: float = 0.0
    score_breakdown: Optional[dict] = None
    status: ShortlistStatus = ShortlistStatus.PENDING


class ShortlistEntryUpdate(BaseModel):
    status: Optional[ShortlistStatus] = None
    recruiter_notes: Optional[str] = None


class ShortlistEntryRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    job_id: int
    candidate_id: int
    score: float
    score_breakdown: Optional[dict]
    recruiter_notes: Optional[str]
    status: ShortlistStatus
    created_at: datetime
    updated_at: datetime
