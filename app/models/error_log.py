from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ErrorLog(Base):
    __tablename__ = "error_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    level: Mapped[str] = mapped_column(String(16), default="ERROR", index=True)
    source: Mapped[str] = mapped_column(String(128), index=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    traceback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # HTTP context (populated when error comes from a request)
    method: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
