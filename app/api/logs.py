"""Error log API — query, resolve, and clear persisted error entries."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.error_log import ErrorLog

router = APIRouter(prefix="/logs", tags=["logs"])


def _serialize(e: ErrorLog) -> dict:
    return {
        "id": e.id,
        "logged_at": e.logged_at.isoformat() + "Z",
        "level": e.level,
        "source": e.source,
        "error_type": e.error_type,
        "message": e.message,
        "traceback": e.traceback,
        "method": e.method,
        "path": e.path,
        "status_code": e.status_code,
        "request_body": e.request_body,
        "resolved": e.resolved,
        "resolved_note": e.resolved_note,
    }


@router.get("/errors")
async def list_errors(
    limit: int = Query(100, le=500),
    offset: int = 0,
    level: Optional[str] = None,
    resolved: Optional[bool] = None,
    source: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(ErrorLog).order_by(ErrorLog.logged_at.desc()).offset(offset).limit(limit)
    if level:
        q = q.where(ErrorLog.level == level.upper())
    if resolved is not None:
        q = q.where(ErrorLog.resolved == resolved)
    if source:
        q = q.where(ErrorLog.source.ilike(f"%{source}%"))
    result = await db.execute(q)
    return [_serialize(e) for e in result.scalars().all()]


@router.get("/errors/stats")
async def error_stats(db: AsyncSession = Depends(get_db)):
    total = await db.scalar(select(func.count()).select_from(ErrorLog)) or 0
    unresolved = await db.scalar(
        select(func.count()).select_from(ErrorLog).where(ErrorLog.resolved == False)
    ) or 0
    last_24h = await db.scalar(
        select(func.count()).select_from(ErrorLog).where(
            ErrorLog.logged_at >= datetime.utcnow() - timedelta(hours=24)
        )
    ) or 0
    critical = await db.scalar(
        select(func.count()).select_from(ErrorLog).where(
            ErrorLog.level == "CRITICAL", ErrorLog.resolved == False
        )
    ) or 0
    return {
        "total": total,
        "unresolved": unresolved,
        "last_24h": last_24h,
        "critical_unresolved": critical,
    }


@router.patch("/errors/{error_id}/resolve")
async def resolve_error(
    error_id: int,
    note: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    entry = await db.get(ErrorLog, error_id)
    if entry:
        entry.resolved = True
        if note:
            entry.resolved_note = note[:256]
        await db.commit()
    return {"ok": True}


@router.patch("/errors/resolve-all")
async def resolve_all(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ErrorLog).where(ErrorLog.resolved == False)
    )
    entries = result.scalars().all()
    for e in entries:
        e.resolved = True
    await db.commit()
    return {"resolved": len(entries)}


@router.delete("/errors")
async def clear_errors(
    older_than_days: int = Query(7, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Delete error entries older than N days. older_than_days=0 clears all."""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    result = await db.execute(
        delete(ErrorLog).where(ErrorLog.logged_at < cutoff)
    )
    await db.commit()
    return {"deleted": result.rowcount}
