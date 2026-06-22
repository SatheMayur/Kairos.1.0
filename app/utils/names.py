"""Candidate name hygiene — WhatsApp push names are often handles, emojis, or
nicknames ("@lok", "mr_gk_borse"), not real names. These helpers detect those,
clean them for display, and pull a real name out of a chat message when stated.
"""
from __future__ import annotations

import re

# Strip emoji / pictographs / arrows / variation selectors / ZWJ.
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emoji & pictographs
    "\U00002600-\U000027BF"   # misc symbols & dingbats
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U00002190-\U000021FF"   # arrows
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0000200D"              # zero-width joiner
    "]",
    flags=re.UNICODE,
)
# Indic letter ranges we want to keep (Devanagari + Gujarati).
_INDIC = "ऀ-ॿ઀-૿"
_PLACEHOLDER_WORDS = {"whatsapp lead", "whatsapp user", "unknown", "candidate",
                      "lead", "user", "na", "n/a"}


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def clean_name(raw: str | None) -> str:
    """Make a raw push name presentable: drop emojis, turn @/_/. into spaces,
    remove stray symbols and digits, collapse whitespace. '' if nothing usable."""
    if not raw:
        return ""
    s = _EMOJI.sub("", str(raw))
    s = re.sub(r"[@_.]+", " ", s)
    s = re.sub(rf"[^A-Za-z{_INDIC}\s'\-]", " ", s)   # keep latin/indic letters + ' -
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_placeholder_name(name: str | None, phone: str | None = None) -> bool:
    """True when `name` is NOT a usable real name (handle, emoji, phone, generic)."""
    if not name or not name.strip():
        return True
    raw = name.strip()
    if raw.lower() in _PLACEHOLDER_WORDS:
        return True
    if raw.startswith("@") or "_" in raw:            # @handle / snake_handle
        return True
    if phone and _digits(name) and _digits(name) == _digits(phone):
        return True
    cleaned = clean_name(raw)
    letters = re.sub(rf"[^A-Za-z{_INDIC}]", "", cleaned)
    if len(letters) < 3:                             # emoji/symbol-only or too short
        return True
    if " " not in cleaned and cleaned == cleaned.lower():   # single lowercase token
        return True
    return False


def friendly_display_name(name: str | None, phone: str | None = None) -> str:
    """Readable UI label. Real name → cleaned. Handle → cleaned handle.
    Pure junk → 'WhatsApp user ••1234' (last 4 of phone)."""
    cleaned = clean_name(name)
    if not is_placeholder_name(name, phone):
        return cleaned or (name or "").strip()
    if cleaned and cleaned.lower() not in _PLACEHOLDER_WORDS and re.search(rf"[A-Za-z{_INDIC}]", cleaned):
        return cleaned
    tail = _digits(phone)[-4:]
    return f"WhatsApp user ••{tail}" if tail else "WhatsApp user"


# Only explicit name statements (NOT bare "i am ...", which matches "i am interested").
_NAME_PAT = re.compile(
    r"\b(?:my name is|my name's|name is|name\s*[:\-]|myself|i am called|this is)\s+"
    r"([A-Za-z][A-Za-z'.\-]+(?:\s+[A-Za-z][A-Za-z'.\-]+){0,3})",
    flags=re.IGNORECASE,
)
# Common words that mean the capture is a sentence, not a name.
_NOT_NAME = {
    "my", "the", "a", "an", "this", "that", "i", "we", "you", "interested",
    "currently", "available", "looking", "ready", "fine", "good", "from", "here",
    "working", "fully", "very", "resume", "cv", "just", "also", "still", "not",
    "now", "please", "sir", "madam", "hello", "hi", "thanks", "thank", "yes", "no",
    "experienced", "fresher", "experience", "applying", "application", "job", "role",
    "want", "need", "can", "will", "would", "have", "having", "am", "is", "are",
}


def extract_name_from_text(text: str | None) -> str | None:
    """Pull a stated name from a candidate's message ('my name is Rahul Shah').
    Rejects sentence fragments like 'interested in this opportunity'."""
    if not text:
        return None
    m = _NAME_PAT.search(text)
    if not m:
        return None
    cand = clean_name(m.group(1))
    if not cand or is_placeholder_name(cand):
        return None
    words = cand.split()
    if any(w.lower() in _NOT_NAME for w in words):     # any common word → not a name
        return None
    if not (1 <= len(words) <= 3):
        return None
    return " ".join(w.capitalize() for w in words)
