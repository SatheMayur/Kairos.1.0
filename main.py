"""Application entry point — FastAPI app factory + lifespan."""
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.database import init_db, AsyncSessionLocal
from app.api import api_router
from app.api.ui import router as ui_router
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.startup_seed import seed_if_empty
from app.utils.logging import configure_logging
from app.config import get_settings

configure_logging()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_if_empty(session)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="AI Recruitment System",
    description=(
        "Automated recruitment pipeline: JD analysis → candidate sourcing → "
        "shortlisting → outreach → interview scheduling."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global error handlers ─────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all for any unhandled exception — log to DB and return 500."""
    tb_str = traceback.format_exc()
    try:
        from app.utils.error_log import log_error
        await log_error(
            message=str(exc),
            source=f"http:{request.url.path}",
            level="ERROR",
            exc=exc,
            traceback_str=tb_str,
            method=request.method,
            path=str(request.url.path),
            status_code=500,
        )
    except Exception:
        pass
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__, "error": str(exc)[:300]},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Log 5xx HTTP exceptions; pass 4xx through silently."""
    if exc.status_code >= 500:
        try:
            from app.utils.error_log import log_error
            await log_error(
                message=str(exc.detail),
                source=f"http:{request.url.path}",
                level="ERROR",
                error_type="HTTPException",
                method=request.method,
                path=str(request.url.path),
                status_code=exc.status_code,
            )
        except Exception:
            pass
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "app" / "static")),
    name="static",
)

app.include_router(api_router, prefix="/api/v1")
app.include_router(ui_router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "env": settings.app_env}


@app.get("/", tags=["system"])
async def root():
    return RedirectResponse(url="/ui/")
