"""UI router — serves Jinja2 admin pages at /ui/*"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter(prefix="/ui", tags=["ui"])

_PAGES = {
    "": ("dashboard.html", "dashboard"),
    "jobs": ("jobs.html", "jobs"),
    "candidates": ("candidates.html", "candidates"),
    "shortlist": ("shortlist.html", "shortlist"),
    "outreach": ("outreach.html", "outreach"),
    "interviews": ("interviews.html", "interviews"),
    "import": ("import.html", "import"),
    "system": ("system.html", "system"),
    "whatsapp": ("whatsapp.html", "whatsapp"),
}


def _render(request: Request, template: str, active: str) -> HTMLResponse:
    return templates.TemplateResponse(request, template, {"active": active})


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _render(request, "dashboard.html", "dashboard")

@router.get("/jobs", response_class=HTMLResponse)
async def jobs(request: Request):
    return _render(request, "jobs.html", "jobs")

@router.get("/candidates", response_class=HTMLResponse)
async def candidates(request: Request):
    return _render(request, "candidates.html", "candidates")

@router.get("/shortlist", response_class=HTMLResponse)
async def shortlist(request: Request):
    return _render(request, "shortlist.html", "shortlist")

@router.get("/outreach", response_class=HTMLResponse)
async def outreach(request: Request):
    return _render(request, "outreach.html", "outreach")

@router.get("/interviews", response_class=HTMLResponse)
async def interviews(request: Request):
    return _render(request, "interviews.html", "interviews")

@router.get("/import", response_class=HTMLResponse)
async def import_candidates(request: Request):
    return _render(request, "import.html", "import")

@router.get("/system", response_class=HTMLResponse)
async def system(request: Request):
    return _render(request, "system.html", "system")

@router.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp(request: Request):
    return _render(request, "whatsapp.html", "whatsapp")
