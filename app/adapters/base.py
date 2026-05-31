"""Abstract base adapter — all portal adapters implement this interface."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from app.models.candidate import CandidateSource


@dataclass
class RawCandidate:
    """Normalised candidate record returned by every adapter."""
    name: str
    source: CandidateSource
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    skills: list[str] = field(default_factory=list)
    experience_years: Optional[float] = None
    current_salary: Optional[float] = None
    expected_salary: Optional[float] = None
    location: Optional[str] = None
    notice_period_days: Optional[int] = None
    education: Optional[str] = None
    current_employer: Optional[str] = None
    current_role: Optional[str] = None
    raw_profile: Optional[str] = None
    resume_url: Optional[str] = None
    source_ref: Optional[str] = None


class BasePortalAdapter(ABC):
    """One adapter per job portal — all share this contract."""

    @property
    @abstractmethod
    def source(self) -> CandidateSource:
        """Which portal this adapter represents."""

    @abstractmethod
    async def search(
        self,
        keywords: list[str],
        location: Optional[str] = None,
        experience_min: Optional[float] = None,
        experience_max: Optional[float] = None,
        limit: int = 50,
    ) -> list[RawCandidate]:
        """Search for candidates matching the JD criteria.

        Returns a list of normalised RawCandidate objects.
        Implementations must handle auth, pagination, and rate limiting.
        """

    async def health_check(self) -> bool:
        """Optional liveness probe — returns True if the portal is reachable."""
        return True
