"""SQLAlchemy async engine, session factory, and Base."""
import asyncio
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


def _import_models() -> None:
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


# Schema is ensured lazily on first DB use. Vercel's serverless runtime does not
# reliably run FastAPI's startup/lifespan, so we cannot depend on init_db() alone.
_schema_lock = asyncio.Lock()
_schema_ready = False


async def ensure_schema() -> None:
    """Create tables and backfill any missing columns. Idempotent; runs once per process.

    Each statement runs in AUTOCOMMIT so one failure can't abort the rest (a shared
    transaction would poison every later statement after the first error)."""
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if _schema_ready:
            return
        _import_models()
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        except Exception:
            pass

        if _is_postgres:
            # Every column the models declare → ADD COLUMN IF NOT EXISTS (no-op if present).
            # Known columns with defaults go first so they get the right default.
            stmts = [
                "ALTER TABLE wa_queue ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0",
                "ALTER TABLE wa_queue ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMP",
                "ALTER TABLE wa_connection ADD COLUMN IF NOT EXISTS last_poll_at TIMESTAMP",
                "ALTER TABLE wa_connection ADD COLUMN IF NOT EXISTS pending_command VARCHAR(20)",
            ]
            dialect = engine.dialect
            for table in Base.metadata.sorted_tables:
                for col in table.columns:
                    if col.primary_key:
                        continue
                    try:
                        coltype = col.type.compile(dialect=dialect)
                        stmts.append(
                            f'ALTER TABLE {table.name} '
                            f'ADD COLUMN IF NOT EXISTS {col.name} {coltype}'
                        )
                    except Exception:
                        pass
            try:
                async with engine.connect() as conn:
                    conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                    for stmt in stmts:
                        try:
                            await conn.exec_driver_sql(stmt)
                        except Exception:
                            pass
            except Exception:
                pass

        _schema_ready = True


async def init_db() -> None:
    """Startup hook (when the runtime runs it). Lazy ensure_schema covers the rest."""
    await ensure_schema()


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency that yields a scoped DB session."""
    if not _schema_ready:
        await ensure_schema()
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
