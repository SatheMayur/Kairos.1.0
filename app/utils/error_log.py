"""Central error logging utility.

Call `await log_error(...)` from any async context to persist an error to the DB.
This is intentionally resilient — it will never raise or crash the caller.
"""
from __future__ import annotations

import logging
import re
import traceback as tb
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Patterns for secrets that must never be persisted to the error_log table.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_KV_RE = re.compile(
    r'("?(?:apna_token|token|password|authorization|raven-token|secret|api_key)"?\s*[:=]\s*"?)[^"\s,&}]+',
    re.IGNORECASE,
)


def _redact(text: Optional[str]) -> Optional[str]:
    """Strip JWTs and secret key/value pairs before anything is stored/exposed."""
    if not text:
        return text
    text = _JWT_RE.sub("<redacted-token>", text)
    text = _KV_RE.sub(r"\1<redacted>", text)
    return text


async def log_error(
    message: str,
    *,
    source: str = "app",
    level: str = "ERROR",
    error_type: Optional[str] = None,
    traceback_str: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    status_code: Optional[int] = None,
    request_body: Optional[str] = None,
    exc: Optional[BaseException] = None,
) -> None:
    """Write one error entry to the error_log table. Never raises."""
    if exc is not None:
        error_type = error_type or type(exc).__name__
        if traceback_str is None:
            traceback_str = tb.format_exc()

    try:
        from app.database import AsyncSessionLocal
        from app.models.error_log import ErrorLog

        async with AsyncSessionLocal() as db:
            entry = ErrorLog(
                logged_at=datetime.utcnow(),
                level=level.upper(),
                source=source[:128],
                error_type=(error_type or "UnknownError")[:128],
                message=_redact(message)[:4000],
                traceback=_redact(traceback_str)[:8000] if traceback_str else None,
                method=method,
                path=path[:512] if path else None,
                status_code=status_code,
                request_body=_redact(request_body)[:2000] if request_body else None,
            )
            db.add(entry)
            await db.commit()
    except Exception as inner:
        # Never let error logging cause a secondary failure
        logger.error("[error_log] Failed to persist error log: %s", inner)
