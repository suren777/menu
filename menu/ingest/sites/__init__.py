"""Site-specific configurations for the ingest pipeline."""

from menu.ingest.registry import SiteConfig
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

REGISTRY: dict[str, SiteConfig] = {
    BBC_GOOD_FOOD.name: BBC_GOOD_FOOD,
}


def registry() -> dict[str, SiteConfig]:
    """All registered sites, keyed by name."""
    return REGISTRY


__all__ = ["REGISTRY", "registry"]
