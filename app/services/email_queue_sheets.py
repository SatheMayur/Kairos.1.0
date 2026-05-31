"""Write outreach emails directly into the Google Sheets Email Queue.

The Apps Script (AI_HR_AutoSend) polls the sheet every 5 minutes, sends
PENDING rows via Gmail, and marks them SENT.  This is the production-grade
delivery path — no SMTP credentials or open ports required.

Sheet columns (1-indexed):
  A=To  B=Subject  C=Body  D=Status  E=Created_At
  F=Sent_At  G=Candidate_Name  H=Role  I=Priority
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_APPEND_URL = (
    "https://sheets.googleapis.com/v4/spreadsheets"
    "/{sheet_id}/values/Sheet1!A:I:append?valueInputOption=USER_ENTERED"
)


async def queue_email(
    *,
    to: str,
    subject: str,
    body: str,
    candidate_name: str = "",
    role: str = "",
    priority: str = "NORMAL",
) -> bool:
    """Append a PENDING row to the Email Queue sheet.

    Returns True if queued, False on any error (caller falls back to SMTP).
    """
    from app.config import get_settings
    settings = get_settings()

    token = await _get_access_token(settings)
    if not token:
        logger.warning("EmailQueue: no Google credentials — falling back to SMTP")
        return False

    row = [
        to,
        subject,
        body,
        "PENDING",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "",
        candidate_name,
        role,
        priority,
    ]

    import httpx
    url = _APPEND_URL.format(sheet_id=settings.sheets_email_queue_id)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"values": [row]},
            )
            resp.raise_for_status()
        logger.info("EmailQueue: queued → %s | %s", to, subject[:60])
        return True
    except Exception as exc:
        logger.error("EmailQueue: sheet write failed: %s", exc)
        return False


async def _get_access_token(settings) -> Optional[str]:
    """Return a short-lived OAuth2 bearer token from the service account credentials."""
    try:
        import google.auth.transport.requests  # type: ignore
        from google.oauth2 import service_account  # type: ignore

        if settings.google_sa_credentials_json:
            info = json.loads(settings.google_sa_credentials_json)
        elif settings.google_sa_credentials_file:
            with open(settings.google_sa_credentials_file) as fh:
                info = json.load(fh)
        else:
            return None

        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception as exc:
        logger.error("EmailQueue: token error: %s", exc)
        return None
