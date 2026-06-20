"""ResumeDoc model — the Resume Bank.

Stores the EXTRACTED TEXT of a CV plus light metadata (never the binary file).
Each row can be linked to a Candidate. A sha256 of the normalized text is kept
so the same CV arriving twice (upload again, or via WhatsApp + email) is caught
as a duplicate instead of creating a second row.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ResumeDoc(Base):
    __tablename__ = "resume_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("candidates.id"), nullable=True, index=True
    )

    # Where it came from: UPLOAD / WHATSAPP / EMAIL
    source: Mapped[str] = mapped_column(String(20), default="UPLOAD")
    filename: Mapped[Optional[str]] = mapped_column(String(512))
    content_type: Mapped[Optional[str]] = mapped_column(String(120))
    # The phone or email address it arrived from (for WhatsApp / email sources).
    from_contact: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    text: Mapped[Optional[str]] = mapped_column(Text)
    # sha256 of the normalized text — used to catch the same CV arriving twice.
    text_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)

    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ResumeDoc id={self.id} candidate_id={self.candidate_id} source={self.source}>"
