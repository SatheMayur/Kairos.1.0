"""Application entry point — FastAPI app factory + lifespan."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
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
