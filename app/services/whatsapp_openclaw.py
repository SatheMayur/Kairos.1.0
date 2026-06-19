"""OpenClaw / WAHA WhatsApp sender.

Sends messages via the WAHA REST API that OpenClaw exposes.

WAHA setup (one-time):
  1. Start WAHA: docker run -p 3000:3000 devlikeapro/waha
  2. Scan QR at http://localhost:3000/dashboard
  3. In WAHA dashboard → Webhooks → add:
       URL : https://kgirdharlal-recruitment.vercel.app/api/v1/webhook/whatsapp
       Events: message
  4. Set env vars:
       OPENCLAW_API_URL = http://<your-host>:3000
       OPENCLAW_API_KEY = <from WAHA dashboard>
       OPENCLAW_SESSION = default
       OPENCLAW_WEBHOOK_SECRET = <any secret string, same in both places>

Phone format: Indian numbers stored as "9876543210" or "+919876543210"
are normalised to "919876543210@c.us" for WAHA.
"""
import re
import httpx
from app.config import get_settings
from app.utils.logging import get_logger
from app.utils.phone import jid_local, normalize_indian_mobile, to_chat_id

logger = get_logger(__name__)
settings = get_settings()

_POSITIVE = {"yes", "yeah", "yep", "yup", "haan", "ha", "ok", "okay",
             "sure", "interested", "apply", "want", "i want", "send", "great",
             "good", "fine", "confirm", "confirmed", "proceed"}

_NEGATIVE = {"no", "nahi", "nope", "not interested", "not now", "later",
             "busy", "already placed", "not looking", "stop", "unsubscribe"}


def _fmt_phone(phone: str) -> str | None:
    """Normalise any Indian mobile to WAHA chatId format: 91XXXXXXXXXX@c.us.
    Returns None when the number isn't a usable Indian mobile."""
    return to_chat_id(phone)


def _extract_phone(chat_id: str) -> str:
    """Convert any WhatsApp JID back to its local part (drops @c.us/@s.whatsapp.net/@lid)."""
    return jid_local(chat_id)


def is_positive(text: str) -> bool:
    t = text.strip().lower()
    return any(kw in t for kw in _POSITIVE)


def is_negative(text: str) -> bool:
    t = text.strip().lower()
    return any(kw in t for kw in _NEGATIVE)


async def send_whatsapp(phone: str, message: str, db=None) -> str | None:
    """Send a WhatsApp message.

    Priority:
    1. Queue to DB wa_queue table (polled by bridge.js every 3 s) — works with no public URL
    2. Direct WAHA/OpenClaw REST call (legacy, if OPENCLAW_API_URL is set)
    Returns a pseudo-ID on queue success, real ID on direct send, or None on failure.

    OUTBOUND outreach passes a phone string from a CSV/portal — it is normalized
    to a clean Indian mobile FIRST, and junk / invalid numbers (landlines, "NA",
    <10 digits, Excel ".0" artifacts) are never queued (we return None so the
    caller treats the candidate as unreachable instead of sending garbage).

    REPLIES to inbound messages may pass a full WhatsApp JID (e.g. a privacy
    '<id>@lid' or '...@c.us'); those are passed through to the bridge AS-IS so
    the reply actually reaches the person who messaged us.
    """
    is_jid = "@" in (phone or "")
    if is_jid:
        # A full JID from an inbound reply — send to it verbatim.
        queue_phone = phone
    else:
        mobile = normalize_indian_mobile(phone)
        if not mobile:
            logger.warning("WhatsApp skipped — %r is not a valid Indian mobile", phone)
            return None
        # Clean 91-prefixed digits — the Baileys bridge turns these into the
        # correct '91XXXXXXXXXX@s.whatsapp.net' JID itself. We deliberately do
        # NOT store an '@c.us' chat id here: the bridge sends to that domain
        # as-is, which is wrong for Baileys.
        queue_phone = f"91{mobile}"

    # Path 1: DB queue (bridge.js polls /api/v1/wa/poll).
    if db is not None:
        try:
            from app.models.wa_queue import WAQueue
            row = WAQueue(phone=queue_phone, message=message)
            db.add(row)
            await db.flush()
            logger.info("WhatsApp queued to DB (id=%d) for %s", row.id, queue_phone)
            return f"queued:{row.id}"
        except Exception as exc:
            logger.warning("WA DB queue failed: %s — falling back to direct send", exc)

    # Path 2: Direct WAHA call (if URL configured)
    if not settings.openclaw_api_url:
        logger.warning("WhatsApp not configured — no DB session and OPENCLAW_API_URL not set")
        return None

    # For a bare phone use the normalized chatId; for a JID, send it verbatim.
    chat_id = queue_phone if is_jid else _fmt_phone(phone)
    if not chat_id:
        logger.warning("WhatsApp skipped — %r is not a valid Indian mobile", phone)
        return None
    url = f"{settings.openclaw_api_url.rstrip('/')}/api/sendText"
    headers = {}
    if settings.openclaw_api_key:
        headers["X-Api-Key"] = settings.openclaw_api_key

    payload = {"chatId": chat_id, "text": message, "session": settings.openclaw_session}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            msg_id = data.get("id") or data.get("key", {}).get("id", "sent")
            logger.info("WhatsApp sent via OpenClaw to %s id=%s", chat_id, msg_id)
            return msg_id
    except Exception as exc:
        logger.error("OpenClaw direct send failed to %s: %s", chat_id, exc)
        return None
