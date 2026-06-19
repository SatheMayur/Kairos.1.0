"""Canonical phone-number helpers (Indian mobiles + WhatsApp JIDs).

One place for the digit-wrangling that used to live in several modules
(webhook, whatsapp_openclaw, duplicates, smart_scrape, the CSV adapters). The
heart of it is ONE strict normalizer — ``normalize_indian_mobile`` — that turns
the messy reality of portal CSV phone fields into a clean 10-digit Indian
mobile, or ``None`` when the value isn't a usable mobile. Everything else
(dedup key, WhatsApp JID, "is this reachable") is derived from it so the WhatsApp
send path and the CSV adapters agree on exactly what counts as a valid number.

Real-world inputs handled:
    "9876543210"            → "9876543210"
    "+91 98765 43210"       → "9876543210"
    "+91-9876543210"        → "9876543210"
    "0 9876543210"          → "9876543210"   (trunk-prefix 0 dropped)
    "919876543210"          → "9876543210"   (country code stripped)
    "98765 43210"           → "9876543210"
    "9876543210.0"          → "9876543210"   (Excel float artifact)
    "9876543210 / 9123..."  → "9876543210"   (first valid of several)
    "NA" / "-" / "" / None  → None
    "0612345678" (landline) → None           (doesn't start 6-9)
    "12345" (<10 digits)    → None

Public functions:
  normalize_indian_mobile(x) → clean 10-digit mobile str, or None if not valid
  is_valid_mobile(x)         → bool (True iff normalize_indian_mobile returns a value)
  jid_local(x)               → text before '@' in a WhatsApp JID (no digit cleaning)
  to_local_10(x)             → bare local digits, India '91' stripped (legacy; lenient)
  to_local_10_or_none(x)     → strict 10-digit mobile or None (alias of normalize_indian_mobile)
  to_chat_id(x)              → '91XXXXXXXXXX@c.us' for the bridge/WAHA, or None if invalid
  norm_phone(x)              → canonical 10-digit form for matching/dedup; '' if not valid
"""
from __future__ import annotations

import re
from typing import Optional

# A token of digits that looks like it *could* be a phone number. Used to pull
# the first plausible candidate out of a multi-number cell like
# "9876543210 / 9123456789" or "9876543210, 022-12345678".
_DIGIT_RUN = re.compile(r"\d[\d\s\-().]{7,}\d|\d{8,}")


def _clean_digits(value) -> str:
    """All digits in *value* with the Excel ".0" float artifact removed.

    Excel/openpyxl turn a numeric phone cell into a float, so "9876543210"
    arrives as "9876543210.0". Naively stripping non-digits would yield
    "98765432100" (11 digits — a wrong number). We drop a single trailing
    ".0"/".00"… first, then strip everything non-numeric.
    """
    if value is None:
        return ""
    s = str(value).strip()
    # Drop a trailing ".0", ".00", … (float artifact) BEFORE removing punctuation.
    s = re.sub(r"\.0+$", "", s)
    return re.sub(r"\D", "", s)


def _digits_to_mobile(digits: str) -> Optional[str]:
    """Given a run of digits, return a clean 10-digit Indian mobile or None.

    Accepts an optional '91' country code and/or a leading trunk '0'. The bare
    10 digits must start with 6-9 (the valid Indian mobile range) — anything
    else (landline, short code, junk) returns None.
    """
    if not digits:
        return None
    # Strip a leading trunk '0' (e.g. "0 9876543210" → "09876543210").
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    # Strip the India country code in its common forms.
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("091"):
        digits = digits[3:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return None


def normalize_indian_mobile(value) -> Optional[str]:
    """THE normalizer. Any messy phone field → clean 10-digit Indian mobile, or None.

    Strips spaces/punctuation/'+'/leading '0', handles the Excel ".0" artifact,
    strips a '91' country code, and — when a cell holds several numbers — returns
    the FIRST one that is a valid Indian mobile. Returns None for junk ("NA",
    "-", ""), landlines, short codes, and anything not a 10-digit 6-9 mobile.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # Fast path: whole field is one number.
    direct = _digits_to_mobile(_clean_digits(s))
    if direct:
        return direct

    # Multi-number / messy cell: scan for the first token that normalizes.
    for match in _DIGIT_RUN.finditer(s):
        token = match.group(0)
        token = re.sub(r"\.0+$", "", token)
        mobile = _digits_to_mobile(re.sub(r"\D", "", token))
        if mobile:
            return mobile
    return None


def is_valid_mobile(value) -> bool:
    """True iff *value* normalizes to a usable Indian mobile."""
    return normalize_indian_mobile(value) is not None


def jid_local(chat_id) -> str:
    """Local part of a WhatsApp JID, e.g. '919876543210@c.us' → '919876543210'.
    Drops '@c.us' / '@s.whatsapp.net' / '@lid' / '@broadcast'. No digit cleaning."""
    return (chat_id or "").split("@")[0]


def to_local_10(value) -> str:
    """Any phone or JID → bare local digits (lenient; legacy callers).

    Prefers the strict mobile normalization; if that fails, falls back to the
    old behaviour (last 10 digits, India '91' stripped) so non-mobile JIDs from
    inbound webhooks/'@lid' privacy ids still resolve to *something* to match on.
    """
    mobile = normalize_indian_mobile(value)
    if mobile:
        return mobile
    local = jid_local(value)
    digits = re.sub(r"\.0+$", "", local)
    digits = re.sub(r"\D", "", digits)
    if digits.startswith("91") and len(digits) == 12:
        return digits[2:]
    return digits[-10:] if len(digits) >= 10 else digits


def to_local_10_or_none(value) -> Optional[str]:
    """Strict: a clean 10-digit Indian mobile, or None. (Same as normalize_indian_mobile.)"""
    return normalize_indian_mobile(value)


def to_chat_id(phone) -> Optional[str]:
    """Normalise any Indian mobile to WAHA/bridge chatId '91XXXXXXXXXX@c.us'.

    Returns None when *phone* is not a valid Indian mobile, so callers never
    queue a garbage JID (e.g. '@c.us' or '98765432100@c.us') to the bridge.
    """
    mobile = normalize_indian_mobile(phone)
    if not mobile:
        return None
    return f"91{mobile}@c.us"


def norm_phone(phone) -> str:
    """Canonical 10-digit form for matching/dedup; '' when not a valid mobile.

    Built on the strict normalizer so dedup keys agree with the send path —
    "9876543210", "+91 98765 43210" and the Excel "9876543210.0" all collapse to
    the same key. Falls back to last-10-digits for anything that isn't a clean
    mobile but still has >= 10 digits (so legacy/foreign numbers still dedup).
    """
    if not phone:
        return ""
    mobile = normalize_indian_mobile(phone)
    if mobile:
        return mobile
    digits = re.sub(r"\.0+$", "", str(phone))
    digits = re.sub(r"\D", "", digits)
    return digits[-10:] if len(digits) >= 10 else ""
