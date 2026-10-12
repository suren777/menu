"""Discovery tests: request_xml parses sitemap XML into entries.

The pattern filtering the old discover_urls did now happens when the
crawl queues pages (_walk_sitemap), so those tests live in
test_pipeline.py.
"""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from menu.ingest.discover import SitemapEntry, request_xml
from menu.ingest.errors import FetchError, NotCachedError
from menu.ingest.fetch import _cache_path
from menu.ingest.http import http_session
from menu.ingest.registry import SiteConfig
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

if TYPE_CHECKING:
    from pathlib import Path

SITE = SiteConfig(
    name="test",
    base_url="https://test.com",
    sitemap_urls=("https://test.com/sitemap.xml",),
)


@patch("menu.ingest.discover.fetch_page")
def test_request_xml_urlset(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = (
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://test.com/recipes/a</loc>"
        b"<lastmod>2026-10-01</lastmod></url></urlset>"
    )

    assert request_xml("https://test.com/sitemap.xml", SITE) == (
        "urlset",
        [SitemapEntry(loc="https://test.com/recipes/a", lastmod="2026-10-01")],
    )


@patch("menu.ingest.discover.fetch_page")
def test_request_xml_sitemap_index(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = (
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://test.com/recipes-1.xml</loc></sitemap>"
        b"<sitemap><loc>https://test.com/recipes-2.xml</loc>"
        b"<lastmod>2026-10-02</lastmod></sitemap>"
        b"</sitemapindex>"
    )

    assert request_xml("https://test.com/sitemap.xml", SITE) == (
        "sitemapindex",
        [
            SitemapEntry(loc="https://test.com/recipes-1.xml"),
            SitemapEntry(loc="https://test.com/recipes-2.xml", lastmod="2026-10-02"),
        ],
    )


@patch("menu.ingest.discover.fetch_page")
def test_request_xml_fetches_fresh_but_cached(mock_fetch: MagicMock) -> None:
    """Sitemaps change, so a live crawl fetches fresh — through the
    cache-writing path, so a later rebuild can replay the crawl."""
    mock_fetch.return_value = b"<urlset></urlset>"

    request_xml("https://test.com/sitemap.xml", SITE)

    mock_fetch.assert_called_once_with(
        "https://test.com/sitemap.xml", SITE, use_cache=False, offline=False
    )


@patch("menu.ingest.discover.fetch_page")
def test_request_xml_fail(mock_fetch: MagicMock) -> None:
    mock_fetch.side_effect = FetchError("Can't fetch '...'", status_code=404)

    with pytest.raises(FetchError, match="Can't fetch"):
        request_xml("https://test.com/sitemap.xml", SITE)


# --- offline mode -----------------------------------------------------------


def test_request_xml_offline_replays_the_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Offline reads the cached sitemap (fetched fresh live, but
    cached) — no network, robots or politeness."""
    monkeypatch.setattr("menu.ingest.fetch.DEFAULT_CACHE_DIR", tmp_path)
    _cache_path("https://test.com/sitemap.xml", tmp_path).write_bytes(
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://test.com/recipes/a</loc></url></urlset>"
    )

    with patch.object(http_session(), "get", side_effect=RuntimeError("network")):
        kind, entries = request_xml("https://test.com/sitemap.xml", SITE, offline=True)

    assert kind == "urlset"
    assert entries == [SitemapEntry(loc="https://test.com/recipes/a")]


def test_request_xml_offline_uncached_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("menu.ingest.fetch.DEFAULT_CACHE_DIR", tmp_path)

    with (
        patch.object(http_session(), "get", side_effect=RuntimeError("network")),
        pytest.raises(NotCachedError, match="offline cache"),
    ):
        request_xml("https://test.com/sitemap.xml", SITE, offline=True)


def test_bbc_site_config() -> None:
    assert BBC_GOOD_FOOD.sitemap_urls == ("https://www.bbcgoodfood.com/sitemap.xml",)
    assert BBC_GOOD_FOOD.json_ld_test_id == "page-schema"
