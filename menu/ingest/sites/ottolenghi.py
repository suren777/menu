"""Ottolenghi site config."""

from menu.ingest.registry import SiteConfig

OTTOLENGHI = SiteConfig(
    name="ottolenghi",
    base_url="https://ottolenghi.co.uk",
    sitemap_urls=("https://ottolenghi.co.uk/sitemap_metaobject_pages_1.xml",),
    url_pattern=r"^https://ottolenghi\.co\.uk/pages/recipes/[^/]+$",
    politeness_delay=1.0,
    unit_system="imperial",
    nutrition_sources=("cofid", "fdc"),
)
