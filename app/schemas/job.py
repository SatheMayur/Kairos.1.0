from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.models.job import JobStatus


class JobCreate(BaseModel):
    title: str
    company: Optional[str] = None
    raw_jd: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience_min: Optional[float] = None
    experience_max: Optional[float] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    location: Optional[str] = None
    notice_period_days: Optional[int] = None
    education: Optional[str] = None
    job_type: Optional[str] = None
    description: Optional[str] = None
    status: JobStatus = JobStatus.ACTIVE


class JobUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    skills: Optional[list[str]] = None
    experience_min: Optional[float] = None
    experience_max: Optional[float] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    location: Optional[str] = None
    notice_period_days: Optional[int] = None
    education: Optional[str] = None
    job_type: Optional[str] = None
    description: Optional[str] = None
    status: Optional[JobStatus] = None


class JobRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    company: Optional[str]
    skills: list[str]
    experience_min: Optional[float]
    experience_max: Optional[float]
    salary_min: Optional[float]
    salary_max: Optional[float]
    location: Optional[str]
    notice_period_days: Optional[int]
    education: Optional[str]
    job_type: Optional[str]
    description: Optional[str]
    status: JobStatus
    created_at: datetime
    updated_at: datetime


class JDAnalysisResult(BaseModel):
    """Structured output from the JD analyzer service."""
    title: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience_min: Optional[float] = None
    experience_max: Optional[float] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    location: Optional[str] = None
    notice_period_days: Optional[int] = None
    education: Optional[str] = None
    job_type: Optional[str] = None
    description: Optional[str] = None
