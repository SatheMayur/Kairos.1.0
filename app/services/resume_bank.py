"""Resume Bank service — extract text from CVs, parse light fields, and store.

Stores extracted TEXT + metadata only (never the binary file), keeping Neon
light. A sha256 of the normalized text catches the same CV arriving twice.

Public surface:
  extract_text(file_bytes, filename) -> str
  parse_resume(text) -> dict   (best-effort; missing fields are None)
  async ingest_resume(...)     (never raises — returns {"ok": False, "reason": ...})
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate, CandidateSource
from app.models.resume_doc import ResumeDoc
from app.utils.phone import normalize_indian_mobile
from app.utils.logging import get_logger

logger = get_logger(__name__)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Runs of digits that might be a phone number (allow spaces/dashes/+/() inside).
_PHONE_RUN_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")
# "5 years", "3+ yrs", "2-4 years of experience"
_EXP_RE = re.compile(r"(\d{1,2})(?:\s*\+|\s*-\s*\d{1,2})?\s*(?:\+)?\s*(?:years?|yrs?)", re.I)

# Small fallback skill set, used only if the JD analyzer tokens aren't importable.
_FALLBACK_SKILLS = [
    "python", "java", "javascript", "sql", "excel", "react", "node.js",
    "autocad", "solidworks", "catia", "fusion 360", "creo", "photoshop",
    "illustrator", "figma", "tally", "payroll", "recruitment", "hr",
    "communication", "ms office", "word", "powerpoint",
]


def _skill_tokens() -> list[str]:
    """Reuse the JD analyzer's known-skill tokens when importable, else fallback."""
    try:
        from app.services.jd_analyzer import _SKILL_TOKENS
        return list(_SKILL_TOKENS)
    except Exception:
        return list(_FALLBACK_SKILLS)


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Pull plain text out of a PDF / DOCX / DOC / TXT (or anything decodable).

    Mirrors the robust read used in app/api/jobs.py. Never raises — returns the
    text it could get, or "" on failure.
    """
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    text = ""
    try:
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        elif ext == "docx":
            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif ext == "doc":
            # Old binary .doc — no reliable pure-Python parser; best-effort.
            text = "".join(
                ch for ch in file_bytes.decode("latin-1", errors="ignore")
                if ch.isprintable() or ch in "\n\r\t "
            )
        else:
            # txt or unknown — just decode.
            text = file_bytes.decode("utf-8", errors="ignore")
    except Exception as exc:
        logger.warning("Resume text extraction failed (%s): %s", ext, exc)
        return ""
    return text or ""


def _normalize_for_hash(text: str) -> str:
    """Collapse whitespace + lowercase so trivial reformatting still dedups."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_for_hash(text).encode("utf-8")).hexdigest()


def _looks_like_name(line: str) -> bool:
    """Heuristic: 2-4 words, mostly letters, no email/phone/digits, title-ish."""
    s = line.strip()
    if not s or len(s) > 60:
        return False
    if "@" in s or re.search(r"\d", s):
        return False
    words = s.split()
    if not (1 <= len(words) <= 4):
        return False
    # Reject lines that are clearly headings/labels.
    low = s.lower()
    if any(w in low for w in ("resume", "curriculum", "vitae", "profile", "objective", "summary")):
        return False
    letters = sum(c.isalpha() for c in s)
    return letters >= max(2, int(len(s.replace(" ", "")) * 0.7))


def parse_resume(text: str) -> dict:
    """Best-effort, rules-based extraction. Missing fields are None."""
    text = text or ""

    # Email
    email = None
    m = _EMAIL_RE.search(text)
    if m:
        email = m.group(0).strip().rstrip(".").lower()

    # Phone — scan digit runs through the canonical Indian-mobile normalizer.
    phone = None
    for run in _PHONE_RUN_RE.finditer(text):
        mob = normalize_indian_mobile(run.group(0))
        if mob:
            phone = mob
            break

    # Name — first non-empty top line that looks like a name.
    name = None
    for line in text.splitlines()[:12]:
        if _looks_like_name(line):
            name = line.strip()
            break

    # Skills — known-token matches (case-insensitive, word-ish boundaries).
    low = text.lower()
    skills: list[str] = []
    for tok in _skill_tokens():
        if tok.lower() in low and tok not in skills:
            skills.append(tok)

    # Experience — first "X years" figure.
    experience_years = None
    em = _EXP_RE.search(text)
    if em:
        try:
            experience_years = float(em.group(1))
        except ValueError:
            experience_years = None

    return {
        "email": email,
        "phone": phone,
        "name": name,
        "skills": skills or None,
        "experience_years": experience_years,
    }


async def _find_candidate(
    db: AsyncSession, *, email: Optional[str], phone: Optional[str]
) -> Optional[Candidate]:
    """Match an existing candidate by email or normalized phone/whatsapp."""
    if email:
        row = (await db.execute(
            select(Candidate).where(Candidate.email == email)
        )).scalar_one_or_none()
        if row:
            return row

    if phone:
        # Compare on the normalized 10-digit form against phone & whatsapp.
        candidates = (await db.execute(select(Candidate))).scalars().all()
        for c in candidates:
            for field in (c.phone, c.whatsapp):
                if field and normalize_indian_mobile(field) == phone:
                    return c
    return None


async def ingest_resume(
    *,
    file_bytes: bytes,
    filename: str,
    source: str = "UPLOAD",
    from_contact: Optional[str] = None,
    db: AsyncSession,
) -> dict:
    """Extract → dedup → link/create candidate → store. Never raises.

    Returns one of:
      {"ok": False, "reason": "..."}
      {"ok": True, "duplicate": True, "resume_id": int}
      {"ok": True, "resume_id", "candidate_id", "candidate_name", "matched": bool}
    """
    try:
        text = extract_text(file_bytes, filename)
        if len((text or "").strip()) < 20:
            return {"ok": False, "reason": "no readable text"}

        thash = _text_hash(text)

        # Dedup: same CV text already stored?
        dup = (await db.execute(
            select(ResumeDoc).where(ResumeDoc.text_hash == thash)
        )).scalar_one_or_none()
        if dup is not None:
            return {"ok": True, "duplicate": True, "resume_id": dup.id}

        parsed = parse_resume(text)

        # The contact it arrived from may itself be a phone or email we can match.
        contact_phone = normalize_indian_mobile(from_contact) if from_contact else None
        contact_email = None
        if from_contact and "@" in from_contact:
            contact_email = from_contact.strip().lower()

        match_email = parsed.get("email") or contact_email
        match_phone = parsed.get("phone") or contact_phone

        candidate = await _find_candidate(db, email=match_email, phone=match_phone)
        matched = candidate is not None

        if candidate is None:
            src = CandidateSource.MANUAL
            candidate = Candidate(
                name=parsed.get("name") or "Unknown (from CV)",
                email=match_email,
                phone=match_phone,
                whatsapp=match_phone,
                skills=parsed.get("skills") or [],
                experience_years=parsed.get("experience_years"),
                raw_profile=text[:20000],
                source=src,
            )
            db.add(candidate)
            await db.flush()  # assign id

        doc = ResumeDoc(
            candidate_id=candidate.id,
            source=(source or "UPLOAD").upper(),
            filename=filename,
            content_type=None,
            from_contact=from_contact,
            text=text,
            text_hash=thash,
            size_bytes=len(file_bytes) if file_bytes else 0,
            received_at=datetime.utcnow(),
        )
        db.add(doc)
        await db.flush()
        await db.commit()

        return {
            "ok": True,
            "resume_id": doc.id,
            "candidate_id": candidate.id,
            "candidate_name": candidate.name,
            "matched": matched,
        }
    except Exception as exc:
        logger.warning("ingest_resume failed: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass
        return {"ok": False, "reason": "Something went wrong while reading this CV. Please try again."}
