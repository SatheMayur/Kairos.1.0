# AI Recruitment System

Automated end-to-end recruitment pipeline built with Python 3.11, FastAPI, and SQLAlchemy.

## What it does

| Stage | What happens |
|-------|-------------|
| **JD Analysis** | Paste/upload a JD → extracts role, skills, experience, salary, location, notice period, education |
| **Candidate Sourcing** | Fans out to 15 job portals concurrently; mock adapters used by default |
| **Shortlisting** | One scoring engine: skills (40%) + experience (25%) + salary (15%) + location (10%) + role fit (10%) |
| **Outreach** | Email / WhatsApp / SMS / Call (placeholder) — one service, auto-rendered templates |
| **Scheduling** | Propose slots → candidate confirms via link → meet link generated → 24h reminder |
| **Background Jobs** | APScheduler: sourcing every 6h, outreach every 1h, reminders daily at 8 AM UTC |

Recruiter only touches: final interviews + hiring decisions.

---

## Folder structure

```
recruitment_system/
├── main.py                     # FastAPI app + lifespan
├── requirements.txt
├── .env.example
├── Dockerfile
├── pytest.ini
├── app/
│   ├── config.py               # All env-var access (one place)
│   ├── database.py             # SQLAlchemy async engine + Base
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── job.py
│   │   ├── candidate.py
│   │   ├── shortlist.py
│   │   ├── outreach.py
│   │   └── interview.py
│   ├── schemas/                # Pydantic request/response schemas
│   │   ├── job.py
│   │   ├── candidate.py
│   │   ├── shortlist.py
│   │   ├── outreach.py
│   │   └── interview.py
│   ├── services/               # Business logic (no route handlers here)
│   │   ├── jd_analyzer.py      # THE one JD parser
│   │   ├── scoring.py          # THE one scoring engine
│   │   ├── sourcing.py         # THE one sourcing orchestrator
│   │   ├── outreach.py         # THE one outreach service
│   │   └── scheduling.py       # THE one scheduling service
│   ├── adapters/               # Portal adapters (15 portals, mock by default)
│   │   ├── base.py             # Abstract interface
│   │   ├── mock.py             # Mock adapter
│   │   └── registry.py         # Portal → adapter mapping
│   ├── api/                    # FastAPI route handlers (thin, no business logic)
│   │   ├── jobs.py
│   │   ├── candidates.py
│   │   ├── shortlist.py
│   │   ├── outreach.py
│   │   └── interviews.py
│   ├── jobs/
│   │   └── scheduler.py        # APScheduler background jobs
│   └── utils/
│       ├── logging.py
│       └── retry.py
├── tests/
│   ├── conftest.py
│   ├── test_jd_analyzer.py
│   ├── test_scoring.py
│   ├── test_sourcing.py
│   ├── test_outreach.py
│   ├── test_scheduling.py
│   └── test_api.py
└── sample_data/
    ├── sample_jd.txt
    └── sample_candidates.json
```

---

## Setup

```bash
# 1. Clone / enter the directory
cd recruitment_system

# 2. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set SMTP credentials, Twilio keys, etc.
# Leave USE_MOCK_ADAPTERS=true for local dev (no portal keys needed)

# 5. Run
uvicorn main:app --reload
```

Open http://localhost:8000/docs for the interactive API.

---

## Run with Docker

```bash
docker build -t recruitment-system .
docker run -p 8000:8000 --env-file .env recruitment-system
```

---

## Tests

```bash
# Run all tests
pytest

# With coverage
pytest --cov=app --cov-report=term-missing

# Run a specific test file
pytest tests/test_scoring.py -v
```

---

## Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/jobs/analyze-jd` | Parse JD text, return extracted fields |
| POST | `/api/v1/jobs/from-jd` | Parse JD + create job record |
| POST | `/api/v1/jobs/{id}/source` | Trigger sourcing for a job |
| GET  | `/api/v1/shortlist?job_id=1` | View shortlist with scores |
| POST | `/api/v1/shortlist/score/{job_id}/{cand_id}` | Score one candidate |
| POST | `/api/v1/outreach/bulk/{job_id}` | Contact all shortlisted candidates |
| POST | `/api/v1/interviews/propose` | Propose interview slots to candidate |
| GET  | `/api/v1/interviews/confirm/{token}?slot=0` | Candidate confirms a slot |
| POST | `/api/v1/interviews/reminders` | Manually trigger reminder dispatch |

Full interactive docs: **http://localhost:8000/docs**

---

## Adding a real portal adapter

1. Create `app/adapters/yourportal.py` implementing `BasePortalAdapter`
2. Register it in `app/adapters/registry.py` — replace `_stub(CandidateSource.YOURPORTAL)` with `YourPortalAdapter(api_key=settings.yourportal_api_key)`
3. Add the API key to `.env.example` and `app/config.py`

No other files need to change.

---

## Scoring thresholds

| Score | Decision |
|-------|----------|
| ≥ 65 | AUTO_SHORTLIST |
| 40–64 | MANUAL_REVIEW |
| < 40 | REJECT |

Rejection emails are **never auto-sent** — drafts only, recruiter must approve.
