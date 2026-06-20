"""SQLAlchemy async engine, session factory, and Base."""
import asyncio
import os
import re

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
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
    # On Vercel serverless, holding a connection pool per function instance can
    # exhaust Neon's connection limit across many instances and cause intermittent
    # FUNCTION_INVOCATION_FAILED (500) errors. NullPool opens/closes a fresh
    # connection per request — the documented best practice for serverless + Neon.
    **({"poolclass": NullPool} if _is_postgres else {}),
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
    import app.models.conversation  # noqa: F401
    import app.models.app_setting   # noqa: F401
    import app.models.resume_doc     # noqa: F401
    import app.models.jd_doc         # noqa: F401


# Schema is ensured lazily on first DB use. Vercel's serverless runtime does not
# reliably run FastAPI's startup/lifespan, so we cannot depend on init_db() alone.
_schema_lock = asyncio.Lock()
_schema_ready = False


async def _dedupe_and_reconcile_in_session(db: AsyncSession) -> None:
    """The actual cleanup, operating on a caller-provided session.

    1. DEDUPE: collapse duplicate (candidate_id, job_id) shortlist rows to the
       single best one. "Best" = most-advanced status, tie-break highest score,
       then lowest id. The losers are deleted so the unique index can be created.
    2. RECONCILE: any entry marked INTERVIEW_SCHEDULED with no backing Interview
       row (one with a real scheduled_at) is downgraded to its prior sensible
       status — CONTACTED if a sent OutreachLog exists, else SHORTLISTED.

    Uses the ORM so it works identically on sqlite and Postgres.
    """
    from sqlalchemy import select, delete
    from app.models.shortlist import ShortlistEntry, ShortlistStatus, STATUS_RANK
    from app.models.interview import Interview
    from app.models.outreach import OutreachLog, OutreachStatus

    entries = (await db.execute(select(ShortlistEntry))).scalars().all()
    if not entries:
        return

    # ── 1. Dedupe ───────────────────────────────────────────────────────────
    by_pair: dict[tuple[int, int], list[ShortlistEntry]] = {}
    for e in entries:
        by_pair.setdefault((e.candidate_id, e.job_id), []).append(e)

    loser_ids: list[int] = []
    for rows in by_pair.values():
        if len(rows) < 2:
            continue
        # Pick the winner: most advanced, then highest score, then lowest id.
        rows_sorted = sorted(
            rows,
            key=lambda r: (STATUS_RANK.get(r.status, 0), r.score or 0.0, -r.id),
            reverse=True,
        )
        loser_ids.extend(r.id for r in rows_sorted[1:])

    if loser_ids:
        await db.execute(delete(ShortlistEntry).where(ShortlistEntry.id.in_(loser_ids)))
        await db.commit()

    # ── 2. Reconcile INTERVIEW_SCHEDULED with no backing interview ───────────
    scheduled = (await db.execute(
        select(ShortlistEntry).where(
            ShortlistEntry.status == ShortlistStatus.INTERVIEW_SCHEDULED
        )
    )).scalars().all()

    changed = False
    for entry in scheduled:
        iv = (await db.execute(
            select(Interview).where(
                Interview.candidate_id == entry.candidate_id,
                Interview.job_id == entry.job_id,
                Interview.scheduled_at.isnot(None),
            )
        )).scalars().first()
        if iv:
            continue  # backing interview exists — leave it alone

        # No real interview → fall back to the most sensible earlier status.
        sent_log = (await db.execute(
            select(OutreachLog).where(
                OutreachLog.candidate_id == entry.candidate_id,
                OutreachLog.job_id == entry.job_id,
                OutreachLog.status == OutreachStatus.SENT,
            )
        )).scalars().first()
        entry.status = (
            ShortlistStatus.CONTACTED if sent_log else ShortlistStatus.SHORTLISTED
        )
        changed = True

    if changed:
        await db.commit()


async def _dedupe_and_reconcile_shortlist(db: AsyncSession | None = None) -> None:
    """Startup cleanup wrapper. Idempotent; safe to run on every boot.

    Opens its own session when called without one (the schema-bootstrap path);
    tests pass their own session so the cleanup runs against the test DB.
    """
    if db is not None:
        await _dedupe_and_reconcile_in_session(db)
        return
    async with AsyncSessionLocal() as own:
        await _dedupe_and_reconcile_in_session(own)


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

        # Clean up the shortlist table BEFORE we add the unique index: collapse
        # any duplicate (candidate_id, job_id) rows down to the single best one,
        # and downgrade any INTERVIEW_SCHEDULED entry that has no backing interview.
        # Runs on every dialect so local sqlite stays clean too (the index itself
        # is only created on Postgres below; sqlite gets the constraint from
        # create_all on a fresh DB).
        try:
            await _dedupe_and_reconcile_shortlist()
        except Exception:
            pass

        if _is_postgres:
            # Add the unique index now that duplicates are gone. IF NOT EXISTS so
            # re-runs are no-ops; wrapped so a failure can never crash startup.
            try:
                async with engine.connect() as conn:
                    conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                    await conn.exec_driver_sql(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_shortlist_candidate_job "
                        "ON shortlist (candidate_id, job_id)"
                    )
            except Exception:
                pass

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
