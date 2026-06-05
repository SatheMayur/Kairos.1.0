"""Orchestrator API — the recruitment-manager agent's daily plan."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services.orchestrator import generate_plan, get_latest_plan

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


@router.get("/today")
async def orchestrator_today(db: AsyncSession = Depends(get_db)):
    """Return the most recent plan (for the dashboard). Read-only."""
    plan = await get_latest_plan(db)
    return plan or {"manager_note": None, "priorities": [], "situation": {}}


@router.post("/plan")
async def orchestrator_plan(db: AsyncSession = Depends(get_db)):
    """Reason over the current state and produce a fresh plan now. No actions taken."""
    return await generate_plan(db, persist=True)
