"""King Arthur Baking site config."""

from menu.ingest.registry import SiteConfig

KING_ARTHUR = SiteConfig(
    name="king_arthur",
    base_url="https://www.kingarthurbaking.com",
    sitemap_urls=("https://www.kingarthurbaking.com/sitemap.xml",),
    url_pattern=r"^https://www\.kingarthurbaking\.com/recipes/[^/]+-recipe$",
    politeness_delay=1.0,
    unit_system="us",
    nutrition_sources=("fdc", "cnf"),
)
