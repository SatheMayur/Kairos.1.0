"""Sourcing service — orchestrates all portal adapters and persists candidates.

This is the ONLY place that calls adapters and writes to the candidates table.
"""
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.adapters.base import RawCandidate
from app.adapters.registry import get_registry
from app.models.candidate import Candidate
from app.models.job import Job
from app.services.scoring import score_candidate
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def source_candidates_for_job(
    job: Job, db: AsyncSession, *, auto_outreach: bool = False
) -> list[ShortlistEntry]:
    """Search all active adapters for candidates matching the job, then score and persist.

    Returns the resulting ShortlistEntry rows. When ``auto_outreach`` is True and
    the owner has auto-outreach enabled, freshly-sourced reachable good matches are
    contacted immediately (the on-add automation); never raises because of it.
    """
    registry = get_registry()
    keywords = (job.skills or []) + ([job.title] if job.title else [])

    # Fan out to all adapters concurrently
    tasks = [
        adapter.search(
            keywords=keywords,
            location=job.location,
            experience_min=job.experience_min,
            experience_max=job.experience_max,
            limit=20,
        )
        for adapter in registry.values()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    raw_candidates: list[RawCandidate] = []
    for adapter_name, result in zip(registry.keys(), results):
        if isinstance(result, Exception):
            logger.warning("Adapter %s failed: %s", adapter_name, result)
        else:
            raw_candidates.extend(result)

    # Apna live search — only available if a token has been saved server-side
    # (via the Import page → "Save token"). The registry can't hold it because the
    # token isn't an env var, so we look it up per-run from the DB and run Apna here.
    apna_raw = await _source_from_apna(job, keywords, db)
    raw_candidates.extend(apna_raw)

    # Location post-filter: drop candidates clearly from the wrong city.
    # Candidates with no location field are kept (scored lower, not discarded).
    if job.location:
        job_city = job.location.lower().split(",")[0].strip()  # "Surat" from "Surat, Gujarat"
        before = len(raw_candidates)
        raw_candidates = [
            r for r in raw_candidates
            if not r.location or job_city in r.location.lower()
        ]
        dropped = before - len(raw_candidates)
        if dropped:
            logger.info(
                "Location filter: dropped %d candidates not in '%s' for job %d",
                dropped, job_city, job.id,
            )

    logger.info("Sourced %d raw candidates for job %d", len(raw_candidates), job.id)

    entries: list[ShortlistEntry] = []
    for raw in raw_candidates:
        candidate = await _upsert_candidate(raw, db)
        entry = await _score_and_shortlist(candidate, job, db)
        if entry:
            entries.append(entry)

    await db.flush()

    # On-add automation: contact the good, reachable matches we just sourced.
    if auto_outreach:
        from app.services.auto_outreach import auto_outreach_after_sourcing
        await auto_outreach_after_sourcing(db, job, entries)
        await db.flush()

    return entries


async def _source_from_apna(job: Job, keywords: list[str], db: AsyncSession) -> list[RawCandidate]:
    """Run an Apna live search if a token is saved on the server.

    Defensive by design: any failure (no token, expired token, network error) is
    logged and swallowed so it can never crash a sourcing run. Returns [] on any
    problem.
    """
    from app.services.app_settings import get_setting

    try:
        token = await get_setting(db, "apna_token")
    except Exception as exc:  # e.g. table missing on a very old DB
        logger.warning("Could not read saved Apna token: %s", exc)
        return []

    if not token:
        return []  # No token saved — skip Apna cleanly, no Apna results this run.

    org_id = None
    try:
        org_id = await get_setting(db, "apna_org_id")
    except Exception:
        pass

    from app.adapters.apna import ApnaAdapter

    adapter = ApnaAdapter(token=token, org_id=org_id) if org_id else ApnaAdapter(token=token)
    try:
        results = await adapter.search(
            keywords=keywords,
            location=job.location,
            experience_min=job.experience_min,
            experience_max=job.experience_max,
            limit=20,
        )
        logger.info("Apna live search returned %d candidates for job %d", len(results), job.id)
        return results
    except Exception as exc:
        # 401/403 means the saved token has expired — tell the owner in plain words.
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in (401, 403):
            logger.warning(
                "Apna token expired — re-save it on the Import page. (job %d)", job.id
            )
        else:
            logger.warning("Apna live search failed for job %d: %s", job.id, exc)
        return []


async def _upsert_candidate(raw: RawCandidate, db: AsyncSession) -> Candidate:
    """Insert candidate if not present (match on email or source_ref)."""
    existing = None
    if raw.email:
        result = await db.execute(select(Candidate).where(Candidate.email == raw.email))
        existing = result.scalar_one_or_none()
    if existing is None and raw.source_ref:
        result = await db.execute(
            select(Candidate).where(Candidate.source_ref == raw.source_ref)
        )
        existing = result.scalar_one_or_none()

    if existing:
        return existing

    candidate = Candidate(
        name=raw.name,
        email=raw.email,
        phone=raw.phone,
        whatsapp=raw.whatsapp or raw.phone,
        skills=raw.skills,
        experience_years=raw.experience_years,
        current_salary=raw.current_salary,
        expected_salary=raw.expected_salary,
        location=raw.location,
        notice_period_days=raw.notice_period_days,
        education=raw.education,
        current_employer=raw.current_employer,
        current_role=raw.current_role,
        raw_profile=raw.raw_profile,
        resume_url=raw.resume_url,
        source=raw.source,
        source_ref=raw.source_ref,
    )
    db.add(candidate)
    await db.flush()  # assign id
    return candidate


async def _score_and_shortlist(
    candidate: Candidate, job: Job, db: AsyncSession
) -> ShortlistEntry | None:
    """Score a candidate against a job and create OR update a ShortlistEntry.

    There is exactly one entry per (candidate, job) — enforced by a unique
    constraint. Re-scoring an existing pair UPDATES that row (score, insights,
    and — for not-yet-advanced entries — status); it never inserts a second row.
    """
    # Find the existing entry for this (candidate, job), if any.
    result = await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.job_id == job.id,
            ShortlistEntry.candidate_id == candidate.id,
        )
    )
    existing = result.scalar_one_or_none()

    scored = score_candidate(
        candidate_skills=candidate.skills or [],
        candidate_experience=candidate.experience_years,
        candidate_expected_salary=candidate.expected_salary,
        candidate_location=candidate.location,
        candidate_role=candidate.current_role,
        job_title=job.title,
        job_skills=job.skills or [],
        job_experience_min=job.experience_min,
        job_experience_max=job.experience_max,
        job_salary_min=job.salary_min,
        job_salary_max=job.salary_max,
        job_location=job.location,
    )

    # Enhance with AI scoring if configured
    from app.services.ai_scoring import ai_score_candidate
    ai_result = await ai_score_candidate(candidate, job)
    ai_insights = {}
    if ai_result and "score" in ai_result:
        # Blend: 60% AI score (0-10 → 0-100) + 40% rule-based
        ai_score_100 = ai_result["score"] * 10
        blended = round(scored.total * 0.4 + ai_score_100 * 0.6, 2)
        scored.total = blended
        scored.decision = "AUTO_SHORTLIST" if blended >= 65 else ("MANUAL_REVIEW" if blended >= 40 else "REJECT")
        logger.info("AI-blended score for %s: %.1f (%s)", candidate.name, blended, scored.decision)
        ai_insights = {
            "ai_strengths": ai_result.get("strengths", []),
            "ai_concerns": ai_result.get("concerns", []),
            "ai_reasoning": ai_result.get("reasoning", ""),
            "ai_opener": ai_result.get("personalized_opener", ""),
        }

    status_map = {
        "AUTO_SHORTLIST": ShortlistStatus.SHORTLISTED,
        "MANUAL_REVIEW": ShortlistStatus.PENDING,
        "REJECT": ShortlistStatus.REJECTED,
    }

    breakdown = {**(scored.breakdown or {}), **ai_insights}
    new_status = status_map[scored.decision]

    if existing is not None:
        # Re-score: always refresh the score + insights, but only re-classify the
        # status when the candidate hasn't advanced past review. A CONTACTED /
        # INTERESTED / INTERVIEW_SCHEDULED / HIRED entry keeps its status (we never
        # silently move it back to PENDING/REJECTED); PENDING/SHORTLISTED ones are
        # freely re-classified.
        from app.models.shortlist import ADVANCED_STATUSES
        existing.score = scored.total
        existing.score_breakdown = breakdown
        if existing.status not in ADVANCED_STATUSES:
            existing.status = new_status
        return existing

    entry = ShortlistEntry(
        job_id=job.id,
        candidate_id=candidate.id,
        score=scored.total,
        score_breakdown=breakdown,
        status=new_status,
    )
    db.add(entry)
    return entry
