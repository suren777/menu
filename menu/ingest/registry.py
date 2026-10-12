"""Site registry: adding a site to menu is mostly config."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from menu.domain.site import SiteConfig


def get_site(name: str) -> SiteConfig:
    """Look up a site config by its registry name."""
    from menu.ingest import sites

    registry = sites.registry()
    if name not in registry:
        known = ", ".join(sorted(registry))
        raise KeyError(f"Unknown site {name!r}. Known sites: {known}")
    return registry[name]
