from fastapi import APIRouter
from app.api import jobs, candidates, shortlist, outreach, interviews
from app.api import cron
from app.api import import_csv
from app.api import sourcing

api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(candidates.router)
api_router.include_router(shortlist.router)
api_router.include_router(outreach.router)
api_router.include_router(interviews.router)
api_router.include_router(cron.router)
api_router.include_router(import_csv.router)
api_router.include_router(sourcing.router)
