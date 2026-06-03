"""WhatsApp bridge API — used by the Baileys bridge process (polling architecture).

The bridge (bridge.js) calls these endpoints every few seconds:
  GET  /wa/poll          — fetch PENDING outgoing messages
  POST /wa/ack           — mark messages as SENT or FAILED
  POST /wa/inbound       — deliver inbound WhatsApp message to the pipeline

This eliminates the need for any public URL on the bridge machine.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.config import get_settings
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.outreach import OutreachLog, OutreachChannel, OutreachStatus
from app.models.shortlist import ShortlistEntry, ShortlistStatus
from app.models.wa_queue import WAQueue, WAQueueStatus
from app.utils.logging import get_logger

router = APIRouter(prefix="/wa", tags=["whatsapp-bridge"])
logger = get_logger(__name__)
settings = get_settings()

BRIDGE_SECRET = settings.bridge_api_secret


def _auth(x_bridge_key: str = Header(default="")):
    if x_bridge_key != BRIDGE_SECRET:
        raise HTTPException(status_code=401, detail="Invalid bridge key")


@router.get("/poll")
async def poll_queue(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_auth),
):
    """Return up to 20 PENDING outgoing messages. Bridge calls this every 3 s."""
    # Track last poll time for watchdog dead-bridge detection
    from app.models.wa_connection import WaConnection
    conn_row = await db.get(WaConnection, 1)
    if conn_row:
        conn_row.last_poll_at = datetime.utcnow()

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


# ── Dashboard endpoints (no auth — used by /ui/whatsapp) ──────────────────

@router.get("/dashboard/stats")
async def wa_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """KPI counts for the WhatsApp dashboard."""
    # Queue stats
    q_res = await db.execute(select(WAQueue))
    queue_rows = q_res.scalars().all()
    q_pending = sum(1 for r in queue_rows if r.status == WAQueueStatus.PENDING)
    q_sent    = sum(1 for r in queue_rows if r.status == WAQueueStatus.SENT)
    q_failed  = sum(1 for r in queue_rows if r.status == WAQueueStatus.FAILED)

    # Outreach log WA stats
    ol_res = await db.execute(
        select(OutreachLog).where(OutreachLog.channel == OutreachChannel.WHATSAPP)
    )
    wa_logs = ol_res.scalars().all()
    replied   = sum(1 for l in wa_logs if l.status == OutreachStatus.REPLIED)
    interested = sum(1 for l in wa_logs if l.reply_text and any(
        kw in (l.reply_text or "").lower() for kw in ["yes","haan","interested","ok","sure"]))

    return {
        "queue_pending": q_pending,
        "queue_sent": q_sent,
        "queue_failed": q_failed,
        "outreach_sent": len(wa_logs),
        "replied": replied,
        "interested": interested,
    }


@router.get("/dashboard/conversations")
async def wa_conversations(db: AsyncSession = Depends(get_db)):
    """Return all WhatsApp outreach logs enriched with candidate + job info."""
    ol_res = await db.execute(
        select(OutreachLog)
        .where(OutreachLog.channel == OutreachChannel.WHATSAPP)
        .order_by(OutreachLog.created_at.desc())
        .limit(200)
    )
    logs = ol_res.scalars().all()

    # Build candidate + job maps
    cand_ids = list({l.candidate_id for l in logs})
    job_ids  = list({l.job_id for l in logs})

    cands = {}
    if cand_ids:
        cr = await db.execute(select(Candidate).where(Candidate.id.in_(cand_ids)))
        cands = {c.id: c for c in cr.scalars().all()}

    jobs = {}
    if job_ids:
        jr = await db.execute(select(Job).where(Job.id.in_(job_ids)))
        jobs = {j.id: j for j in jr.scalars().all()}

    # Shortlist statuses
    sl_res = await db.execute(
        select(ShortlistEntry).where(ShortlistEntry.candidate_id.in_(cand_ids))
    )
    sl_map = {}
    for e in sl_res.scalars().all():
        sl_map[(e.candidate_id, e.job_id)] = e.status.value

    result = []
    for log in logs:
        c = cands.get(log.candidate_id)
        j = jobs.get(log.job_id)
        result.append({
            "id": log.id,
            "candidate_id": log.candidate_id,
            "candidate_name": c.name if c else f"#{log.candidate_id}",
            "candidate_phone": c.whatsapp or c.phone if c else "",
            "job_id": log.job_id,
            "job_title": j.title if j else f"Job #{log.job_id}",
            "type": log.outreach_type.value,
            "status": log.status.value,
            "message": log.message,
            "reply": log.reply_text,
            "pipeline_status": sl_map.get((log.candidate_id, log.job_id), ""),
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            "replied_at": log.replied_at.isoformat() if log.replied_at else None,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })
    return result


@router.get("/dashboard/queue")
async def wa_queue_list(db: AsyncSession = Depends(get_db)):
    """Return recent wa_queue entries for the dashboard."""
    res = await db.execute(
        select(WAQueue).order_by(WAQueue.created_at.desc()).limit(100)
    )
    rows = res.scalars().all()
    return [{
        "id": r.id,
        "phone": r.phone,
        "message": r.message,
        "status": r.status.value,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        "error": r.error,
    } for r in rows]


# ── QR / connection endpoints ─────────────────────────────────────────────

@router.post("/qr")
async def bridge_post_qr(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_auth),
):
    """Bridge POSTs QR string or connected status here.
    payload: {qr: "<qr_string>"} or {status: "CONNECTED"}
    """
    from datetime import datetime
    from app.models.wa_connection import WaConnection
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    status = payload.get("status")
    qr_data = payload.get("qr")

    # Upsert single row (id=1)
    row = await db.get(WaConnection, 1)
    if not row:
        row = WaConnection(id=1, status="DISCONNECTED")
        db.add(row)

    if status == "CONNECTED":
        row.status = "CONNECTED"
        row.qr_data = None
    elif qr_data:
        row.status = "QR_READY"
        row.qr_data = qr_data

    row.updated_at = datetime.utcnow()
    await db.commit()
    return {"ok": True}


@router.get("/qr-image")
async def wa_qr_image(db: AsyncSession = Depends(get_db)):
    """Return the current QR code as a PNG. No auth — displayed by dashboard."""
    from app.models.wa_connection import WaConnection
    import io, qrcode as _qrcode
    from fastapi.responses import StreamingResponse

    row = await db.get(WaConnection, 1)
    if not row or not row.qr_data or row.status != "QR_READY":
        raise HTTPException(status_code=404, detail="No QR available")

    img = _qrcode.make(row.qr_data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png",
                             headers={"Cache-Control": "no-store"})


@router.get("/connection")
async def get_connection_status(db: AsyncSession = Depends(get_db)):
    """Dashboard polls this to get QR data or connection status. No auth required."""
    from app.models.wa_connection import WaConnection
    row = await db.get(WaConnection, 1)
    if not row:
        return {"status": "DISCONNECTED", "qr_data": None}
    return {
        "status": row.status,
        "qr_data": row.qr_data,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.post("/disconnect")
async def bridge_disconnect(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_auth),
):
    """Bridge notifies disconnect."""
    from datetime import datetime
    from app.models.wa_connection import WaConnection
    row = await db.get(WaConnection, 1)
    if row:
        row.status = "DISCONNECTED"
        row.qr_data = None
        row.updated_at = datetime.utcnow()
        await db.commit()
    return {"ok": True}


@router.post("/dashboard/send")
async def wa_manual_send(payload: dict, db: AsyncSession = Depends(get_db)):
    """Queue a manual WhatsApp message from the dashboard."""
    phone = (payload.get("phone") or "").strip()
    message = (payload.get("message") or "").strip()
    if not phone or not message:
        raise HTTPException(status_code=422, detail="phone and message required")
    from app.services.whatsapp_openclaw import send_whatsapp
    result = await send_whatsapp(phone, message, db=db)
    if result:
        return {"queued": True, "id": result}
    raise HTTPException(status_code=502, detail="Failed to queue message")
