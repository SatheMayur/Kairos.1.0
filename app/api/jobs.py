"""Jobs API — CRUD + JD analysis + trigger sourcing."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.job import Job
from app.schemas.job import JobCreate, JobRead, JobUpdate, JDAnalysisResult
from app.services.jd_analyzer import analyze_jd
from app.services.sourcing import source_candidates_for_job
from app.utils.logging import get_logger

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = get_logger(__name__)


async def _bank_jd(db: AsyncSession, *, title, raw_text, source, job_id=None) -> None:
    """Save a JD into the JD Bank. Non-fatal — never blocks job creation."""
    try:
        if raw_text and raw_text.strip():
            from app.api.resumes import store_jd
            await store_jd(db, title, raw_text, source, job_id=job_id)
    except Exception as exc:
        logger.warning("Could not save JD to the JD Bank: %s", exc)


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    """Create a job requisition (fields pre-filled or from JD analysis)."""
    job = Job(**payload.model_dump())
    db.add(job)
    await db.flush()
    await _bank_jd(db, title=job.title, raw_text=job.raw_jd, source="PASTE", job_id=job.id)
    return job


@router.post("/analyze-jd", response_model=JDAnalysisResult)
async def analyze_jd_text(raw_jd: str, db: AsyncSession = Depends(get_db)):
    """Parse a JD string and return extracted fields (rules-based; does not save)."""
    parsed = analyze_jd(raw_jd)
    await _bank_jd(db, title=parsed.title, raw_text=raw_jd, source="PASTE")
    return parsed


@router.post("/analyze-jd-file", response_model=JDAnalysisResult)
async def analyze_jd_file(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Upload a JD file (PDF / DOC / DOCX / TXT), extract its text, and return the
    parsed job fields for the recruiter to review (does NOT create the job)."""
    import io

    filename = (file.filename or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in ("pdf", "doc", "docx", "txt"):
        raise HTTPException(status_code=400,
                            detail="Unsupported file type. Please upload a PDF, DOC, DOCX or TXT file.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="That file is empty — please choose a file with the job description in it.")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="That file is too large (over 5 MB). Please upload a smaller file.")

    text = ""
    try:
        if ext == "txt":
            text = content.decode("utf-8", errors="ignore")
        elif ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        elif ext == "docx":
            import docx
            doc = docx.Document(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif ext == "doc":
            # Old binary .doc — no reliable pure-Python parser; best-effort, else ask for PDF/DOCX.
            text = "".join(ch for ch in content.decode("latin-1", errors="ignore") if ch.isprintable() or ch in "\n\r\t ")
    except Exception as exc:
        logger.warning("JD file extraction failed (%s): %s", ext, exc)
        raise HTTPException(status_code=422,
                            detail="Couldn't read text from this file — it may be corrupt or password-protected. Try re-saving it as a PDF or DOCX.")

    if len((text or "").strip()) < 20:
        raise HTTPException(status_code=422,
                            detail=("Couldn't find readable text in this file."
                                    + (" Old .doc files often don't read well — please save it as PDF or DOCX and try again." if ext == "doc" else "")))

    parsed = analyze_jd(text)
    await _bank_jd(db, title=parsed.title, raw_text=text, source="UPLOAD")
    return parsed


@router.post("/from-jd", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job_from_jd(raw_jd: str, db: AsyncSession = Depends(get_db)):
    """Analyze JD text and immediately create + save a job record."""
    parsed = analyze_jd(raw_jd)
    job = Job(
        title=parsed.title or "Untitled Role",
        raw_jd=raw_jd,
        skills=parsed.skills,
        experience_min=parsed.experience_min,
        experience_max=parsed.experience_max,
        salary_min=parsed.salary_min,
        salary_max=parsed.salary_max,
        location=parsed.location,
        notice_period_days=parsed.notice_period_days,
        education=parsed.education,
        job_type=parsed.job_type,
        description=parsed.description,
    )
    db.add(job)
    await db.flush()
    await _bank_jd(db, title=job.title, raw_text=raw_jd, source="PASTE", job_id=job.id)
    return job


@router.get("", response_model=list[JobRead])
async def list_jobs(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    query = select(Job).offset(skip).limit(limit).order_by(Job.created_at.desc())
    if status:
        query = query.where(Job.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await _get_or_404(job_id, db)
    return job


@router.patch("/{job_id}", response_model=JobRead)
async def update_job(job_id: int, payload: JobUpdate, db: AsyncSession = Depends(get_db)):
    job = await _get_or_404(job_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(job, field, value)
    return job


@router.post("/{job_id}/archive", response_model=JobRead)
async def archive_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Archive a job: mark it CLOSED and hide it from active lists. Reversible,
    keeps all candidates/history. This is the safe alternative to deleting a job
    that already has applicants."""
    from app.models.job import JobStatus
    job = await _get_or_404(job_id, db)
    job.status = JobStatus.CLOSED
    return job


@router.post("/{job_id}/reopen", response_model=JobRead)
async def reopen_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Re-activate an archived/paused job."""
    from app.models.job import JobStatus
    job = await _get_or_404(job_id, db)
    job.status = JobStatus.ACTIVE
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: int, force: bool = False, db: AsyncSession = Depends(get_db)):
    """Delete a job. Guarded: if the job has any candidates/applications, deletion
    is refused (409) and the recruiter is told to archive instead — unless force=true.

    When deletion proceeds, every dependent row (shortlist, outreach, interviews,
    conversations) is removed first in the same transaction, so the delete can never
    leave orphans or hit a foreign-key violation (which previously 500'd on Postgres)."""
    from sqlalchemy import delete as sa_delete, func
    from app.models.shortlist import ShortlistEntry
    from app.models.outreach import OutreachLog
    from app.models.interview import Interview
    from app.models.conversation import Conversation

    job = await _get_or_404(job_id, db)

    app_count = (await db.execute(
        select(func.count()).select_from(ShortlistEntry).where(ShortlistEntry.job_id == job_id)
    )).scalar_one()

    if app_count and not force:
        raise HTTPException(
            status_code=409,
            detail=(f"This job has {app_count} candidate(s) attached. Archive it instead to keep "
                    f"their history, or delete with force=true to permanently remove the job and "
                    f"all {app_count} of its candidate records."),
        )

    # Safe to remove: clear every table that references jobs.id, then the job.
    for Model in (ShortlistEntry, OutreachLog, Interview, Conversation):
        await db.execute(sa_delete(Model).where(Model.job_id == job_id))
    await db.delete(job)


@router.post("/{job_id}/source", summary="Trigger candidate sourcing for a job")
async def trigger_sourcing(job_id: int, outreach: Optional[bool] = None, db: AsyncSession = Depends(get_db)):
    """Fan out to all portal adapters (incl. Apollo.io), score, persist — and
    auto-contact the good, reachable matches (WhatsApp-first) unless outreach=false."""
    from app.config import get_settings
    job = await _get_or_404(job_id, db)
    do_outreach = get_settings().auto_outreach_enabled if outreach is None else outreach
    entries = await source_candidates_for_job(job, db, auto_outreach=do_outreach)
    return {"sourced": len(entries), "job_id": job_id, "outreach": do_outreach}


@router.post("/{job_id}/contact-all", summary="Contact all reachable, not-yet-contacted candidates for a job")
async def contact_all_for_job(
    job_id: int,
    include_pending: bool = False,
    include_paused: bool = False,
    limit: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Send initial outreach to reachable, not-yet-contacted SHORTLISTED candidates
    for this job, on the live channel (WhatsApp if connected, else email).

    Safe by default: only human-approved (SHORTLISTED) candidates on an ACTIVE job
    are contacted. Set ``include_pending=true`` to also message AI-suggested
    (PENDING) candidates, and ``include_paused=true`` to override the paused-job
    guard. Idempotent — anyone already contacted or unreachable is skipped."""
    from app.models.shortlist import ShortlistEntry, ShortlistStatus
    from app.models.job import JobStatus
    from app.services.auto_outreach import (
        contact_job_entries, _DEFAULT_BULK_STATUSES, _ALL_OPEN_STATUSES,
    )

    job = await _get_or_404(job_id, db)
    statuses = _ALL_OPEN_STATUSES if include_pending else _DEFAULT_BULK_STATUSES
    entries = (await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.job_id == job_id,
            ShortlistEntry.status.in_(list(statuses)),
        )
    )).scalars().all()
    res = await contact_job_entries(
        db, job, entries, statuses=statuses, require_active_job=not include_paused,
        limit=limit,
    )
    if res.get("skipped_inactive_job"):
        res["message"] = (
            f"This job is {job.status.value if hasattr(job.status,'value') else job.status} — "
            "nobody was contacted. Reopen the job (or pass include_paused) to contact its candidates."
        )
    return res


async def _job_entries_and_candidates(job_id: int, db: AsyncSession):
    """Load every shortlist entry for a job plus the matching candidate rows."""
    from app.models.shortlist import ShortlistEntry
    from app.models.candidate import Candidate

    er = await db.execute(
        select(ShortlistEntry).where(ShortlistEntry.job_id == job_id)
        .order_by(ShortlistEntry.score.desc())
    )
    entries = er.scalars().all()
    cand_ids = list({e.candidate_id for e in entries})
    cands: dict[int, Candidate] = {}
    if cand_ids:
        cr = await db.execute(select(Candidate).where(Candidate.id.in_(cand_ids)))
        cands = {c.id: c for c in cr.scalars().all()}
    return entries, cands


@router.get("/{job_id}/stats", summary="Job intelligence: bands, pipeline, insights")
async def job_stats(job_id: int, db: AsyncSession = Depends(get_db)):
    """For ONE job: how many strong/medium/weak matches, the pipeline breakdown,
    how many are reachable, and plain-English insights + recommended next steps."""
    from app.services.job_intelligence import (
        compute_stats, build_insights, build_recommendations, band_of,
    )
    from app.services.data_quality import is_reachable
    from app.models.shortlist import ShortlistStatus

    job = await _get_or_404(job_id, db)
    entries, cands = await _job_entries_and_candidates(job_id, db)

    stats = compute_stats(entries, cands)

    # Action-ready tallies the recommendations need (reachability is per-candidate).
    counts = {
        "pending_strong_reachable": 0,
        "shortlisted_reachable": 0,
        "interested": 0,
    }
    for e in entries:
        c = cands.get(e.candidate_id)
        ok = is_reachable(c) if c is not None else False
        if e.status == ShortlistStatus.PENDING and ok and band_of(e.score) == "strong":
            counts["pending_strong_reachable"] += 1
        elif e.status == ShortlistStatus.SHORTLISTED and ok:
            counts["shortlisted_reachable"] += 1
        elif e.status == ShortlistStatus.INTERESTED:
            counts["interested"] += 1

    return {
        "job": {
            "id": job.id, "title": job.title, "company": job.company,
            "location": job.location, "status": job.status,
            "skills": job.skills or [],
            "experience_min": job.experience_min, "experience_max": job.experience_max,
            "salary_min": job.salary_min, "salary_max": job.salary_max,
        },
        **stats,
        "insights": build_insights(stats),
        "recommendations": build_recommendations(stats, counts),
    }


@router.get("/{job_id}/candidates", summary="Matching candidates for a job, enriched")
async def job_candidates(
    job_id: int,
    band: Optional[str] = None,            # strong | medium | weak
    status: Optional[str] = None,
    reachable_only: bool = False,
    needs_contact: bool = False,
    include_closed: bool = True,
    q: Optional[str] = None,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    """The ranked candidate list for a job — match score, per-dimension match,
    status, last activity, and the recommended next action. Built in a handful of
    queries (no per-candidate round-trips)."""
    from app.models.shortlist import ShortlistStatus
    from app.models.outreach import OutreachLog
    from app.services.data_quality import is_reachable
    from app.services.job_intelligence import (
        band_of, match_dimensions, recommended_action, STATUS_LABEL, CLOSED_STATUSES,
    )

    await _get_or_404(job_id, db)
    entries, cands = await _job_entries_and_candidates(job_id, db)

    # Latest activity per candidate for this job — one query, grouped in Python.
    last_activity: dict[int, dict] = {}
    if cands:
        olr = await db.execute(
            select(OutreachLog).where(OutreachLog.job_id == job_id)
            .order_by(OutreachLog.created_at.desc())
        )
        for o in olr.scalars().all():
            if o.candidate_id in last_activity:
                continue  # already have the most-recent one (ordered desc)
            ts = o.sent_at or o.replied_at or o.created_at
            label = {"EMAIL": "Emailed", "WHATSAPP": "WhatsApp sent"}.get(
                o.channel.value, o.channel.value.title())
            if o.status.value == "REPLIED" or o.replied_at:
                label = "Replied"
            elif o.status.value in ("FAILED", "BOUNCED"):
                label = "Message failed"
            last_activity[o.candidate_id] = {
                "ts": ts.isoformat() if ts else None, "label": label,
            }

    rows = []
    for e in entries:
        c = cands.get(e.candidate_id)
        if c is None:
            continue
        b = band_of(e.score)
        ok = is_reachable(c)
        st = e.status
        if band and b != band:
            continue
        if status and st.value != status:
            continue
        if reachable_only and not ok:
            continue
        if needs_contact and (ok or st in (
            ShortlistStatus.HIRED, ShortlistStatus.REJECTED,
            ShortlistStatus.NOT_INTERESTED, ShortlistStatus.DROPPED)):
            continue
        if not include_closed and st in CLOSED_STATUSES:
            continue
        if q:
            ql = q.lower()
            hay = " ".join(filter(None, [
                c.name, c.current_role, c.current_employer, c.location,
                " ".join(c.skills or []),
            ])).lower()
            if ql not in hay:
                continue

        bd = e.score_breakdown or {}
        la = last_activity.get(c.id)
        rows.append({
            "entry_id": e.id,
            "candidate_id": c.id,
            "name": c.name,
            "score": round(e.score) if e.score is not None else None,
            "band": b,
            "status": st.value,
            "status_label": STATUS_LABEL.get(st, st.value),
            "reachable": ok,
            "needs_contact": (not ok) and st not in (
                ShortlistStatus.HIRED, ShortlistStatus.REJECTED,
                ShortlistStatus.NOT_INTERESTED, ShortlistStatus.DROPPED),
            "match": match_dimensions(bd),
            "skills": c.skills or [],
            "experience_years": c.experience_years,
            "location": c.location,
            "current_role": c.current_role,
            "current_employer": c.current_employer,
            "expected_salary": c.expected_salary,
            "email": c.email,
            "phone": c.phone,
            "whatsapp": c.whatsapp,
            "source": c.source.value if c.source else None,
            "ai_strengths": bd.get("ai_strengths", []),
            "ai_concerns": bd.get("ai_concerns", []),
            "ai_reasoning": bd.get("ai_reasoning", ""),
            "last_activity": la,
            "recommended_action": recommended_action(st, e.score, ok),
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        })

    rows.sort(key=lambda r: (r["score"] or 0), reverse=True)
    return {"job_id": job_id, "count": len(rows), "candidates": rows[:limit]}


async def _get_or_404(job_id: int, db: AsyncSession) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job
