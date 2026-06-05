"""Conversation — per candidate+job WhatsApp thread memory for the Conversation Agent.

Stores the running message history and the facts collected so far (CTC, notice
period, location, availability) so the agent reasons over the whole conversation
instead of treating each reply in isolation.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(Integer, ForeignKey("candidates.id"), index=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id"), index=True)

    # Facts gathered from the candidate: expected_ctc, current_ctc, notice_period, location, availability
    collected: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    # Running thread: [{dir: "in"|"out", text: str, ts: iso}] (capped to recent turns)
    history: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(String(24), default="ACTIVE")  # ACTIVE/SCHEDULING/SCHEDULED/NOT_INTERESTED/NEEDS_HUMAN/CLOSED
    last_intent: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
