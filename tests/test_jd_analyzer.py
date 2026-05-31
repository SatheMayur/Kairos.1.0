"""Unit tests for JD analyzer."""
import pytest
from app.services.jd_analyzer import analyze_jd


SAMPLE_JD = """
Sr. Python Developer

We are looking for an experienced Python Developer to join our team in Bangalore.

Requirements:
- 3-6 years of experience in Python, FastAPI, and SQL
- Strong knowledge of Docker, AWS, and REST API
- B.Tech / B.E. in Computer Science or related field
- Notice period: 30 days or less

Salary: ₹12-18 LPA
Job Type: Full-time
"""


def test_extracts_skills():
    result = analyze_jd(SAMPLE_JD)
    skills_lower = [s.lower() for s in result.skills]
    assert "python" in skills_lower
    assert "fastapi" in skills_lower
    assert "sql" in skills_lower
    assert "docker" in skills_lower
    assert "aws" in skills_lower


def test_extracts_experience():
    result = analyze_jd(SAMPLE_JD)
    assert result.experience_min == 3.0
    assert result.experience_max == 6.0


def test_extracts_location():
    result = analyze_jd(SAMPLE_JD)
    assert result.location == "Bangalore"


def test_extracts_education():
    result = analyze_jd(SAMPLE_JD)
    assert result.education in ("B.Tech", "B.E.")


def test_extracts_notice_period():
    result = analyze_jd(SAMPLE_JD)
    assert result.notice_period_days == 30


def test_extracts_job_type():
    result = analyze_jd(SAMPLE_JD)
    assert result.job_type == "full-time"


def test_empty_jd_returns_defaults():
    result = analyze_jd("")
    assert result.skills == []
    assert result.experience_min is None
    assert result.salary_min is None


def test_extracts_salary():
    jd = "Salary: ₹15 LPA to ₹20 LPA"
    result = analyze_jd(jd)
    assert result.salary_min == 1_500_000.0
    assert result.salary_max == 2_000_000.0
