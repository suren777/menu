from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call, patch

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RetryError

from menu.domain.site import SiteConfig
from menu.ingest.errors import (
    BlockedError,
    DisallowedError,
    FetchError,
    NotCachedError,
)
from menu.ingest.fetch import _cache_path, _robots, fetch_page, fetch_recipe
from menu.ingest.http import http_session

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SITE = SiteConfig(
    name="test",
    base_url="https://test.com",
    sitemap_urls=("https://test.com/sitemap.xml",),
    politeness_delay=0.0,
)


@pytest.fixture(autouse=True)
def _fresh_robots_cache() -> Iterator[None]:
    """Isolate the module-level robots cache: every test fetches (and
    mocks) its own robots.txt."""
    _robots._parsers.clear()
    yield
    _robots._parsers.clear()


@patch.object(http_session(), "get")
def test_fetch_page_caches_to_disk(mock_get: MagicMock, tmp_path: Path) -> None:
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.content = b"<html>cached</html>"
    mock_get.return_value = mock_response

    first = fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    assert first == b"<html>cached</html>"

    cached = _cache_path("https://test.com/recipes/cake", tmp_path)
    assert cached.exists()
    assert cached.read_bytes() == b"<html>cached</html>"

    # Second call must be served from the cache, not the network.
    mock_get.reset_mock()
    second = fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    assert second == b"<html>cached</html>"
    mock_get.assert_not_called()


@patch.object(http_session(), "get")
def test_fetch_page_bypasses_cache_when_disabled(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.content = b"fresh"
    mock_get.return_value = mock_response

    result = fetch_page("https://test.com/x", SITE, cache_dir=tmp_path, use_cache=False)
    assert result == b"fresh"
    # The last of the two calls (robots.txt, then the page) is the page.
    mock_get.assert_called_with("https://test.com/x", timeout=10)


@patch.object(http_session(), "get")
def test_fetch_page_bypass_still_writes_the_cache(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    """A fresh fetch (sitemaps change) is still cached, so a later
    rebuild can replay the crawl."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.content = b"fresh"
    mock_get.return_value = mock_response

    fetch_page("https://test.com/x", SITE, cache_dir=tmp_path, use_cache=False)

    cached = _cache_path("https://test.com/x", tmp_path)
    assert cached.exists()
    assert cached.read_bytes() == b"fresh"


@patch("menu.ingest.fetch._politeness.wait")
@patch.object(http_session(), "get")
def test_fetch_recipe_parses_html(
    mock_get: MagicMock, mock_wait: MagicMock, tmp_path: Path
) -> None:
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.content = b"<html><body><h1>Test Recipe</h1></body></html>"
    mock_get.return_value = mock_response

    soup = fetch_recipe("https://test.com/recipe", SITE, cache_dir=tmp_path)
    assert "Test Recipe" in soup.get_text()
    mock_wait.assert_has_calls(
        [call(SITE.base_url, SITE.politeness_delay)], any_order=False
    )


@patch.object(http_session(), "get")
def test_fetch_page_raises_when_disallowed(mock_get: MagicMock, tmp_path: Path) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "User-agent: *\nDisallow: /"
    mock_get.return_value = mock_response

    with pytest.raises(DisallowedError, match=r"robots\.txt"):
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)


@patch.object(http_session(), "get")
def test_fetch_page_403_is_a_block(mock_get: MagicMock, tmp_path: Path) -> None:
    robots = MagicMock()
    robots.status_code = 200
    robots.text = ""
    blocked = MagicMock()
    blocked.status_code = 403
    mock_get.side_effect = [robots, blocked]

    with pytest.raises(BlockedError, match=r"HTTP 403") as excinfo:
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    assert excinfo.value.status_code == 403


@patch.object(http_session(), "get")
def test_fetch_page_404_is_a_fetch_error_with_status(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    """A dead link is a FetchError carrying the HTTP status, so the
    crawl ledger can record it."""
    robots = MagicMock()
    robots.status_code = 200
    robots.text = ""
    missing = MagicMock()
    missing.ok = False
    missing.status_code = 404
    mock_get.side_effect = [robots, missing]

    with pytest.raises(FetchError, match="Can't fetch") as excinfo:
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    assert excinfo.value.status_code == 404


@patch("menu.ingest.fetch.time.sleep")
@patch.object(http_session(), "get")
def test_fetch_page_429_waits_then_retries(
    mock_get: MagicMock, mock_sleep: MagicMock, tmp_path: Path
) -> None:
    robots = MagicMock()
    robots.status_code = 200
    robots.text = ""
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {}
    ok = MagicMock()
    ok.status_code = 200
    ok.content = b"page"
    mock_get.side_effect = [robots, limited, ok]

    result = fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)

    assert result == b"page"
    mock_sleep.assert_called_once()


@patch("menu.ingest.fetch.time.sleep")
@patch.object(http_session(), "get")
def test_fetch_page_429_twice_is_a_block(
    mock_get: MagicMock, mock_sleep: MagicMock, tmp_path: Path
) -> None:
    robots = MagicMock()
    robots.status_code = 200
    robots.text = ""
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {}
    still_limited = MagicMock()
    still_limited.status_code = 429
    mock_get.side_effect = [robots, limited, still_limited]

    with pytest.raises(BlockedError, match="rate-limited"):
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    mock_sleep.assert_called_once()


@patch.object(http_session(), "get")
def test_fetch_page_missing_robots_allows_all(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    """A 404 robots.txt really allows all: can_fetch is False until
    parse() has run, so the allow-all branch must parse an empty
    rule set."""
    robots = MagicMock()
    robots.status_code = 404
    page = MagicMock()
    page.ok = True
    page.status_code = 200
    page.content = b"page"
    mock_get.side_effect = [robots, page]

    result = fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    assert result == b"page"


@patch.object(http_session(), "get")
def test_fetch_page_unreachable_robots_allows_all(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    page = MagicMock()
    page.ok = True
    page.status_code = 200
    page.content = b"page"
    mock_get.side_effect = [RequestsConnectionError("unreachable"), page]

    result = fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    assert result == b"page"


@patch.object(http_session(), "get")
def test_fetch_page_robots_5xx_disallows_all(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    robots = MagicMock()
    robots.status_code = 503
    mock_get.return_value = robots

    with pytest.raises(DisallowedError, match=r"robots\.txt"):
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)


@patch.object(http_session(), "get")
def test_fetch_page_robots_retry_error_disallows_all(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    """The session's 5xx retries end in RetryError — still a 5xx (RFC
    9309), not the allow-all a connection error gets."""
    mock_get.side_effect = RetryError("5xx retries exhausted")

    with pytest.raises(DisallowedError, match=r"robots\.txt"):
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)


@patch("menu.ingest.fetch._politeness.wait")
@patch.object(http_session(), "get")
def test_fetch_page_uses_our_ua_crawl_delay(
    mock_get: MagicMock, mock_wait: MagicMock, tmp_path: Path
) -> None:
    """The crawl-delay comes from our user-agent's group, not "*" —
    Python matches UA groups on the product token, so 'menu' stands
    in for our full UA string."""
    robots = MagicMock()
    robots.status_code = 200
    robots.text = "User-agent: menu\nCrawl-delay: 7\n"
    page = MagicMock()
    page.ok = True
    page.status_code = 200
    page.content = b"page"
    mock_get.side_effect = [robots, page]

    fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
    mock_wait.assert_called_once_with(SITE.base_url, 7.0)


@patch("menu.ingest.fetch.time.sleep")
@patch.object(http_session(), "get")
def test_fetch_page_429_retry_after_is_capped(
    mock_get: MagicMock, mock_sleep: MagicMock, tmp_path: Path
) -> None:
    robots = MagicMock()
    robots.status_code = 200
    robots.text = ""
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "86400"}
    ok = MagicMock()
    ok.status_code = 200
    ok.content = b"page"
    mock_get.side_effect = [robots, limited, ok]

    result = fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)

    assert result == b"page"
    mock_sleep.assert_called_once_with(60.0)


# --- offline mode -----------------------------------------------------------


@patch.object(http_session(), "get", side_effect=RuntimeError("network"))
@patch("menu.ingest.fetch._politeness.wait")
@patch("menu.ingest.fetch._robots.parser")
def test_fetch_page_offline_uncached_raises_not_cached(
    mock_parser: MagicMock, mock_wait: MagicMock, mock_get: MagicMock, tmp_path: Path
) -> None:
    """Offline, an uncached URL raises NotCachedError without touching
    the network, robots.txt or the politeness delay."""
    with pytest.raises(NotCachedError, match="offline cache"):
        fetch_page(
            "https://test.com/recipes/cake", SITE, cache_dir=tmp_path, offline=True
        )
    mock_parser.assert_not_called()
    mock_wait.assert_not_called()
    mock_get.assert_not_called()


@patch.object(http_session(), "get")
def test_fetch_page_offline_serves_the_cache(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    """Offline a cached URL is served from the disk, never the network."""
    _cache_path("https://test.com/recipes/cake", tmp_path).write_bytes(
        b"<html>cached</html>"
    )

    result = fetch_page(
        "https://test.com/recipes/cake", SITE, cache_dir=tmp_path, offline=True
    )

    assert result == b"<html>cached</html>"
    mock_get.assert_not_called()
