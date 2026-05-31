from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from app.models.candidate import CandidateSource


class CandidateCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience_years: Optional[float] = None
    current_salary: Optional[float] = None
    expected_salary: Optional[float] = None
    location: Optional[str] = None
    notice_period_days: Optional[int] = None
    education: Optional[str] = None
    current_employer: Optional[str] = None
    current_role: Optional[str] = None
    raw_profile: Optional[str] = None
    resume_url: Optional[str] = None
    source: CandidateSource = CandidateSource.MANUAL
    source_ref: Optional[str] = None


class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    skills: Optional[list[str]] = None
    experience_years: Optional[float] = None
    current_salary: Optional[float] = None
    expected_salary: Optional[float] = None
    location: Optional[str] = None
    notice_period_days: Optional[int] = None
    education: Optional[str] = None
    current_employer: Optional[str] = None
    current_role: Optional[str] = None


class CandidateRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    email: Optional[str]
    phone: Optional[str]
    whatsapp: Optional[str]
    skills: list[str]
    experience_years: Optional[float]
    current_salary: Optional[float]
    expected_salary: Optional[float]
    location: Optional[str]
    notice_period_days: Optional[int]
    education: Optional[str]
    current_employer: Optional[str]
    current_role: Optional[str]
    source: CandidateSource
    source_ref: Optional[str]
    created_at: datetime
