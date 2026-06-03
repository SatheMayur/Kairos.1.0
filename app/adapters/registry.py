"""Adapter registry — one place that maps portal names to adapter instances.

Production mode (USE_MOCK_ADAPTERS=false):
  - Only real adapters with valid credentials are registered.
  - No mock stubs in production — a portal without credentials is simply absent.
  - This prevents fake/mock candidates from appearing in real sourcing runs.
  - ApifyLinkedInAdapter registered when APIFY_API_TOKEN is set.

Development mode (USE_MOCK_ADAPTERS=true):
  - All portals stubbed with location-aware MockAdapter for local testing.

Naukri inbound applicants come via CSV export (/ui/import).
Apify Naukri actor scrapes job listings not candidate profiles — disabled.
"""
from app.adapters.base import BasePortalAdapter
from app.adapters.mock import MockAdapter
from app.models.candidate import CandidateSource
from app.config import get_settings

_DEV_PORTALS = [
    CandidateSource.LINKEDIN,
    CandidateSource.NAUKRI,
    CandidateSource.APNA,
    CandidateSource.WORKINDIA,
    CandidateSource.SHINE,
    CandidateSource.INTERNSHALA,
]


def build_registry(use_mock: bool, apify_token: str = "") -> dict[str, BasePortalAdapter]:
    """Return {portal_name: adapter} mapping.

    Production (use_mock=False): only real adapters. No mock fallback.
    Dev (use_mock=True): all portals stubbed with location-aware MockAdapter.
    """
    adapters: dict[str, BasePortalAdapter] = {}

    if use_mock:
        for portal in _DEV_PORTALS:
            adapters[portal.value] = MockAdapter(source=portal, default_limit=5)
        return adapters

    # Production: only register adapters backed by real credentials.
    # Portals without credentials are absent — never return fake candidates.
    if apify_token:
        from app.adapters.apify import ApifyLinkedInAdapter
        adapters[CandidateSource.LINKEDIN.value] = ApifyLinkedInAdapter(apify_token)

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
