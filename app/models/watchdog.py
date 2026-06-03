"""Watchdog health tracking — last run, last success, heal actions taken."""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class WatchdogLog(Base):
    __tablename__ = "watchdog_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_name: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))     # OK / WARN / HEALED / ALERT
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    healed: Mapped[bool] = mapped_column(default=False)
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
