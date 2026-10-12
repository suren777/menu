"""Site configuration: the per-site config every pipeline step reads.

Anything site-specific lives here or in the site's module under
menu.ingest.sites; the ingest pipeline itself stays generic.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class SiteConfig(BaseModel):
    """Configuration for a recipe site.

    Anything site-specific lives here or in the site's module under
    menu.ingest.sites; the ingest pipeline itself stays generic.
    """

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    name: str
    base_url: str
    sitemap_urls: tuple[str, ...]
    """Top-level sitemaps to start discovery from: indexes or plain urlsets."""
    url_pattern: str | None = None
    """Optional regex; only matching URLs are treated as recipe pages."""
    politeness_delay: Annotated[float, Field(ge=0)] = 1.0
    """Minimum seconds between requests to this site's host."""
    json_ld_test_id: str | None = None
    """Some sites embed their recipe JSON-LD behind a test id rather than
    a plain application/ld+json script tag."""
    unit_system: Literal["us", "imperial", "metric"] = "us"
    """Unit system the site's recipes are written in: decides how
    ambiguous units (cup, pint) are interpreted when parsing ingredient
    lines."""
    nutrition_sources: tuple[str, ...] = ("fdc",)
    """Preferred order of reference sources for nutrition values: the
    first source with a confirmed food for the ingredient wins."""
    ingredient_strip_patterns: tuple[str, ...] = ()
    """Regexes removed from each ingredient line before parsing, e.g.
    the site's brand name; case-insensitive."""
