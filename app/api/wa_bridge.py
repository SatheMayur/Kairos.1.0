"""WhatsApp bridge API — used by the Baileys bridge process (polling architecture).

The bridge (bridge.js) calls these endpoints every few seconds:
  GET  /wa/poll          — fetch PENDING outgoing messages
  POST /wa/ack           — mark messages as SENT or FAILED
  POST /wa/inbound       — deliver inbound WhatsApp message to the pipeline

This eliminates the need for any public URL on the bridge machine.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.config import get_settings
from app.models.wa_queue import WAQueue, WAQueueStatus
from app.utils.logging import get_logger

router = APIRouter(prefix="/wa", tags=["whatsapp-bridge"])
logger = get_logger(__name__)
settings = get_settings()

BRIDGE_SECRET = "kgirdharlal-bridge-secret"


def _auth(x_bridge_key: str = Header(default="")):
    if x_bridge_key != BRIDGE_SECRET:
        raise HTTPException(status_code=401, detail="Invalid bridge key")


@router.get("/poll")
async def poll_queue(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_auth),
):
    """Return up to 20 PENDING outgoing messages. Bridge calls this every 3 s."""
    res = await db.execute(
        select(WAQueue)
        .where(WAQueue.status == WAQueueStatus.PENDING)
        .order_by(WAQueue.created_at)
        .limit(20)
    )
    rows = res.scalars().all()
    return [{"id": r.id, "phone": r.phone, "message": r.message} for r in rows]


@router.post("/ack")
async def ack_messages(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_auth),
):
    """Bridge reports send results. payload: {results: [{id, status, error?}]}"""
    results = payload.get("results", [])
    for item in results:
        row = await db.get(WAQueue, item["id"])
        if row:
            row.status = WAQueueStatus.SENT if item["status"] == "sent" else WAQueueStatus.FAILED
            row.sent_at = datetime.utcnow()
            row.error = item.get("error")
    return {"acked": len(results)}


@router.post("/inbound")
async def inbound_message(
    payload: dict,
    _: None = Depends(_auth),
):
    """Bridge POSTs inbound WhatsApp messages here. Re-routes to webhook handler."""
    from app.api.webhook import _handle_inbound
    import asyncio
    from_jid = payload.get("from", "")
    body_text = payload.get("body", "").strip()
    session = payload.get("session", "default")
    if not from_jid or not body_text:
        return {"status": "ignored"}
    # Run async in background — return 200 immediately
    asyncio.create_task(_handle_inbound(from_jid, body_text, session))
    return {"status": "queued"}


@router.get("/status")
async def bridge_status(_: None = Depends(_auth)):
    """Returns 200 if the bridge API is reachable (used by bridge health check)."""
    return {"status": "ok"}
