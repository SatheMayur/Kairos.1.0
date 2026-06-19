"""AppSetting — a minimal server-side key/value store.

Used to persist small operational settings that must survive across requests and
serverless instances (e.g. the Apna live-search token + org id) so automatic,
server-side sourcing can use them. NOT for secrets that belong in env vars.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<AppSetting key={self.key!r}>"
