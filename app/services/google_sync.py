"""Optional Google (Gmail + Calendar) sync via a service account.

INERT unless a service-account JSON + the mailbox to read are stored in
app_settings (keys: google_sa_json, google_sa_subject). When present, the
20-minute sync pulls recent *applicant* emails + upcoming calendar events into
the memory tree, so the Morning Briefing shows live Gmail/Calendar with no agent
in the loop.

Reading a user's Gmail/Calendar with a service account needs Google Workspace
domain-wide delegation for the read-only scopes (a one-time Workspace-admin step).
Without it, the token refresh fails and we skip silently — the briefing falls
back to the manual snapshot. This module NEVER raises into the caller.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.app_settings import get_setting
from app.services import agent_memory
from app.services.agent_memory import _is_applicant_email
from app.utils.logging import get_logger

logger = get_logger(__name__)

_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_CAL_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
_CAL_API = "https://www.googleapis.com/calendar/v3"

_APPLICANT_QUERY = (
    "in:inbox newer_than:14d (from:naukri.com OR from:workindia.in OR from:apna.co "
    "OR from:indeed.com OR subject:(application OR applicant OR candidate OR resume "
    'OR "interview confirmation"))'
)


async def is_configured(db: AsyncSession) -> bool:
    try:
        return bool(await get_setting(db, "google_sa_json") and await get_setting(db, "google_sa_subject"))
    except Exception:
        return False


def _token(sa_json: str, subject: str, scopes: list[str]) -> str | None:
    """Service-account bearer token, impersonating `subject` (needs DWD)."""
    try:
        import google.auth.transport.requests  # type: ignore
        from google.oauth2 import service_account  # type: ignore
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=scopes, subject=subject
        )
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception as exc:
        logger.warning("google_sync: token error (DWD configured?): %s", exc)
        return None


async def sync_google(db: AsyncSession) -> dict:
    """Pull applicant emails + upcoming events into memory. Never raises."""
    try:
        sa_json = await get_setting(db, "google_sa_json")
        subject = await get_setting(db, "google_sa_subject")
    except Exception:
        return {"configured": False}
    if not sa_json or not subject:
        return {"configured": False}

    import httpx
    token = _token(sa_json, subject, [_GMAIL_SCOPE, _CAL_SCOPE])
    if not token:
        return {"configured": True, "error": "auth_failed"}
    headers = {"Authorization": f"Bearer {token}"}
    out = {"configured": True}

    # ── Gmail: applicant emails only ─────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            lr = await client.get(f"{_GMAIL_API}/users/me/messages",
                                  params={"q": _APPLICANT_QUERY, "maxResults": 20}, headers=headers)
            lr.raise_for_status()
            items = []
            for m in lr.json().get("messages", [])[:20]:
                mr = await client.get(
                    f"{_GMAIL_API}/users/me/messages/{m['id']}",
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    headers=headers,
                )
                if mr.status_code != 200:
                    continue
                msg = mr.json()
                hdr = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                item = {"from": hdr.get("From", ""), "subject": hdr.get("Subject", ""),
                        "date": hdr.get("Date", ""), "unread": "UNREAD" in msg.get("labelIds", [])}
                if _is_applicant_email(item):
                    items.append(item)
            await agent_memory.set_memory(db, "external", "gmail",
                                          {"fetched_at": datetime.utcnow().isoformat(), "items": items})
            out["gmail"] = len(items)
    except Exception as exc:
        logger.warning("google_sync gmail: %s", exc)

    # ── Calendar: upcoming events ────────────────────────────────────────────
    try:
        now = datetime.utcnow().isoformat() + "Z"
        async with httpx.AsyncClient(timeout=20) as client:
            cr = await client.get(f"{_CAL_API}/calendars/primary/events",
                                  params={"timeMin": now, "maxResults": 15,
                                          "singleEvents": "true", "orderBy": "startTime"},
                                  headers=headers)
            cr.raise_for_status()
            evs = []
            for e in cr.json().get("items", []):
                start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date")
                evs.append({"title": e.get("summary", ""), "start": start, "location": e.get("location", "")})
            await agent_memory.set_memory(db, "external", "calendar",
                                          {"fetched_at": datetime.utcnow().isoformat(), "items": evs})
            out["calendar"] = len(evs)
    except Exception as exc:
        logger.warning("google_sync calendar: %s", exc)

    try:
        await db.commit()
    except Exception:
        pass
    logger.info("google_sync done: %s", out)
    return out
