"""SQLAlchemy async engine, session factory, and Base."""
import os
import re

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

# Neon provides two URLs:
#   DATABASE_URL         = pooled (PgBouncer) — incompatible with SQLAlchemy prepared statements
#   DATABASE_URL_UNPOOLED = direct connection  — correct for SQLAlchemy/asyncpg
# Prefer the unpooled URL when available.
_db_url = (
    os.environ.get("DATABASE_URL_UNPOOLED")
    or os.environ.get("POSTGRES_URL_NON_POOLING")
    or settings.database_url
)

_is_postgres = _db_url.startswith("postgresql") or _db_url.startswith("postgres")

if _is_postgres:
    # Rewrite to asyncpg driver scheme
    _db_url = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", _db_url)

    # asyncpg does not understand sslmode= in the URL — strip it and pass ssl via connect_args
    _db_url = re.sub(r"[?&]sslmode=[^&]*", "", _db_url).rstrip("?")

_connect_args = {"ssl": "require"} if _is_postgres else {}

engine = create_async_engine(
    _db_url,
    echo=settings.app_env == "development",
    future=True,
    connect_args=_connect_args,
    **({"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True} if _is_postgres else {}),
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Create all tables on startup."""
    import app.models.candidate     # noqa: F401
    import app.models.job           # noqa: F401
    import app.models.shortlist     # noqa: F401
    import app.models.interview     # noqa: F401
    import app.models.outreach      # noqa: F401
    import app.models.wa_queue      # noqa: F401
    import app.models.wa_connection # noqa: F401
    import app.models.watchdog      # noqa: F401
    import app.models.error_log     # noqa: F401
    import app.models.daily_plan    # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if _is_postgres:
            # create_all never ALTERs existing tables, so any column added to a
            # model after its table already existed in production is missing.
            #
            # 1) Explicit backfills (with sensible defaults) for known columns.
            _column_backfills = [
                "ALTER TABLE wa_connection ADD COLUMN IF NOT EXISTS last_poll_at TIMESTAMP",
                "ALTER TABLE wa_connection ADD COLUMN IF NOT EXISTS pending_command VARCHAR(20)",
                "ALTER TABLE wa_queue ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0",
                "ALTER TABLE wa_queue ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMP",
            ]
            for stmt in _column_backfills:
                try:
                    await conn.exec_driver_sql(stmt)
                except Exception:  # never let a backfill block startup
                    pass

            # 2) General safety net: ensure every column the models declare exists.
            #    ADD COLUMN IF NOT EXISTS is a no-op when the column is already there,
            #    so this stops schema-drift bugs from recurring one column at a time.
            dialect = engine.dialect
            for table in Base.metadata.sorted_tables:
                for col in table.columns:
                    if col.primary_key:
                        continue
                    try:
                        coltype = col.type.compile(dialect=dialect)
                        await conn.exec_driver_sql(
                            f'ALTER TABLE {table.name} '
                            f'ADD COLUMN IF NOT EXISTS {col.name} {coltype}'
                        )
                    except Exception:
                        pass


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency that yields a scoped DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
