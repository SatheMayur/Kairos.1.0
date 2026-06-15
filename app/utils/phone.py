"""Canonical phone-number helpers (Indian mobiles + WhatsApp JIDs).

One place for the digit-wrangling that used to live in 4 different modules
(webhook, whatsapp_openclaw, duplicates, smart_scrape). Each had a slightly
different return contract, so we expose a few small functions rather than one
do-everything helper:

  jid_local(x)          → text before '@' in a WhatsApp JID (no digit cleaning)
  to_local_10(x)        → bare local digits, India '91' stripped; str (may be short/'')
  to_local_10_or_none(x)→ same but None unless exactly 10 digits
  to_chat_id(x)         → '91XXXXXXXXXX@c.us' for sending via the bridge/WAHA
  norm_phone(x)         → last-10 digits for matching; '' unless >= 10 digits
"""
from __future__ import annotations

import re
from typing import Optional


def jid_local(chat_id) -> str:
    """Local part of a WhatsApp JID, e.g. '919876543210@c.us' → '919876543210'.
    Drops '@c.us' / '@s.whatsapp.net' / '@lid' / '@broadcast'. No digit cleaning."""
    return (chat_id or "").split("@")[0]


def to_local_10(value) -> str:
    """Any phone or JID → bare local digits. Strips the @domain and the India
    country code, e.g. '919876543210@lid' → '9876543210'. Returns the last 10
    digits when there are at least 10, otherwise whatever digits remain (may be '')."""
    local = jid_local(value)
    digits = re.sub(r"\D", "", local)
    if digits.startswith("91") and len(digits) == 12:
        return digits[2:]
    return digits[-10:] if len(digits) >= 10 else digits


def to_local_10_or_none(value) -> Optional[str]:
    """Like to_local_10 but returns None unless exactly 10 digits remain."""
    digits = to_local_10(value)
    return digits if len(digits) == 10 else None


def to_chat_id(phone) -> str:
    """Normalise any Indian mobile to WAHA chatId format: '91XXXXXXXXXX@c.us'."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("91") and len(digits) == 12:
        return f"{digits}@c.us"
    if len(digits) == 10:
        return f"91{digits}@c.us"
    # Already has some other country code — send as-is
    return f"{digits}@c.us"


def norm_phone(phone) -> str:
    """Last-10-digit canonical form for matching/dedup; '' unless >= 10 digits."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    return digits[-10:] if len(digits) >= 10 else ""
