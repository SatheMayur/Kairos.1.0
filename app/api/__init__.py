from fastapi import APIRouter
from app.api import jobs, candidates, shortlist, outreach, interviews

api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(candidates.router)
api_router.include_router(shortlist.router)
api_router.include_router(outreach.router)
api_router.include_router(interviews.router)
