"""Tiny async helper for the AppSetting key/value store.

get_setting / set_setting are the only two ways the rest of the app should touch
the app_settings table. Keep it boring: no caching, no surprises.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.app_setting import AppSetting


async def get_setting(db: AsyncSession, key: str) -> str | None:
    """Return the stored value for `key`, or None if it was never set."""
    row = await db.get(AppSetting, key)
    return row.value if row else None


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    """Insert or update the value for `key`. Caller commits the session."""
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await db.flush()
