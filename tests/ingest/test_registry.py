import pytest
from pydantic import ValidationError

from menu.ingest.registry import SiteConfig, get_site
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD


def test_bbc_good_food_is_registered() -> None:
    assert get_site("bbc_good_food") == BBC_GOOD_FOOD


def test_unknown_site_raises() -> None:
    with pytest.raises(KeyError, match="does_not_exist"):
        get_site("does_not_exist")


def test_site_config_is_frozen() -> None:
    with pytest.raises(ValidationError, match="frozen"):
        BBC_GOOD_FOOD.name = "other"


def test_site_config_defaults() -> None:
    site = SiteConfig(
        name="x", base_url="https://x.com", sitemap_urls=("https://x.com/s",)
    )
    assert site.politeness_delay == 1.0
    assert site.url_pattern is None
    assert site.json_ld_test_id is None
