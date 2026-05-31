"""THE one scoring engine — rates a candidate against a job on 0–100.

Weights (must sum to 100):
  skills_match     40
  experience_fit   25
  salary_fit       15
  location_fit     10
  role_fit         10
"""
from dataclasses import dataclass
from typing import Optional
from app.utils.logging import get_logger

logger = get_logger(__name__)

WEIGHTS = {
    "skills_match": 40,
    "experience_fit": 25,
    "salary_fit": 15,
    "location_fit": 10,
    "role_fit": 10,
}

assert sum(WEIGHTS.values()) == 100, "Scoring weights must sum to 100"

# Auto-shortlist threshold
SHORTLIST_THRESHOLD = 65.0
# Manual review band
REVIEW_THRESHOLD = 40.0


@dataclass
class ScoreResult:
    total: float                  # 0–100
    breakdown: dict[str, float]   # dimension → score (each 0–its weight)
    decision: str                 # AUTO_SHORTLIST | MANUAL_REVIEW | REJECT


def _skills_score(
    candidate_skills: list[str],
    required_skills: list[str],
    weight: float,
) -> float:
    if not required_skills:
        return weight * 0.5  # no requirements → neutral
    c_lower = {s.lower() for s in candidate_skills}
    r_lower = {s.lower() for s in required_skills}
    matched = len(c_lower & r_lower)
    ratio = matched / len(r_lower)
    return round(ratio * weight, 2)


def _experience_score(
    candidate_exp: Optional[float],
    exp_min: Optional[float],
    exp_max: Optional[float],
    weight: float,
) -> float:
    if candidate_exp is None:
        return weight * 0.4
    if exp_min is None and exp_max is None:
        return weight * 0.6  # no requirement → partial credit
    lo = exp_min or 0.0
    hi = exp_max or lo
    if lo <= candidate_exp <= hi:
        return weight  # perfect fit
    if candidate_exp < lo:
        shortfall = lo - candidate_exp
        penalty = min(shortfall / max(lo, 1.0), 1.0)
        return round(weight * (1.0 - penalty * 0.8), 2)
    # Over-qualified: slight discount
    excess = candidate_exp - hi
    penalty = min(excess / max(hi, 1.0), 0.5)
    return round(weight * (1.0 - penalty * 0.3), 2)


def _salary_score(
    expected_salary: Optional[float],
    budget_min: Optional[float],
    budget_max: Optional[float],
    weight: float,
) -> float:
    if expected_salary is None or (budget_min is None and budget_max is None):
        return weight * 0.5
    lo = budget_min or 0.0
    hi = budget_max or lo
    if lo <= expected_salary <= hi:
        return weight
    if expected_salary < lo:
        return weight  # candidate is cheaper than budget — positive
    over = expected_salary - hi
    overshoot_ratio = over / max(hi, 1.0)
    penalty = min(overshoot_ratio, 1.0)
    return round(weight * (1.0 - penalty), 2)


def _location_score(
    candidate_location: Optional[str],
    job_location: Optional[str],
    weight: float,
) -> float:
    if not candidate_location or not job_location:
        return weight * 0.5
    if candidate_location.lower() == job_location.lower():
        return weight
    # Partial credit for same state/metro (very simple heuristic)
    c_words = set(candidate_location.lower().split())
    j_words = set(job_location.lower().split())
    if c_words & j_words:
        return round(weight * 0.6, 2)
    remote_keywords = {"remote", "wfh", "work from home", "pan india"}
    if any(k in job_location.lower() for k in remote_keywords):
        return weight
    return 0.0


def _role_fit_score(
    candidate_role: Optional[str],
    job_title: Optional[str],
    weight: float,
) -> float:
    if not candidate_role or not job_title:
        return weight * 0.5
    c_words = set(candidate_role.lower().split())
    j_words = set(job_title.lower().split())
    common = c_words & j_words
    if common:
        ratio = len(common) / max(len(j_words), 1)
        return round(min(ratio * weight * 1.5, weight), 2)
    return 0.0


def score_candidate(
    *,
    candidate_skills: list[str],
    candidate_experience: Optional[float],
    candidate_expected_salary: Optional[float],
    candidate_location: Optional[str],
    candidate_role: Optional[str],
    job_title: Optional[str],
    job_skills: list[str],
    job_experience_min: Optional[float],
    job_experience_max: Optional[float],
    job_salary_min: Optional[float],
    job_salary_max: Optional[float],
    job_location: Optional[str],
) -> ScoreResult:
    """Score one candidate against one job. Returns ScoreResult with total 0–100."""
    breakdown = {
        "skills_match": _skills_score(candidate_skills, job_skills, WEIGHTS["skills_match"]),
        "experience_fit": _experience_score(
            candidate_experience, job_experience_min, job_experience_max, WEIGHTS["experience_fit"]
        ),
        "salary_fit": _salary_score(
            candidate_expected_salary, job_salary_min, job_salary_max, WEIGHTS["salary_fit"]
        ),
        "location_fit": _location_score(
            candidate_location, job_location, WEIGHTS["location_fit"]
        ),
        "role_fit": _role_fit_score(candidate_role, job_title, WEIGHTS["role_fit"]),
    }
    total = round(sum(breakdown.values()), 2)
    if total >= SHORTLIST_THRESHOLD:
        decision = "AUTO_SHORTLIST"
    elif total >= REVIEW_THRESHOLD:
        decision = "MANUAL_REVIEW"
    else:
        decision = "REJECT"
    logger.debug("Score %s: total=%.1f decision=%s", candidate_role, total, decision)
    return ScoreResult(total=total, breakdown=breakdown, decision=decision)
