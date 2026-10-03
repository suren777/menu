from unittest.mock import MagicMock, patch

from menu.crawlers.bbc_good_food.crawler import (
    crawl_sitemap,
    import_recipes,
    import_sitemap,
)


@patch("menu.crawlers.bbc_good_food.crawler.get_sitemap")
@patch("menu.crawlers.bbc_good_food.crawler.get_session")
@patch("menu.crawlers.bbc_good_food.crawler.SitemapRepository")
def test_import_sitemap(
    mock_sitemap_repo: MagicMock,
    mock_get_session: MagicMock,
    mock_get_sitemap: MagicMock,
) -> None:
    mock_get_sitemap.return_value = ["http://test.com/sitemap1"]
    mock_sitemap_repo.url_exists.return_value = False
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    import_sitemap()

    mock_get_sitemap.assert_called_once()
    mock_sitemap_repo.url_exists.assert_called_once_with(
        "http://test.com/sitemap1", mock_session
    )
    mock_session.add.assert_called_once()


@patch("menu.crawlers.bbc_good_food.crawler.request_xml")
@patch("menu.crawlers.bbc_good_food.crawler.fetch_recipe")
@patch("menu.crawlers.bbc_good_food.crawler.contains_recipe")
@patch("menu.crawlers.bbc_good_food.crawler.add_recipe")
@patch("menu.crawlers.bbc_good_food.crawler.finalise_sitemap")
def test_import_recipes(
    mock_finalise: MagicMock,
    mock_add_recipe: MagicMock,
    mock_contains: MagicMock,
    mock_fetch: MagicMock,
    mock_request_xml: MagicMock,
) -> None:
    mock_request_xml.return_value = ["http://test.com/recipe1"]
    mock_recipe = MagicMock()
    mock_recipe.find.return_value.contents = ['{"key": "value"}']
    mock_recipe.title.text = "Test Recipe"
    mock_fetch.return_value = mock_recipe
    mock_contains.return_value = True

    import_recipes("http://test.com/sitemap1")

    mock_request_xml.assert_called_once_with("http://test.com/sitemap1")
    mock_fetch.assert_called_once_with("http://test.com/recipe1")
    mock_contains.assert_called_once_with(mock_recipe)
    mock_add_recipe.assert_called_once_with(
        "http://test.com/recipe1", "Test Recipe", {"key": "value"}
    )
    mock_finalise.assert_called_once_with("http://test.com/sitemap1")


@patch("menu.crawlers.bbc_good_food.crawler.get_session")
@patch("menu.crawlers.bbc_good_food.crawler.SitemapRepository")
@patch("menu.crawlers.bbc_good_food.crawler.Pool")
def test_crawl_sitemap(
    mock_pool: MagicMock, mock_sitemap_repo: MagicMock, mock_get_session: MagicMock
) -> None:
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    sitemap_entries = [
        MagicMock(url="http://test.com/sitemap1"),
        MagicMock(url="http://test.com/sitemap2"),
    ]
    mock_sitemap_repo.get_unfinished.return_value = sitemap_entries
    mock_pool_instance = mock_pool.return_value

    crawl_sitemap()

    mock_sitemap_repo.get_unfinished.assert_called_once_with(mock_session)
    mock_pool_instance.map.assert_called_once()

    # Check what map was called with
    args, _kwargs = mock_pool_instance.map.call_args
    assert args[0] == import_recipes
    assert list(args[1]) == ["http://test.com/sitemap1", "http://test.com/sitemap2"]
