"""Site-specific configurations for the ingest pipeline."""

from typing import TYPE_CHECKING

from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD
from menu.ingest.sites.king_arthur import KING_ARTHUR
from menu.ingest.sites.ottolenghi import OTTOLENGHI

if TYPE_CHECKING:
    from menu.ingest.registry import SiteConfig

REGISTRY: dict[str, SiteConfig] = {
    BBC_GOOD_FOOD.name: BBC_GOOD_FOOD,
    KING_ARTHUR.name: KING_ARTHUR,
    OTTOLENGHI.name: OTTOLENGHI,
}


def registry() -> dict[str, SiteConfig]:
    """All registered sites, keyed by name."""
    return REGISTRY


__all__ = ["REGISTRY", "registry"]
