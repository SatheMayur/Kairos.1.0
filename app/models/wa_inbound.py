"""WaInbound — idempotency guard for inbound WhatsApp messages.

Baileys can deliver the same message more than once (reconnects, duplicate
upserts), and each delivery used to trigger another auto-reply — so candidates
saw the same message twice. We record each WhatsApp message id once (unique); a
second arrival is detected and skipped.
"""
from datetime import datetime

from sqlalchemy import Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WaInbound(Base):
    __tablename__ = "wa_inbound"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
