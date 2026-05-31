from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.models.interview import InterviewRound, InterviewStatus


class InterviewCreate(BaseModel):
    candidate_id: int
    job_id: int
    round: InterviewRound = InterviewRound.SCREENING
    duration_minutes: int = 30
    interviewer_name: Optional[str] = None
    interviewer_email: Optional[str] = None
    proposed_slots: Optional[list[str]] = None  # ISO datetime strings


class InterviewUpdate(BaseModel):
    status: Optional[InterviewStatus] = None
    scheduled_at: Optional[datetime] = None
    meet_link: Optional[str] = None
    notes: Optional[str] = None
    reminder_sent: Optional[bool] = None


class InterviewRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    candidate_id: int
    job_id: int
    round: InterviewRound
    status: InterviewStatus
    scheduled_at: Optional[datetime]
    duration_minutes: int
    interviewer_name: Optional[str]
    interviewer_email: Optional[str]
    meet_link: Optional[str]
    confirmation_token: Optional[str]
    proposed_slots: Optional[str]
    reminder_sent: bool
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
