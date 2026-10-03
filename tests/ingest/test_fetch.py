from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call, patch

import pytest

from menu.ingest.errors import DisallowedError
from menu.ingest.fetch import _cache_path, _robots, fetch_page, fetch_recipe
from menu.ingest.registry import SiteConfig

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


@patch("menu.ingest.fetch.session.get")
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


@patch("menu.ingest.fetch.session.get")
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


@patch("menu.ingest.fetch._politeness.wait")
@patch("menu.ingest.fetch.session.get")
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


@patch("menu.ingest.fetch.session.get")
def test_fetch_page_raises_when_disallowed(
    mock_get: MagicMock, tmp_path: Path
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "User-agent: *\nDisallow: /"
    mock_get.return_value = mock_response

    with pytest.raises(DisallowedError, match=r"robots\.txt"):
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)


@patch("menu.ingest.fetch.session.get")
def test_robots_5xx_disallows_all(mock_get: MagicMock, tmp_path: Path) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_get.return_value = mock_response

    with pytest.raises(DisallowedError, match=r"robots\.txt"):
        fetch_page("https://test.com/recipes/cake", SITE, cache_dir=tmp_path)
