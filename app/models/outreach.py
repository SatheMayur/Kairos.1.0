"""OutreachLog — every message sent to a candidate across all channels."""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class OutreachChannel(str, enum.Enum):
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    SMS = "SMS"
    CALL = "CALL"
    PLATFORM_MESSAGE = "PLATFORM_MESSAGE"  # CAD Crowd / LinkedIn / portal DM
    UNREACHABLE = "UNREACHABLE"            # no contact info of any kind


class OutreachStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    REPLIED = "REPLIED"
    BOUNCED = "BOUNCED"


class OutreachType(str, enum.Enum):
    INITIAL_CONTACT = "INITIAL_CONTACT"
    FOLLOW_UP = "FOLLOW_UP"
    SLOT_PROPOSAL = "SLOT_PROPOSAL"
    CONFIRMATION = "CONFIRMATION"
    REMINDER = "REMINDER"
    REJECTION = "REJECTION"


class OutreachLog(Base):
    __tablename__ = "outreach_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("candidates.id"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)

    channel: Mapped[OutreachChannel] = mapped_column(SAEnum(OutreachChannel), nullable=False)
    outreach_type: Mapped[OutreachType] = mapped_column(
        SAEnum(OutreachType), default=OutreachType.INITIAL_CONTACT
    )
    subject: Mapped[Optional[str]] = mapped_column(String(512))
    message: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[OutreachStatus] = mapped_column(
        SAEnum(OutreachStatus), default=OutreachStatus.PENDING, nullable=False
    )
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    reply_text: Mapped[Optional[str]] = mapped_column(Text)

    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<OutreachLog id={self.id} channel={self.channel} "
            f"candidate={self.candidate_id} status={self.status}>"
        )
