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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency that yields a scoped DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
