"""Unit tests for the scoring engine."""
import pytest
from app.services.scoring import score_candidate, SHORTLIST_THRESHOLD, REVIEW_THRESHOLD


def _score(**kwargs):
    defaults = dict(
        candidate_skills=[],
        candidate_experience=None,
        candidate_expected_salary=None,
        candidate_location=None,
        candidate_role=None,
        job_title="Python Developer",
        job_skills=["Python", "FastAPI", "SQL"],
        job_experience_min=2.0,
        job_experience_max=5.0,
        job_salary_min=800_000,
        job_salary_max=1_200_000,
        job_location="Bangalore",
    )
    defaults.update(kwargs)
    return score_candidate(**defaults)


def test_perfect_candidate_scores_high():
    result = _score(
        candidate_skills=["Python", "FastAPI", "SQL", "Docker"],
        candidate_experience=3.0,
        candidate_expected_salary=1_000_000,
        candidate_location="Bangalore",
        candidate_role="Python Developer",
    )
    assert result.total >= SHORTLIST_THRESHOLD
    assert result.decision == "AUTO_SHORTLIST"


def test_mismatched_candidate_scores_low():
    result = _score(
        candidate_skills=["Photoshop", "Illustrator"],
        candidate_experience=0.5,
        candidate_expected_salary=3_000_000,
        candidate_location="Mumbai",
        candidate_role="Graphic Designer",
    )
    assert result.total < REVIEW_THRESHOLD
    assert result.decision == "REJECT"


def test_partial_match_is_manual_review():
    result = _score(
        candidate_skills=["Python"],
        candidate_experience=1.0,
        candidate_expected_salary=1_100_000,
        candidate_location="Mumbai",
        candidate_role="Backend Engineer",
    )
    assert REVIEW_THRESHOLD <= result.total < SHORTLIST_THRESHOLD
    assert result.decision == "MANUAL_REVIEW"


def test_score_total_within_bounds():
    result = _score(
        candidate_skills=["Python", "FastAPI", "SQL"],
        candidate_experience=3.0,
        candidate_expected_salary=1_000_000,
        candidate_location="Bangalore",
        candidate_role="Python Developer",
    )
    assert 0.0 <= result.total <= 100.0


def test_score_breakdown_keys():
    result = _score()
    expected_keys = {"skills_match", "experience_fit", "salary_fit", "location_fit", "role_fit"}
    assert set(result.breakdown.keys()) == expected_keys


def test_no_job_skills_gives_neutral_skills_score():
    result = score_candidate(
        candidate_skills=["Python"],
        candidate_experience=3.0,
        candidate_expected_salary=None,
        candidate_location=None,
        candidate_role=None,
        job_title=None,
        job_skills=[],
        job_experience_min=None,
        job_experience_max=None,
        job_salary_min=None,
        job_salary_max=None,
        job_location=None,
    )
    # skills weight=40, neutral multiplier=0.5 → 20
    assert result.breakdown["skills_match"] == 20.0


def test_overqualified_penalised_mildly():
    in_range = _score(candidate_experience=3.0)
    over = _score(candidate_experience=15.0)
    assert in_range.breakdown["experience_fit"] > over.breakdown["experience_fit"]


def test_candidate_cheaper_than_budget_gets_full_salary_score():
    result = _score(candidate_expected_salary=500_000)  # well under budget min
    assert result.breakdown["salary_fit"] == 15.0


def test_remote_job_location_gives_full_score():
    result = _score(
        job_location="Remote",
        candidate_location="Mumbai",
    )
    assert result.breakdown["location_fit"] == 10.0
