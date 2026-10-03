from unittest.mock import MagicMock, patch

from menu.ingest.pipeline import (
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
        "https://test.com/sitemap1", mock_session
    )
    mock_session.add.assert_called_once()


@patch("menu.ingest.pipeline.add_recipe")
@patch("menu.ingest.pipeline.extract_recipe_data")
@patch("menu.ingest.pipeline.looks_like_recipe")
@patch("menu.ingest.pipeline.fetch_recipe")
def test_process_url_stores_recipe(
    mock_fetch: MagicMock,
    mock_looks: MagicMock,
    mock_extract: MagicMock,
    mock_add_recipe: MagicMock,
) -> None:
    mock_looks.return_value = True
    mock_extract.return_value = {"@type": "Recipe", "name": "Cake"}

    process_url("https://test.com/recipes/cake", SITE)

    mock_add_recipe.assert_called_once()
    args = mock_add_recipe.call_args.args
    assert args[0] == "https://test.com/recipes/cake"
    assert args[2] == {"@type": "Recipe", "name": "Cake"}


@patch("menu.ingest.pipeline.add_recipe")
@patch("menu.ingest.pipeline.extract_recipe_data")
@patch("menu.ingest.pipeline.looks_like_recipe")
@patch("menu.ingest.pipeline.fetch_recipe")
def test_process_url_skips_non_recipes(
    mock_fetch: MagicMock,
    mock_looks: MagicMock,
    mock_extract: MagicMock,
    mock_add_recipe: MagicMock,
) -> None:
    mock_looks.return_value = False

    process_url("https://test.com/about", SITE)

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

    process_sitemap("https://test.com/sitemap1", SITE)

    assert mock_process_url.call_count == 2
    mock_finalise.assert_called_once_with("https://test.com/sitemap1")


@patch("menu.ingest.pipeline.Pool")
@patch("menu.ingest.pipeline.get_session")
@patch("menu.ingest.pipeline.SitemapRepository")
def test_crawl_sitemap(
    mock_sitemap_repo: MagicMock,
    mock_get_session: MagicMock,
    mock_pool: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_sitemap_repo.get_unfinished.return_value = [
        MagicMock(url="https://test.com/sitemap1"),
        MagicMock(url="https://test.com/sitemap2"),
    ]

    crawl_sitemap(SITE)

    mock_sitemap_repo.get_unfinished.assert_called_once_with(mock_session)
    pool_instance = mock_pool.return_value.__enter__.return_value
    pool_instance.starmap.assert_called_once_with(
        process_sitemap,
        [
            ("https://test.com/sitemap1", SITE),
            ("https://test.com/sitemap2", SITE),
        ],
    )


def test_bbc_site_registered() -> None:
    from menu.ingest.registry import get_site

    assert get_site("bbc_good_food").name == BBC_GOOD_FOOD.name
