"""Site registry: adding a site to menu is mostly config."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SiteConfig(BaseModel):
    """Configuration for a recipe site.

    Anything site-specific lives here or in the site's module under
    menu.ingest.sites; the ingest pipeline itself stays generic.
    """

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    name: str
    base_url: str
    sitemap_url: str
    """Top-level sitemap to start discovery from."""
    url_pattern: str | None = None
    """Optional regex; only matching URLs are treated as recipe pages."""
    politeness_delay: Annotated[float, Field(ge=0)] = 1.0
    """Minimum seconds between requests to this site's host."""
    json_ld_test_id: str | None = None
    """Some sites embed their recipe JSON-LD behind a test id rather than
    a plain application/ld+json script tag."""


def get_site(name: str) -> SiteConfig:
    """Look up a site config by its registry name."""
    from menu.ingest import sites

    registry = sites.registry()
    if name not in registry:
        known = ", ".join(sorted(registry))
        raise KeyError(f"Unknown site {name!r}. Known sites: {known}")
    return registry[name]
