from fastapi import APIRouter
from app.api import jobs, candidates, shortlist, outreach, interviews
from app.api import cron
from app.api import import_csv
from app.api import sourcing
from app.api import webhook
from app.api import wa_bridge
from app.api import logs
from app.api.analytics import router as analytics_router

api_router = APIRouter()
api_router.include_router(jobs.router)
api_router.include_router(candidates.router)
api_router.include_router(shortlist.router)
api_router.include_router(outreach.router)
api_router.include_router(interviews.router)
api_router.include_router(cron.router)
api_router.include_router(import_csv.router)
api_router.include_router(sourcing.router)
api_router.include_router(webhook.router)
api_router.include_router(wa_bridge.router)
api_router.include_router(logs.router)
api_router.include_router(analytics_router)
