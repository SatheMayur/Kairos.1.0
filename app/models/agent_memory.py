"""AgentMemory — the WhatsApp agent's persistent, self-learning memory.

A namespaced key/value store that forms a "memory tree": every row is
(scope, key) → JSON value. Scopes act as branches, e.g.

  global         → aggregate insights the agent learns over time
  candidate:14   → everything learned about candidate 14 (CTC, notice, prefs…)
  sync           → last sync timestamp + the latest synced snapshot/deltas

It is written by:
  • the WhatsApp conversation agent after each reply (self-learning), and
  • the 20-minute sync job (snapshots WhatsApp / pipeline / interviews).

It is read by the morning briefing and (future) by the agent to inform replies.
Pure DB-backed so it survives restarts and is visible in the web UI.
"""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, JSON, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentMemory(Base):
    __tablename__ = "agent_memory"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_agent_memory_scope_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<AgentMemory {self.scope}/{self.key}>"
