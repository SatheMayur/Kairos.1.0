"""DailyPlan — the orchestrator's reasoning + prioritised plan for a given day."""
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class DailyPlan(Base):
    __tablename__ = "daily_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # Plain-English summary the recruitment-manager agent wrote for Kirti
    manager_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Prioritised list: [{title, why, action}]
    priorities: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    # Raw situation snapshot the plan was based on (for audit / dashboard)
    situation: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    # "ai" when Claude reasoned it, "rules" when the deterministic fallback built it
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
