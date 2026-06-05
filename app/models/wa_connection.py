"""WhatsApp connection state — stores QR data and bridge status for the dashboard."""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class WaConnection(Base):
    __tablename__ = "wa_connection"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(20), default="DISCONNECTED")
    qr_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # One-shot command the dashboard leaves for the bridge to pick up (e.g. "RELINK")
    pending_command: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
