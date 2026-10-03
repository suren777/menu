"""Fetch pages with a disk cache and per-site politeness delays.

Repeat runs read from the cache instead of hitting the sites again;
the cache lives under .cache/ (gitignored) and is safe to delete.
"""

import hashlib
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from menu.ingest.errors import FetchError
from menu.ingest.registry import SiteConfig

DEFAULT_CACHE_DIR = Path(".cache")


class _Politeness:
    """Tracks the last request time per host so we can space out requests."""

    def __init__(self) -> None:
        self._last_request: dict[str, float] = {}

    def wait(self, host: str, delay: float) -> None:
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request[host] = time.monotonic()


_politeness = _Politeness()


def _cache_path(url: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return cache_dir / f"{digest}.html"


def fetch_page(
    url: str,
    site: SiteConfig,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
) -> bytes:
    """Fetch a page's raw bytes, from the disk cache when possible."""
    path = _cache_path(url, cache_dir)
    if use_cache and path.exists():
        return path.read_bytes()

    _politeness.wait(site.base_url, site.politeness_delay)
    response = requests.get(url, timeout=10)
    if not response.ok:
        raise FetchError(f"Can't fetch {url!r}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return response.content


def fetch_recipe(
    url: str, site: SiteConfig, cache_dir: Path = DEFAULT_CACHE_DIR
) -> BeautifulSoup:
    """Fetch a page and parse it into a BeautifulSoup document."""
    return BeautifulSoup(fetch_page(url, site, cache_dir), "html.parser")
