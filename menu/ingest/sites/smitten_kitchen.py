"""Smitten Kitchen site config."""

from menu.ingest.registry import SiteConfig

SMITTEN_KITCHEN = SiteConfig(
    name="smitten_kitchen",
    base_url="https://smittenkitchen.com",
    sitemap_urls=(
        "https://smittenkitchen.com/sitemap.xml",
        "https://smittenkitchen.com/sitemap-2.xml",
    ),
    url_pattern=r"^https://smittenkitchen\.com/\d{4}/\d{2}/[^/]+/$",
    politeness_delay=3.0,
    unit_system="us",
)
