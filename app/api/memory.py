"""Agent memory + morning briefing API.

  GET  /memory/tree           — the whole memory tree (for the UI)
  GET  /memory/morning-brief  — "what happened while you slept" summary
  POST /memory/sync           — run the snapshot/delta sync (no secret: it sends
                                nothing, only reads business tables + writes memory).
                                Called every ~20 min by the WhatsApp bridge.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services import agent_memory

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/tree")
async def memory_tree(db: AsyncSession = Depends(get_db)):
    return await agent_memory.build_tree(db)


@router.get("/morning-brief")
async def morning_brief(hours: int = 16, db: AsyncSession = Depends(get_db)):
    return await agent_memory.build_morning_brief(db, hours=hours)


@router.post("/sync")
async def memory_sync(db: AsyncSession = Depends(get_db)):
    """Snapshot recent WhatsApp/outreach/pipeline/interview activity into the
    memory tree. Safe + side-effect-free (sends no messages)."""
    return await agent_memory.run_sync(db)
