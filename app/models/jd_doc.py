"""JDDoc model — the Job Description Bank.

Keeps the raw text of every job description that came through the system (pasted
or uploaded), so the owner can look back at what a role was advertised as.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class JDDoc(Base):
    __tablename__ = "jd_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    # How it arrived: UPLOAD / PASTE
    source: Mapped[str] = mapped_column(String(20), default="PASTE")
    job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<JDDoc id={self.id} title={self.title!r} source={self.source}>"
