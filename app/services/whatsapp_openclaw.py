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

logger = get_logger(__name__)
settings = get_settings()

_POSITIVE = {"yes", "yeah", "yep", "yup", "haan", "ha", "ok", "okay",
             "sure", "interested", "apply", "want", "i want", "send", "great",
             "good", "fine", "confirm", "confirmed", "proceed"}

_NEGATIVE = {"no", "nahi", "nope", "not interested", "not now", "later",
             "busy", "already placed", "not looking", "stop", "unsubscribe"}


def _fmt_phone(phone: str) -> str:
    """Normalise any Indian mobile to WAHA chatId format: 91XXXXXXXXXX@c.us"""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("91") and len(digits) == 12:
        return f"{digits}@c.us"
    if len(digits) == 10:
        return f"91{digits}@c.us"
    # Already has country code but not 91 — send as-is
    return f"{digits}@c.us"


def _extract_phone(chat_id: str) -> str:
    """Convert WAHA chatId back to plain digits."""
    return chat_id.replace("@c.us", "").replace("@lid", "")


def is_positive(text: str) -> bool:
    t = text.strip().lower()
    return any(kw in t for kw in _POSITIVE)


def is_negative(text: str) -> bool:
    t = text.strip().lower()
    return any(kw in t for kw in _NEGATIVE)


async def send_whatsapp(phone: str, message: str) -> str | None:
    """Send a WhatsApp message via WAHA. Returns message ID or None on failure."""
    if not settings.openclaw_api_url:
        logger.warning("OpenClaw not configured — OPENCLAW_API_URL not set")
        return None

    chat_id = _fmt_phone(phone)
    url = f"{settings.openclaw_api_url.rstrip('/')}/api/sendText"
    headers = {}
    if settings.openclaw_api_key:
        headers["X-Api-Key"] = settings.openclaw_api_key

    payload = {
        "chatId": chat_id,
        "text": message,
        "session": settings.openclaw_session,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            msg_id = data.get("id") or data.get("key", {}).get("id", "sent")
            logger.info("WhatsApp sent via OpenClaw to %s id=%s", chat_id, msg_id)
            return msg_id
    except Exception as exc:
        logger.error("OpenClaw send failed to %s: %s", chat_id, exc)
        return None
