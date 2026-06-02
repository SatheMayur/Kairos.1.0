"""CSV / batch import endpoint — feed Naukri & WorkIndia downloads into the pipeline.

POST /api/v1/import/csv
  - Upload a CSV file from Naukri or WorkIndia employer dashboard
  - System parses, deduplicates, scores, shortlists, and queues outreach automatically

POST /api/v1/import/batch
  - Submit a JSON array of candidate objects (for manual / scripted entry)

Both endpoints return a per-candidate summary: scored, shortlisted, outreach queued.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import RawCandidate
from app.adapters.naukri import NaukriCSVAdapter
from app.adapters.workindia import WorkIndiaCSVAdapter
from app.api.deps import get_db
from app.config import get_settings
from app.models.candidate import CandidateSource
from app.models.outreach import OutreachChannel, OutreachType
from app.models.shortlist import ShortlistStatus
from app.services.outreach import send_outreach
from app.services.sourcing import _score_and_shortlist, _upsert_candidate
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/import", tags=["import"])

_NAUKRI_ADAPTER = NaukriCSVAdapter()
_WORKINDIA_ADAPTER = WorkIndiaCSVAdapter()


class BatchCandidate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    skills: list[str] = []
    experience_years: Optional[float] = None
    current_salary: Optional[float] = None
    expected_salary: Optional[float] = None
    location: Optional[str] = None
    notice_period_days: Optional[int] = None
    current_employer: Optional[str] = None
    current_role: Optional[str] = None
    source: str = "MANUAL"


class ImportResult(BaseModel):
    total_parsed: int
    inserted: int
    duplicates_skipped: int
    auto_shortlisted: int
    pending_review: int
    rejected: int
    outreach_queued: int
    details: list[dict]


async def _run_import_pipeline(
    raw_candidates: list[RawCandidate],
    job_id: int,
    auto_outreach: bool,
    db: AsyncSession,
) -> ImportResult:
    """Score every candidate, shortlist, and optionally queue outreach."""
    from sqlalchemy import select
    from app.models.job import Job

    job_res = await db.execute(select(Job).where(Job.id == job_id))
    job = job_res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    inserted = 0
    duplicates = 0
    auto_sl = 0
    pending = 0
    rejected = 0
    outreach_queued = 0
    details = []

    for raw in raw_candidates:
        # Upsert candidate (skip exact duplicates by email/source_ref)
        existing_before = True
        try:
            from sqlalchemy import select as sa_select
            from app.models.candidate import Candidate
            exists = False
            if raw.email:
                r = await db.execute(
                    sa_select(Candidate).where(Candidate.email == raw.email)
                )
                exists = r.scalar_one_or_none() is not None
            if not exists and raw.source_ref:
                r = await db.execute(
                    sa_select(Candidate).where(Candidate.source_ref == raw.source_ref)
                )
                exists = r.scalar_one_or_none() is not None
            if exists:
                duplicates += 1
                details.append({"name": raw.name, "result": "DUPLICATE_SKIPPED"})
                continue
            existing_before = False
        except Exception:
            pass

        candidate = await _upsert_candidate(raw, db)
        if not existing_before:
            inserted += 1

        entry = await _score_and_shortlist(candidate, job, db)
        if not entry:
            details.append({"name": raw.name, "result": "ALREADY_SCORED"})
            continue

        if entry.status == ShortlistStatus.SHORTLISTED:
            auto_sl += 1
            result_label = "AUTO_SHORTLISTED"
        elif entry.status == ShortlistStatus.PENDING:
            pending += 1
            result_label = "PENDING_REVIEW"
        else:
            rejected += 1
            result_label = "REJECTED"

        # Queue outreach for auto-shortlisted candidates
        if auto_outreach and entry.status == ShortlistStatus.SHORTLISTED:
            from app.models.shortlist import ShortlistEntry
            try:
                log = await send_outreach(
                    candidate=candidate,
                    job=job,
                    channel=OutreachChannel.WHATSAPP,
                    outreach_type=OutreachType.INITIAL_CONTACT,
                    db=db,
                )
                if log.status.value == "SENT":
                    outreach_queued += 1
                    entry.status = ShortlistStatus.CONTACTED
            except Exception as exc:
                logger.warning("Outreach failed for %s: %s", candidate.name, exc)

        details.append({
            "name": raw.name,
            "email": raw.email,
            "score": entry.score,
            "result": result_label,
        })

    await db.commit()

    return ImportResult(
        total_parsed=len(raw_candidates),
        inserted=inserted,
        duplicates_skipped=duplicates,
        auto_shortlisted=auto_sl,
        pending_review=pending,
        rejected=rejected,
        outreach_queued=outreach_queued,
        details=details,
    )


@router.post("/csv", response_model=ImportResult)
async def import_csv(
    file: UploadFile = File(..., description="CSV file downloaded from Naukri or WorkIndia"),
    job_id: int = Form(..., description="Job ID to score candidates against"),
    source: str = Form("NAUKRI", description="Portal source: NAUKRI or WORKINDIA"),
    auto_outreach: bool = Form(True, description="Queue outreach for AUTO_SHORTLIST candidates"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a Naukri or WorkIndia CSV export.

    The system will:
    1. Parse every row using the correct column mapping
    2. Deduplicate against existing candidates (by email / source_ref)
    3. Score each new candidate against the specified job
    4. Auto-shortlist (score ≥ 65) or flag for manual review (40–64)
    5. Optionally queue outreach emails for auto-shortlisted candidates immediately
    """
    src = source.upper().strip()
    if src not in ("NAUKRI", "WORKINDIA"):
        raise HTTPException(status_code=400, detail="source must be NAUKRI or WORKINDIA")

    content = await file.read()
    try:
        csv_text = content.decode("utf-8-sig")  # handle BOM from Windows Excel exports
    except UnicodeDecodeError:
        csv_text = content.decode("latin-1")

    if src == "NAUKRI":
        raw_candidates = _NAUKRI_ADAPTER.parse_csv(csv_text)
    else:
        raw_candidates = _WORKINDIA_ADAPTER.parse_csv(csv_text)

    if not raw_candidates:
        raise HTTPException(status_code=400, detail="No candidates found in CSV — check format")

    logger.info("CSV import: source=%s job=%d candidates=%d", src, job_id, len(raw_candidates))
    return await _run_import_pipeline(raw_candidates, job_id, auto_outreach, db)


@router.post("/batch", response_model=ImportResult)
async def import_batch(
    candidates: list[BatchCandidate],
    job_id: int,
    auto_outreach: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Submit candidates as a JSON array (manual entry or scripted copy-paste).

    Useful for WorkIndia's 3 handpicked candidates or any portal without CSV export.
    """
    raw: list[RawCandidate] = []
    for c in candidates:
        try:
            src = CandidateSource(c.source.upper())
        except ValueError:
            src = CandidateSource.MANUAL
        raw.append(
            RawCandidate(
                name=c.name,
                source=src,
                email=c.email,
                phone=c.phone,
                skills=c.skills,
                experience_years=c.experience_years,
                current_salary=c.current_salary,
                expected_salary=c.expected_salary,
                location=c.location,
                notice_period_days=c.notice_period_days,
                current_employer=c.current_employer,
                current_role=c.current_role,
                source_ref=f"{src.value.lower()}:{c.email or c.phone or c.name}",
            )
        )

    logger.info("Batch import: job=%d candidates=%d", job_id, len(raw))
    return await _run_import_pipeline(raw, job_id, auto_outreach, db)
