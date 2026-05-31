"""Candidate model — single source of truth for applicant data."""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, Float, DateTime, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class CandidateSource(str, enum.Enum):
    LINKEDIN = "LINKEDIN"
    INDEED = "INDEED"
    NAUKRI = "NAUKRI"
    APNA = "APNA"
    WORKINDIA = "WORKINDIA"
    JOBHAI = "JOBHAI"
    INTERNSHALA = "INTERNSHALA"
    FRESHERSWORLD = "FRESHERSWORLD"
    SHINE = "SHINE"
    PLACEMENTINDIA = "PLACEMENTINDIA"
    QUIKR = "QUIKR"
    CLICKINDIA = "CLICKINDIA"
    OLX = "OLX"
    JORA = "JORA"
    FOUNDIT = "FOUNDIT"
    MANUAL = "MANUAL"
    MOCK = "MOCK"


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    whatsapp: Mapped[Optional[str]] = mapped_column(String(50))

    # Profile fields
    skills: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    experience_years: Mapped[Optional[float]] = mapped_column(Float)
    current_salary: Mapped[Optional[float]] = mapped_column(Float)
    expected_salary: Mapped[Optional[float]] = mapped_column(Float)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    notice_period_days: Mapped[Optional[int]] = mapped_column(Integer)
    education: Mapped[Optional[str]] = mapped_column(String(255))
    current_employer: Mapped[Optional[str]] = mapped_column(String(255))
    current_role: Mapped[Optional[str]] = mapped_column(String(255))
    raw_profile: Mapped[Optional[str]] = mapped_column(Text)
    resume_url: Mapped[Optional[str]] = mapped_column(String(512))

    # Source tracking
    source: Mapped[CandidateSource] = mapped_column(
        SAEnum(CandidateSource), default=CandidateSource.MANUAL
    )
    source_ref: Mapped[Optional[str]] = mapped_column(String(512))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Candidate id={self.id} name={self.name!r} source={self.source}>"
