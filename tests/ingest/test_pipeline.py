from unittest.mock import MagicMock, patch

import pytest

from menu.ingest.errors import FetchError
from menu.ingest.pipeline import (
    CrawlReport,
    crawl_sitemap,
    import_sitemap,
    process_sitemap,
    process_url,
)
from menu.ingest.registry import SiteConfig
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

SITE = SiteConfig(
    name="test",
    base_url="https://test.com",
    sitemap_url="https://test.com/sitemap.xml",
)


@patch("menu.ingest.pipeline.SitemapRepository")
@patch("menu.ingest.pipeline.get_session")
@patch("menu.ingest.pipeline.request_xml")
def test_import_sitemap(
    mock_request_xml: MagicMock,
    mock_get_session: MagicMock,
    mock_sitemap_repo: MagicMock,
) -> None:
    mock_request_xml.return_value = ["https://test.com/sitemap1"]
    mock_sitemap_repo.url_exists.return_value = False
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    import_sitemap(SITE)

    mock_sitemap_repo.url_exists.assert_called_once_with(
        "https://test.com/sitemap1", SITE.name, mock_session
    )
    mock_session.add.assert_called_once()


@patch("menu.ingest.pipeline.add_recipe")
@patch("menu.ingest.pipeline.extract_recipe_data")
@patch("menu.ingest.pipeline.fetch_recipe")
def test_process_url_stores_recipe(
    _mock_fetch: MagicMock,
    mock_extract: MagicMock,
    mock_add_recipe: MagicMock,
) -> None:
    mock_extract.return_value = {"@type": "Recipe", "name": "Cake"}

    stored = process_url("https://test.com/recipes/cake", SITE)

    assert stored is True
    mock_add_recipe.assert_called_once()
    args = mock_add_recipe.call_args.args
    assert args[0] == "https://test.com/recipes/cake"
    assert args[2] == {"@type": "Recipe", "name": "Cake"}


@patch("menu.ingest.pipeline.add_recipe")
@patch("menu.ingest.pipeline.extract_recipe_data")
@patch("menu.ingest.pipeline.fetch_recipe")
def test_process_url_skips_non_recipes(
    _mock_fetch: MagicMock,
    mock_extract: MagicMock,
    mock_add_recipe: MagicMock,
) -> None:
    mock_extract.return_value = None

    stored = process_url("https://test.com/about", SITE)

    assert stored is False
    mock_add_recipe.assert_not_called()


@patch("menu.ingest.pipeline.finalise_sitemap")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.discover_urls")
def test_process_sitemap(
    mock_discover: MagicMock,
    mock_process_url: MagicMock,
    mock_finalise: MagicMock,
) -> None:
    mock_discover.return_value = ["https://test.com/recipes/a", "https://test.com/b"]
    mock_process_url.return_value = True

    report = process_sitemap("https://test.com/sitemap1", SITE)

    assert mock_process_url.call_count == 2
    assert report == CrawlReport(fetched=2, stored=2)
    mock_finalise.assert_called_once_with("https://test.com/sitemap1", SITE.name)


@patch("menu.ingest.pipeline.finalise_sitemap")
@patch("menu.ingest.pipeline.process_url")
@patch("menu.ingest.pipeline.discover_urls")
def test_process_sitemap_skips_bad_urls(
    mock_discover: MagicMock,
    mock_process_url: MagicMock,
    mock_finalise: MagicMock,
) -> None:
    mock_discover.return_value = [
        "https://test.com/recipes/a",
        "https://test.com/dead-link",
        "https://test.com/recipes/c",
    ]
    mock_process_url.side_effect = [True, FetchError("Can't fetch"), True]

    report = process_sitemap("https://test.com/sitemap1", SITE)

    assert report == CrawlReport(fetched=3, stored=2, failed=1)
    mock_finalise.assert_called_once_with("https://test.com/sitemap1", SITE.name)


@patch("menu.ingest.pipeline.process_sitemap")
@patch("menu.ingest.pipeline.get_session")
@patch("menu.ingest.pipeline.SitemapRepository")
def test_crawl_sitemap(
    mock_sitemap_repo: MagicMock,
    mock_get_session: MagicMock,
    mock_process_sitemap: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_sitemap_repo.get_unfinished.return_value = [
        MagicMock(url="https://test.com/sitemap1"),
        MagicMock(url="https://test.com/sitemap2"),
    ]
    mock_process_sitemap.side_effect = [
        CrawlReport(fetched=1, stored=1),
        CrawlReport(fetched=2, stored=2),
    ]

    report = crawl_sitemap(SITE)

    mock_sitemap_repo.get_unfinished.assert_called_once_with(mock_session, SITE.name)
    assert mock_process_sitemap.call_count == 2
    mock_process_sitemap.assert_any_call("https://test.com/sitemap1", SITE)
    mock_process_sitemap.assert_any_call("https://test.com/sitemap2", SITE)
    assert report == CrawlReport(fetched=3, stored=3)


@patch("menu.ingest.pipeline.finalise_sitemap")
@patch("menu.ingest.pipeline.process_sitemap")
@patch("menu.ingest.pipeline.get_session")
@patch("menu.ingest.pipeline.SitemapRepository")
def test_crawl_sitemap_skips_dead_sitemap(
    mock_sitemap_repo: MagicMock,
    mock_get_session: MagicMock,
    mock_process_sitemap: MagicMock,
    mock_finalise: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_sitemap_repo.get_unfinished.return_value = [
        MagicMock(url="https://test.com/sitemap1"),
        MagicMock(url="https://test.com/sitemap2"),
        MagicMock(url="https://test.com/sitemap3"),
    ]
    mock_process_sitemap.side_effect = [
        CrawlReport(fetched=2, stored=2),
        FetchError("Can't fetch sitemap2"),
        CrawlReport(fetched=1, stored=1),
    ]

    report = crawl_sitemap(SITE)

    assert mock_process_sitemap.call_count == 3
    mock_finalise.assert_called_once_with("https://test.com/sitemap2", SITE.name)
    assert report == CrawlReport(fetched=3, stored=3, failed=1)
    assert "3 fetched, 3 stored, 1 failed" in capsys.readouterr().out


def test_bbc_site_registered() -> None:
    from menu.ingest.registry import get_site

    assert get_site("bbc_good_food").name == BBC_GOOD_FOOD.name
