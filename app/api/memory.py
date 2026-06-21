"""Agent memory + morning briefing API.

  GET  /memory/tree           — the whole memory tree (for the UI)
  GET  /memory/morning-brief  — "what happened while you slept" summary
  POST /memory/sync           — run the snapshot/delta sync (no secret: it sends
                                nothing, only reads business tables + writes memory).
                                Called every ~20 min by the WhatsApp bridge.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services import agent_memory

router = APIRouter(prefix="/memory", tags=["memory"])


class ExternalSnapshot(BaseModel):
    gmail: list[dict] | None = None       # [{from, subject, date, unread}]
    calendar: list[dict] | None = None    # [{title, start, end, location}]


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


@router.post("/external-snapshot")
async def external_snapshot(payload: ExternalSnapshot, db: AsyncSession = Depends(get_db)):
    """Store a live Gmail / Google-Calendar snapshot into the memory tree so the
    Morning Briefing can show real inbox + calendar data. Pushed by an agent that
    holds Google access (the deployed app itself has no Google credentials)."""
    from datetime import datetime
    saved = {}
    if payload.gmail is not None:
        await agent_memory.set_memory(db, "external", "gmail",
                                      {"fetched_at": datetime.utcnow().isoformat(), "items": payload.gmail})
        saved["gmail"] = len(payload.gmail)
    if payload.calendar is not None:
        await agent_memory.set_memory(db, "external", "calendar",
                                      {"fetched_at": datetime.utcnow().isoformat(), "items": payload.calendar})
        saved["calendar"] = len(payload.calendar)
    await db.commit()
    return {"saved": saved}
