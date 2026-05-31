"""Write outreach emails directly into the Google Sheets Email Queue.

The Apps Script (AI_HR_AutoSend) polls the sheet every 5 minutes, sends
PENDING rows via Gmail, and marks them SENT.  This is the production-grade
delivery path — no SMTP credentials or open ports required.

Sheet columns (1-indexed):
  A=To  B=Subject  C=Body  D=Status  E=Created_At
  F=Sent_At  G=Candidate_Name  H=Role  I=Priority

Write path priority (first success wins):
  1. Apps Script Web App POST (APPS_SCRIPT_WEB_APP_URL) — no SA credentials needed
  2. Sheets REST API with SA credentials (GOOGLE_SA_CREDENTIALS_JSON env var)
  3. Sheets REST API with SA file (GOOGLE_SA_CREDENTIALS_FILE env var)
  4. Application Default Credentials (ADC) — works on GCP / Cloud Run
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_APPEND_URL = (
    "https://sheets.googleapis.com/v4/spreadsheets"
    "/{sheet_id}/values/Sheet1!A:I:append?valueInputOption=USER_ENTERED"
)
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503, 504}


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
    Tries Apps Script web app first (no SA credentials needed), then falls
    back to the Sheets REST API with SA / ADC credentials.
    """
    from app.config import get_settings
    settings = get_settings()

    payload = {
        "to": to,
        "subject": subject,
        "body": body,
        "candidate_name": candidate_name,
        "role": role,
        "priority": priority,
    }

    # Path 1: Apps Script Web App (preferred — no SA credentials required)
    if settings.apps_script_web_app_url:
        ok = await _queue_via_web_app(settings, payload)
        if ok:
            return True
        logger.warning("EmailQueue: web app failed, falling back to Sheets API")

    # Path 2-4: Sheets REST API (requires SA or ADC)
    return await _queue_via_sheets_api(settings, payload)


async def _queue_via_web_app(settings, payload: dict) -> bool:
    """POST to the Apps Script Web App — no SA credentials needed."""
    import asyncio
    import httpx
    data = dict(payload)
    if settings.apps_script_webhook_secret:
        data["secret"] = settings.apps_script_webhook_secret

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.post(
                    settings.apps_script_web_app_url,
                    json=data,
                )
                if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                result = resp.json()
                if result.get("success"):
                    logger.info("EmailQueue (web app): queued → %s | %s", payload["to"], payload["subject"][:60])
                    return True
                logger.warning("EmailQueue (web app): script error: %s", result.get("error"))
                return False
        except httpx.TimeoutException:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
                continue
            logger.error("EmailQueue (web app): timed out after %d attempts", _MAX_RETRIES)
        except Exception as exc:
            logger.error("EmailQueue (web app): attempt %d failed: %s", attempt, exc)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
                continue
    return False


async def _queue_via_sheets_api(settings, payload: dict) -> bool:
    """Append a row using the Sheets REST API (requires SA or ADC credentials)."""
    token = await _get_access_token(settings)
    if not token:
        logger.warning("EmailQueue: no Google credentials — falling back to SMTP")
        return False

    row = [
        payload["to"],
        payload["subject"],
        payload["body"],
        "PENDING",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "",
        payload.get("candidate_name", ""),
        payload.get("role", ""),
        payload.get("priority", "NORMAL"),
    ]

    import asyncio
    import httpx
    url = _APPEND_URL.format(sheet_id=settings.sheets_email_queue_id)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"values": [row]},
                )
                if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                    wait = 2 ** attempt
                    logger.warning(
                        "EmailQueue: HTTP %d — retrying in %ds (attempt %d/%d)",
                        resp.status_code, wait, attempt, _MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
            logger.info("EmailQueue (Sheets API): queued → %s | %s", payload["to"], payload["subject"][:60])
            return True
        except httpx.TimeoutException:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
                continue
            logger.error("EmailQueue: timed out after %d attempts for %s", _MAX_RETRIES, payload["to"])
        except Exception as exc:
            logger.error("EmailQueue: sheet write failed (attempt %d): %s", attempt, exc)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
                continue

    return False


async def _get_access_token(settings) -> Optional[str]:
    """Return a short-lived OAuth2 bearer token.

    Tries SA credentials first, then falls back to Application Default Credentials.
    """
    import google.auth.transport.requests  # type: ignore

    # 1. Service account from JSON string
    if settings.google_sa_credentials_json:
        try:
            from google.oauth2 import service_account  # type: ignore
            info = json.loads(settings.google_sa_credentials_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=[_SHEETS_SCOPE]
            )
            creds.refresh(google.auth.transport.requests.Request())
            return creds.token
        except Exception as exc:
            logger.error("EmailQueue: SA JSON token error: %s", exc)

    # 2. Service account from file path
    if settings.google_sa_credentials_file:
        try:
            from google.oauth2 import service_account  # type: ignore
            with open(settings.google_sa_credentials_file) as fh:
                info = json.load(fh)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=[_SHEETS_SCOPE]
            )
            creds.refresh(google.auth.transport.requests.Request())
            return creds.token
        except Exception as exc:
            logger.error("EmailQueue: SA file token error: %s", exc)

    # 3. Application Default Credentials (GCP / Cloud Run / gcloud auth)
    try:
        import google.auth  # type: ignore
        creds, _ = google.auth.default(scopes=[_SHEETS_SCOPE])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception as exc:
        logger.debug("EmailQueue: ADC not available: %s", exc)

    return None
