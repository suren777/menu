from menu.ingest.registry import SiteConfig, get_site
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD


def test_bbc_good_food_is_registered() -> None:
    assert get_site("bbc_good_food") == BBC_GOOD_FOOD


def test_unknown_site_raises() -> None:
    try:
        get_site("does_not_exist")
    except KeyError as err:
        assert "does_not_exist" in str(err)
    else:
        raise AssertionError("expected KeyError")


def test_site_config_is_frozen() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BBC_GOOD_FOOD.name = "other"


def test_site_config_defaults() -> None:
    site = SiteConfig(name="x", base_url="https://x.com", sitemap_url="https://x.com/s")
    assert site.politeness_delay == 1.0
    assert site.url_pattern is None
    assert site.json_ld_test_id is None
