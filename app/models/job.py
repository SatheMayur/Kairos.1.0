"""Job / Job Requisition model."""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, Float, DateTime, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class JobStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[Optional[str]] = mapped_column(String(255))
    raw_jd: Mapped[Optional[str]] = mapped_column(Text)

    # Extracted fields from JD analysis
    skills: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    experience_min: Mapped[Optional[float]] = mapped_column(Float)
    experience_max: Mapped[Optional[float]] = mapped_column(Float)
    salary_min: Mapped[Optional[float]] = mapped_column(Float)
    salary_max: Mapped[Optional[float]] = mapped_column(Float)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    notice_period_days: Mapped[Optional[int]] = mapped_column(Integer)
    education: Mapped[Optional[str]] = mapped_column(String(255))
    job_type: Mapped[Optional[str]] = mapped_column(String(100))  # full-time / part-time / contract
    description: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus), default=JobStatus.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.title!r} status={self.status}>"
