"""JD Analyzer — parses raw job description text and extracts structured fields.

Uses regex + keyword heuristics.  Designed to be swapped for an LLM-backed
implementation by replacing _extract_* helpers while keeping the public API.
"""
import re
from typing import Optional
from app.schemas.job import JDAnalysisResult
from app.utils.logging import get_logger

logger = get_logger(__name__)

# ── Salary patterns ────────────────────────────────────────────────────────────
# Matches "₹15 LPA – ₹22 LPA", "12-18 LPA", "Rs. 10L to 15L", "800000-1200000"
_SALARY_RANGE_RE = re.compile(
    r"(?:₹|rs\.?\s*|inr\s*)?"
    r"(\d+(?:\.\d+)?)\s*(k|l|lakh|lpa)?"
    r"\s*(?:[-–—to]+)\s*"
    r"(?:₹|rs\.?\s*|inr\s*)?"
    r"(\d+(?:\.\d+)?)\s*(k|l|lakh|lpa)?",
    re.IGNORECASE,
)
_SALARY_SINGLE_RE = re.compile(
    r"(?:₹|rs\.?\s*|inr\s*)(\d+(?:\.\d+)?)\s*(k|l|lakh|lpa)?",
    re.IGNORECASE,
)
_SALARY_KEYWORDS = re.compile(
    r"(salary|ctc|compensation|pay|package|stipend)\s*[:\-]?\s*", re.IGNORECASE
)

# ── Experience patterns ────────────────────────────────────────────────────────
_EXP_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[–\-—to]+\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)",
    re.IGNORECASE,
)
_EXP_SINGLE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp\.?)",
    re.IGNORECASE,
)
_EXP_MIN_RE = re.compile(
    r"(?:minimum|min\.?|at\s+least)\s+(\d+(?:\.\d+)?)\s*(?:years?|yrs?)",
    re.IGNORECASE,
)

# ── Notice period ──────────────────────────────────────────────────────────────
_NOTICE_RE = re.compile(
    r"notice\s+period\s*[:\-]?\s*(\d+)\s*(days?|months?|weeks?)",
    re.IGNORECASE,
)

# ── Job type ───────────────────────────────────────────────────────────────────
_JOB_TYPE_TOKENS = {
    "full-time": "full-time",
    "full time": "full-time",
    "part-time": "part-time",
    "part time": "part-time",
    "contract": "contract",
    "freelance": "freelance",
    "internship": "internship",
    "remote": "remote",
    "hybrid": "hybrid",
    "on-site": "on-site",
    "onsite": "on-site",
}

# ── Education ──────────────────────────────────────────────────────────────────
_EDU_TOKENS = {
    "b.tech": "B.Tech",
    "btech": "B.Tech",
    "b.e.": "B.E.",
    "be ": "B.E.",
    "m.tech": "M.Tech",
    "mtech": "M.Tech",
    "mba": "MBA",
    "bca": "BCA",
    "mca": "MCA",
    "b.sc": "B.Sc",
    "m.sc": "M.Sc",
    "phd": "PhD",
    "diploma": "Diploma",
    "graduate": "Graduate",
    "postgraduate": "Postgraduate",
}

# ── Common tech/domain skills ─────────────────────────────────────────────────
_SKILL_TOKENS = [
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust", "ruby",
    "react", "angular", "vue", "node.js", "django", "fastapi", "flask", "spring",
    "sql", "mysql", "postgresql", "mongodb", "redis", "elasticsearch",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible",
    "machine learning", "deep learning", "nlp", "data science", "pandas", "numpy",
    "autocad", "solidworks", "catia", "revit", "fusion 360",
    "photoshop", "illustrator", "figma", "sketch", "indesign",
    "excel", "tableau", "power bi", "sap", "salesforce",
    "html", "css", "git", "linux", "rest api", "graphql",
    "manual testing", "selenium", "jira", "agile", "scrum",
]


def _apply_unit(num_str: str, unit: str | None) -> float:
    """Convert a numeric string + optional unit suffix to a float rupee value."""
    try:
        val = float(num_str.replace(",", "").strip())
    except ValueError:
        return 0.0
    if not unit:
        return val
    u = unit.lower()
    if u == "k":
        return val * 1_000
    if u in ("l", "lakh", "lpa"):
        return val * 100_000
    return val


def _extract_skills(text: str) -> list[str]:
    lower = text.lower()
    found = []
    for token in _SKILL_TOKENS:
        # word-boundary aware check
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, lower):
            found.append(token.title() if " " not in token else token)
    return list(dict.fromkeys(found))  # preserve order, deduplicate


def _extract_experience(text: str) -> tuple[Optional[float], Optional[float]]:
    # Try range first: "3–6 years", "3-6 years"
    m = _EXP_RE.search(text)
    if m:
        return float(m.group(1)), float(m.group(2))
    # Single value: "5 years of experience"
    m2 = _EXP_SINGLE_RE.search(text)
    if m2:
        val = float(m2.group(1))
        return val, val
    # Minimum phrasing
    m3 = _EXP_MIN_RE.search(text)
    if m3:
        val = float(m3.group(1))
        return val, None
    return None, None


def _extract_salary(text: str) -> tuple[Optional[float], Optional[float]]:
    # Priority 1: search in a window after a salary keyword
    for km in _SALARY_KEYWORDS.finditer(text):
        snippet = text[km.start(): km.start() + 80]
        mr = _SALARY_RANGE_RE.search(snippet)
        if mr:
            lo = _apply_unit(mr.group(1), mr.group(2))
            hi = _apply_unit(mr.group(3), mr.group(4))
            if lo < 1000 and hi < 1000 and lo > 0:  # bare numbers → assume LPA
                lo *= 100_000
                hi *= 100_000
            return lo or None, hi or None
        ms = _SALARY_SINGLE_RE.search(snippet)
        if ms:
            val = _apply_unit(ms.group(1), ms.group(2))
            if val < 1000:
                val *= 100_000
            return val, val

    # Priority 2: scan full text for a range that has explicit currency/unit markers
    for m in _SALARY_RANGE_RE.finditer(text):
        lo = _apply_unit(m.group(1), m.group(2))
        hi = _apply_unit(m.group(3), m.group(4))
        # Only trust bare-number ranges when they're plausibly salary-sized (> 1000 after conversion)
        if m.group(2) or m.group(4):  # at least one side has an explicit unit
            if lo < 1000:
                lo *= 100_000
            if hi < 1000:
                hi *= 100_000
            return lo or None, hi or None

    return None, None


def _extract_notice_period(text: str) -> Optional[int]:
    m = _NOTICE_RE.search(text)
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    if "month" in unit:
        return value * 30
    if "week" in unit:
        return value * 7
    return value


def _extract_location(text: str) -> Optional[str]:
    cities = [
        "Mumbai", "Delhi", "Bangalore", "Bengaluru", "Hyderabad", "Chennai",
        "Kolkata", "Pune", "Ahmedabad", "Surat", "Jaipur", "Lucknow",
        "Noida", "Gurugram", "Gurgaon", "Chandigarh", "Indore", "Bhopal",
        "Remote", "Work from Home", "WFH", "Pan India",
    ]
    lower = text.lower()
    for city in cities:
        if city.lower() in lower:
            return city
    return None


def _extract_education(text: str) -> Optional[str]:
    lower = text.lower()
    for token, label in _EDU_TOKENS.items():
        if token in lower:
            return label
    return None


def _extract_job_type(text: str) -> Optional[str]:
    lower = text.lower()
    for token, label in _JOB_TYPE_TOKENS.items():
        if token in lower:
            return label
    return None


def _extract_title(text: str) -> Optional[str]:
    """Best-effort: grab the first non-empty line as the job title."""
    for line in text.splitlines():
        line = line.strip()
        if line and len(line) < 120:
            return line
    return None


def analyze_jd(raw_jd: str) -> JDAnalysisResult:
    """Parse a raw JD string and return structured extraction results.

    All fields are best-effort; None means not found.
    """
    logger.debug("Analyzing JD (%d chars)", len(raw_jd))
    salary_min, salary_max = _extract_salary(raw_jd)
    exp_min, exp_max = _extract_experience(raw_jd)
    return JDAnalysisResult(
        title=_extract_title(raw_jd),
        skills=_extract_skills(raw_jd),
        experience_min=exp_min,
        experience_max=exp_max,
        salary_min=salary_min,
        salary_max=salary_max,
        location=_extract_location(raw_jd),
        notice_period_days=_extract_notice_period(raw_jd),
        education=_extract_education(raw_jd),
        job_type=_extract_job_type(raw_jd),
        description=raw_jd[:500],
    )


def _num(v):
    """Coerce an LLM value to float, else None."""
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


async def analyze_jd_smart(raw_jd: str) -> JDAnalysisResult:
    """LLM-backed JD extraction (Gemini/Claude) with the regex analyzer as fallback.

    Far more accurate than heuristics — correctly picks the real role title (not the
    'JOB DESCRIPTION' heading) and pulls experience/salary even when phrased oddly.
    """
    from app.services.llm import llm_provider, llm_json

    if llm_provider() == "none":
        return analyze_jd(raw_jd)

    prompt = (
        "You are parsing a job description. Return ONLY a JSON object with these keys:\n"
        '  "title": the actual ROLE title (e.g. "AI & Process Automation Engineer") — '
        'NOT a heading like "Job Description"/"JD"/"Position Title".\n'
        '  "skills": array of specific skills/tools (strings).\n'
        '  "experience_min": number (years) or null.  "experience_max": number or null.\n'
        '  "salary_min": MONTHLY salary in INR rupees or null.  "salary_max": monthly INR or null '
        "(convert LPA/annual to monthly: annual÷12; e.g. 6 LPA → 50000).\n"
        '  "location": city/region string or null.\n'
        '  "education": required education string or null.\n'
        '  "job_type": one of full-time/part-time/contract/internship/remote or null.\n'
        '  "description": a 1-2 sentence plain summary of the role.\n\n'
        "Job description:\n\n" + (raw_jd or "")[:6000]
    )
    try:
        data = await llm_json(prompt, max_tokens=800)
    except Exception:
        data = None
    if not isinstance(data, dict):
        return analyze_jd(raw_jd)

    try:
        skills = data.get("skills") or []
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",") if s.strip()]
        return JDAnalysisResult(
            title=(data.get("title") or "").strip() or None,
            skills=[str(s).strip() for s in skills if str(s).strip()][:20],
            experience_min=_num(data.get("experience_min")),
            experience_max=_num(data.get("experience_max")),
            salary_min=_num(data.get("salary_min")),
            salary_max=_num(data.get("salary_max")),
            location=(data.get("location") or "").strip() or None,
            notice_period_days=None,
            education=(data.get("education") or "").strip() or None,
            job_type=(data.get("job_type") or "").strip() or None,
            description=(data.get("description") or "").strip() or (raw_jd or "")[:500],
        )
    except Exception:
        return analyze_jd(raw_jd)
