"""Adapter registry — one place that maps portal names to adapter instances.

Real adapters are thin stubs here; swap mock→real by setting USE_MOCK_ADAPTERS=false
and filling in the relevant API keys in .env.
"""
from app.adapters.base import BasePortalAdapter
from app.adapters.mock import MockAdapter
from app.models.candidate import CandidateSource
from app.config import get_settings


def _stub(src: CandidateSource) -> BasePortalAdapter:
    """Return a mock adapter labelled with the real portal source."""
    return MockAdapter(source=src, default_limit=10)


def build_registry(use_mock: bool) -> dict[str, BasePortalAdapter]:
    """Return {portal_name: adapter} mapping.

    When use_mock is True every adapter falls back to the mock implementation.
    Add real adapter classes here as they are implemented.
    """
    adapters: dict[str, BasePortalAdapter] = {}

    portals = [
        CandidateSource.LINKEDIN,
        CandidateSource.INDEED,
        CandidateSource.NAUKRI,
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

    for portal in portals:
        adapters[portal.value] = _stub(portal)  # replace _stub() with real class when ready

    return adapters


_registry: dict[str, BasePortalAdapter] | None = None


def get_registry() -> dict[str, BasePortalAdapter]:
    global _registry
    if _registry is None:
        settings = get_settings()
        _registry = build_registry(use_mock=settings.use_mock_adapters)
    return _registry
