"""Mock adapter — returns deterministic fake candidates for local dev and tests."""
import random
from typing import Optional
from app.adapters.base import BasePortalAdapter, RawCandidate
from app.models.candidate import CandidateSource

_FIRST = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Kavya", "Arjun", "Neha", "Raj", "Pooja"]
_LAST  = ["Sharma", "Patel", "Singh", "Mehta", "Joshi", "Gupta", "Verma", "Shah", "Nair", "Rao"]
_SKILLS_POOL = [
    "Python", "Java", "React", "Node.js", "SQL", "AWS", "Docker",
    "Machine Learning", "AutoCAD", "SolidWorks", "Photoshop", "Illustrator",
    "Excel", "Tableau", "Figma", "FastAPI", "Django", "Kubernetes",
]
_LOCATIONS = ["Mumbai", "Bangalore", "Surat", "Ahmedabad", "Pune", "Delhi", "Hyderabad"]
_EDU = ["B.Tech", "B.E.", "MBA", "BCA", "B.Sc", "M.Tech", "MCA"]
_EMPLOYERS = ["TCS", "Infosys", "Wipro", "HCL", "Accenture", "Capgemini", "Tech Mahindra"]


def _fake_candidate(idx: int, source: CandidateSource, keywords: list[str]) -> RawCandidate:
    rng = random.Random(idx)
    first, last = rng.choice(_FIRST), rng.choice(_LAST)
    exp = round(rng.uniform(0.5, 10.0), 1)
    base_salary = int(exp * rng.randint(40_000, 70_000))
    skills = rng.sample(_SKILLS_POOL, k=rng.randint(3, 7))
    # Inject some of the search keywords so scoring is realistic
    for kw in keywords[:2]:
        if kw not in skills:
            skills.append(kw)
    return RawCandidate(
        name=f"{first} {last}",
        source=source,
        email=f"{first.lower()}.{last.lower()}{idx}@example.com",
        phone=f"+91{rng.randint(7000000000, 9999999999)}",
        whatsapp=f"+91{rng.randint(7000000000, 9999999999)}",
        skills=skills,
        experience_years=exp,
        current_salary=float(base_salary),
        expected_salary=float(int(base_salary * rng.uniform(1.1, 1.3))),
        location=rng.choice(_LOCATIONS),
        notice_period_days=rng.choice([0, 15, 30, 60, 90]),
        education=rng.choice(_EDU),
        current_employer=rng.choice(_EMPLOYERS),
        current_role=rng.choice(["Software Engineer", "Designer", "Analyst", "Developer"]),
        raw_profile=f"Mock profile for {first} {last} with {exp}y experience.",
        source_ref=f"mock-{source.value.lower()}-{idx}",
    )


class MockAdapter(BasePortalAdapter):
    """Single mock adapter used for all portals when USE_MOCK_ADAPTERS=true."""

    def __init__(self, source: CandidateSource = CandidateSource.MOCK, default_limit: int = 10):
        self._source = source
        self._default_limit = default_limit

    @property
    def source(self) -> CandidateSource:
        return self._source

    async def search(
        self,
        keywords: list[str],
        location: Optional[str] = None,
        experience_min: Optional[float] = None,
        experience_max: Optional[float] = None,
        limit: int = 50,
    ) -> list[RawCandidate]:
        count = min(limit, self._default_limit)
        seed_offset = hash(self._source.value + "".join(keywords)) % 10_000
        return [_fake_candidate(seed_offset + i, self._source, keywords) for i in range(count)]
