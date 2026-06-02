"""Adapter registry — one place that maps portal names to adapter instances.

Real adapters:
  - ApifyLinkedInAdapter  — proactive LinkedIn sourcing (when APIFY_API_TOKEN is set)

Naukri inbound applicants come via CSV export from your Naukri employer dashboard
uploaded at /ui/import — Apify cannot access employer-login-gated applicant data.

All other portals are stubbed until real credentials are provided.
"""
from app.adapters.base import BasePortalAdapter
from app.adapters.mock import MockAdapter
from app.models.candidate import CandidateSource
from app.config import get_settings


def _stub(src: CandidateSource) -> BasePortalAdapter:
    return MockAdapter(source=src, default_limit=10)


def build_registry(use_mock: bool, apify_token: str = "") -> dict[str, BasePortalAdapter]:
    """Return {portal_name: adapter} mapping."""
    adapters: dict[str, BasePortalAdapter] = {}

    # LinkedIn: Apify proactive sourcing — finds passive candidates from LinkedIn searches
    if apify_token and not use_mock:
        from app.adapters.apify import ApifyLinkedInAdapter
        adapters[CandidateSource.LINKEDIN.value] = ApifyLinkedInAdapter(apify_token)

    # Naukri inbound applicants must come via CSV export (/ui/import)
    # Apify Naukri actor scrapes job listings, NOT applicant profiles — disabled.
    stub_portals = [
        CandidateSource.NAUKRI,
        CandidateSource.INDEED,
        CandidateSource.APNA,
        CandidateSource.WORKINDIA,
        CandidateSource.JOBHAI,
        CandidateSource.INTERNSHALA,
        CandidateSource.FRESHERSWORLD,
        CandidateSource.SHINE,
        CandidateSource.PLACEMENTINDIA,
        CandidateSource.QUIKR,
        CandidateSource.CLICKINDIA,
        CandidateSource.OLX,
        CandidateSource.JORA,
        CandidateSource.FOUNDIT,
    ]
    if not apify_token or use_mock:
        stub_portals = [CandidateSource.LINKEDIN] + stub_portals

    for portal in stub_portals:
        if portal.value not in adapters:
            adapters[portal.value] = _stub(portal)

    return adapters


_registry: dict[str, BasePortalAdapter] | None = None


def get_registry() -> dict[str, BasePortalAdapter]:
    global _registry
    if _registry is None:
        settings = get_settings()
        _registry = build_registry(
            use_mock=settings.use_mock_adapters,
            apify_token=settings.apify_api_token,
        )
    return _registry


def reset_registry() -> None:
    """Force re-build on next get_registry() call (useful after config changes)."""
    global _registry
    _registry = None
