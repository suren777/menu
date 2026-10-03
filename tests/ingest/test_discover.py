from unittest.mock import MagicMock, patch

from menu.ingest.discover import discover_urls, request_xml
from menu.ingest.errors import FetchError
from menu.ingest.registry import SiteConfig
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

SITE = SiteConfig(
    name="test",
    base_url="https://test.com",
    sitemap_url="https://test.com/sitemap.xml",
)


@patch("menu.ingest.discover.requests.get")
def test_request_xml_ok(mock_get: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.content = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>http://test.com</loc></url></urlset>'
    mock_get.return_value = mock_response

    assert request_xml("https://test.com/sitemap.xml") == ["http://test.com"]


@patch("menu.ingest.discover.requests.get")
def test_request_xml_sitemap_index(mock_get: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.content = (
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://test.com/recipes-1.xml</loc></sitemap>"
        b"<sitemap><loc>https://test.com/recipes-2.xml</loc></sitemap>"
        b"</sitemapindex>"
    )
    mock_get.return_value = mock_response

    assert request_xml("https://test.com/sitemap.xml") == [
        "https://test.com/recipes-1.xml",
        "https://test.com/recipes-2.xml",
    ]


@patch("menu.ingest.discover.requests.get")
def test_request_xml_fail(mock_get: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.ok = False
    mock_get.return_value = mock_response

    try:
        request_xml("https://test.com/sitemap.xml")
    except FetchError:
        pass
    else:
        raise AssertionError("expected FetchError")


@patch("menu.ingest.discover.requests.get")
def test_discover_urls_filters_by_pattern(mock_get: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.content = (
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://test.com/recipes/cake</loc></url>"
        b"<url><loc>https://test.com/recipes/cake/step-2</loc></url>"
        b"<url><loc>https://test.com/about</loc></url>"
        b"</urlset>"
    )
    mock_get.return_value = mock_response

    site = SITE.model_copy(update={"url_pattern": r"^https://test\.com/recipes/[^/]+$"})
    assert list(discover_urls(site)) == ["https://test.com/recipes/cake"]


@patch("menu.ingest.discover.requests.get")
def test_discover_urls_without_pattern_yields_all(mock_get: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.content = (
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://test.com/a</loc></url>"
        b"<url><loc>https://test.com/b</loc></url>"
        b"</urlset>"
    )
    mock_get.return_value = mock_response

    assert list(discover_urls(SITE)) == ["https://test.com/a", "https://test.com/b"]


def test_bbc_site_config() -> None:
    assert BBC_GOOD_FOOD.sitemap_url == "https://www.bbcgoodfood.com/sitemap.xml"
    assert BBC_GOOD_FOOD.json_ld_test_id == "page-schema"
