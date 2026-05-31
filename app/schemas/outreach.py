from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.models.outreach import OutreachChannel, OutreachStatus, OutreachType


class OutreachLogCreate(BaseModel):
    candidate_id: int
    job_id: int
    channel: OutreachChannel
    outreach_type: OutreachType = OutreachType.INITIAL_CONTACT
    subject: Optional[str] = None
    message: str


class OutreachLogRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    candidate_id: int
    job_id: int
    channel: OutreachChannel
    outreach_type: OutreachType
    subject: Optional[str]
    message: str
    status: OutreachStatus
    error_detail: Optional[str]
    reply_text: Optional[str]
    sent_at: Optional[datetime]
    replied_at: Optional[datetime]
    created_at: datetime
