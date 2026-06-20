"""ShortlistEntry — links a Candidate to a Job with a score and pipeline status."""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Integer, Float, DateTime, ForeignKey, Enum as SAEnum, JSON, Text, UniqueConstraint,
)
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


# How "advanced" a pipeline status is. Used to pick the best row when
# de-duplicating (most-advanced wins) and to decide safe status transitions
# (never auto-move a candidate backwards past review). Higher = further along.
STATUS_RANK: dict[ShortlistStatus, int] = {
    ShortlistStatus.HIRED: 7,
    ShortlistStatus.INTERVIEW_SCHEDULED: 6,
    ShortlistStatus.INTERESTED: 5,
    ShortlistStatus.CONTACTED: 4,
    ShortlistStatus.SHORTLISTED: 3,
    ShortlistStatus.PENDING: 2,
    ShortlistStatus.REJECTED: 1,
    ShortlistStatus.NOT_INTERESTED: 1,
    ShortlistStatus.DROPPED: 0,
}

# Statuses past review that re-scoring must never silently move backwards.
ADVANCED_STATUSES = frozenset({
    ShortlistStatus.CONTACTED,
    ShortlistStatus.INTERESTED,
    ShortlistStatus.INTERVIEW_SCHEDULED,
    ShortlistStatus.HIRED,
})


class ShortlistEntry(Base):
    __tablename__ = "shortlist"
    __table_args__ = (
        UniqueConstraint("candidate_id", "job_id", name="uq_shortlist_candidate_job"),
    )

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
