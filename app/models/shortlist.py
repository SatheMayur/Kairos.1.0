"""ShortlistEntry — links a Candidate to a Job with a score and pipeline status."""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, Float, DateTime, ForeignKey, Enum as SAEnum, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ShortlistStatus(str, enum.Enum):
    PENDING = "PENDING"           # scored, not yet reviewed
    SHORTLISTED = "SHORTLISTED"   # approved for outreach
    REJECTED = "REJECTED"         # filtered out
    CONTACTED = "CONTACTED"       # outreach sent
    INTERESTED = "INTERESTED"     # candidate replied yes
    NOT_INTERESTED = "NOT_INTERESTED"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    HIRED = "HIRED"
    DROPPED = "DROPPED"


class ShortlistEntry(Base):
    __tablename__ = "shortlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("candidates.id"), nullable=False, index=True
    )

    score: Mapped[float] = mapped_column(Float, default=0.0)          # 0–100
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)      # per-dimension scores
    recruiter_notes: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[ShortlistStatus] = mapped_column(
        SAEnum(ShortlistStatus), default=ShortlistStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<ShortlistEntry id={self.id} job={self.job_id} "
            f"candidate={self.candidate_id} score={self.score:.1f} status={self.status}>"
        )
