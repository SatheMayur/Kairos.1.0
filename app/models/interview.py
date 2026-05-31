"""Interview model — scheduled interview slots."""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, Enum as SAEnum, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class InterviewRound(str, enum.Enum):
    SCREENING = "SCREENING"
    TECHNICAL = "TECHNICAL"
    HR = "HR"
    FINAL = "FINAL"


class InterviewStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"       # slots sent to candidate
    CONFIRMED = "CONFIRMED"     # candidate picked a slot
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("candidates.id"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)

    round: Mapped[InterviewRound] = mapped_column(
        SAEnum(InterviewRound), default=InterviewRound.SCREENING
    )
    status: Mapped[InterviewStatus] = mapped_column(
        SAEnum(InterviewStatus), default=InterviewStatus.PROPOSED, nullable=False
    )

    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    interviewer_name: Mapped[Optional[str]] = mapped_column(String(255))
    interviewer_email: Mapped[Optional[str]] = mapped_column(String(255))
    meet_link: Mapped[Optional[str]] = mapped_column(String(512))
    confirmation_token: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    proposed_slots: Mapped[Optional[str]] = mapped_column(Text)  # JSON list of ISO datetimes

    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Interview id={self.id} round={self.round} "
            f"candidate={self.candidate_id} status={self.status}>"
        )
