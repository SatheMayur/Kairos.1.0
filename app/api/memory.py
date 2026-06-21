"""Agent memory + morning briefing API.

  GET  /memory/tree           — the whole memory tree (for the UI)
  GET  /memory/morning-brief  — "what happened while you slept" summary
  POST /memory/sync           — run the snapshot/delta sync (no secret: it sends
                                nothing, only reads business tables + writes memory).
                                Called every ~20 min by the WhatsApp bridge.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services import agent_memory

router = APIRouter(prefix="/memory", tags=["memory"])


class ExternalSnapshot(BaseModel):
    gmail: list[dict] | None = None       # [{from, subject, date, unread}]
    calendar: list[dict] | None = None    # [{title, start, end, location}]


class GoogleCreds(BaseModel):
    service_account_json: str             # the downloaded SA key (JSON)
    impersonate_email: str                # the mailbox to read, e.g. kirti@kgirdharlal.com


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


@router.get("/google-credentials")
async def google_credentials_status(db: AsyncSession = Depends(get_db)):
    """Whether unattended Google sync is configured (never returns the secret)."""
    from app.services.app_settings import get_setting
    return {"configured": bool(await get_setting(db, "google_sa_json")),
            "impersonate_email": await get_setting(db, "google_sa_subject")}


@router.post("/google-credentials")
async def set_google_credentials(payload: GoogleCreds, db: AsyncSession = Depends(get_db)):
    """Store a Google service-account key + the mailbox to read, so the 20-min sync
    pulls Gmail/Calendar automatically (no agent needed). Secret is never returned."""
    import json
    from app.services.app_settings import set_setting
    try:
        info = json.loads(payload.service_account_json)
        if "client_email" not in info or "private_key" not in info:
            raise ValueError("missing fields")
    except Exception:
        raise HTTPException(status_code=400,
                            detail="That doesn't look like a valid Google service-account JSON key.")
    await set_setting(db, "google_sa_json", payload.service_account_json)
    await set_setting(db, "google_sa_subject", payload.impersonate_email.strip())
    await db.commit()
    # Try an immediate sync so the briefing fills in right away.
    from app.services.google_sync import sync_google
    result = await sync_google(db)
    return {"saved": True, "first_sync": result}


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
