"""Sourcing API — trigger Apify-powered candidate search for a job."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.job import Job
from app.services.sourcing import source_candidates_for_job
from app.config import get_settings
from app.utils.logging import get_logger

router = APIRouter(prefix="/sourcing", tags=["sourcing"])
logger = get_logger(__name__)
settings = get_settings()


@router.post("/{job_id}")
async def run_sourcing(job_id: int, db: AsyncSession = Depends(get_db)):
    """Trigger Apify sourcing for a job — searches LinkedIn + Naukri, scores and stores candidates.

    Requires APIFY_API_TOKEN + USE_MOCK_ADAPTERS=false in env vars.
    Returns a summary of candidates found and shortlist decisions.
    """
    if not settings.apify_api_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "APIFY_API_TOKEN not configured. "
                "Get your token from apify.com/account/integrations, then:\n"
                "  vercel env add APIFY_API_TOKEN production\n"
                "Also ensure USE_MOCK_ADAPTERS=false is set."
            ),
        )

    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    logger.info("Starting Apify sourcing for job %d (%s)", job_id, job.title)

    # Reset registry so it picks up the Apify token (in case it was cached as mock)
    from app.adapters.registry import reset_registry
    reset_registry()

    entries = await source_candidates_for_job(job, db)
    await db.commit()

    shortlisted = [e for e in entries if e.status.value == "SHORTLISTED"]
    review = [e for e in entries if e.status.value == "PENDING"]
    rejected = [e for e in entries if e.status.value == "REJECTED"]

    return {
        "job_id": job_id,
        "job_title": job.title,
        "total_sourced": len(entries),
        "shortlisted": len(shortlisted),
        "manual_review": len(review),
        "rejected": len(rejected),
        "message": (
            f"Sourced {len(entries)} candidates via LinkedIn + Naukri. "
            f"{len(shortlisted)} auto-shortlisted, {len(review)} need manual review."
        ),
    }
