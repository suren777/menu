"""Fetch pages with a disk cache, robots.txt and politeness delays.

Repeat runs read from the cache instead of hitting the sites again;
the cache lives in .cache/ at the project root (gitignored) and is
safe to delete. robots.txt is honoured per site: a disallowed URL
raises DisallowedError, and the robots crawl-delay is respected on
top of the site's own politeness delay.
"""

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from requests.exceptions import RequestException, RetryError

from menu.ingest.errors import BlockedError, DisallowedError, FetchError
from menu.ingest.http import USER_AGENT, session

if TYPE_CHECKING:
    from requests import Response

    from menu.ingest.registry import SiteConfig

# Anchored to the project root so the cache doesn't depend on the
# directory the crawl is started from.
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"


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


class _Robots:
    """robots.txt per site base URL, fetched once through the shared
    session and cached — single process on purpose, like _politeness.

    A 4xx (no robots.txt) allows all and a 5xx disallows all (RFC
    9309) — the session's RetryError after exhausting 5xx retries
    counts as a 5xx; a connection error allows all, since the per-URL
    fetch surfaces a real block itself."""

    def __init__(self) -> None:
        self._parsers: dict[str, RobotFileParser] = {}

    def parser(self, base_url: str) -> RobotFileParser:
        if base_url not in self._parsers:
            self._parsers[base_url] = self._fetch(base_url)
        return self._parsers[base_url]

    def _fetch(self, base_url: str) -> RobotFileParser:
        parser = RobotFileParser()
        robots_url = f"{base_url.rstrip('/')}/robots.txt"
        try:
            response = session.get(robots_url, timeout=10)
        except RetryError:
            # The session's retries ran out on 5xx: unobtainable
            # robots.txt (RFC 9309) disallows everything.
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser
        except RequestException:
            parser.parse([])  # no rules parsed: allows everything
            return parser

        if response.status_code >= 500:
            # Unobtainable robots.txt (RFC 9309): disallow everything.
            parser.parse(["User-agent: *", "Disallow: /"])
        elif response.status_code >= 400:
            parser.parse([])  # No robots.txt: allows everything.
        else:
            parser.parse(response.text.splitlines())
        return parser


_robots = _Robots()


def check_robots(url: str, site: SiteConfig) -> None:
    """Raise DisallowedError when robots.txt disallows url to our UA."""
    parser = _robots.parser(site.base_url)
    if not parser.can_fetch(USER_AGENT, url):
        raise DisallowedError(f"robots.txt disallows {url!r}")


def _cache_path(url: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return cache_dir / f"{digest}.html"


def _retry_after(response: Response) -> float:
    """Seconds to wait after a 429: a numeric Retry-After capped at a
    minute (a day-long Retry-After must not stall the crawl), else 5."""
    try:
        return min(max(float(response.headers["Retry-After"]), 1.0), 60.0)
    except (KeyError, TypeError, ValueError):
        return 5.0


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

    check_robots(url, site)
    parser = _robots.parser(site.base_url)
    # The site's own delay or the robots crawl-delay, whichever is
    # larger.
    try:
        delay = max(
            site.politeness_delay, float(parser.crawl_delay(USER_AGENT) or 0.0)
        )
    except ValueError:  # a garbage crawl-delay: fall back to our own
        delay = site.politeness_delay
    _politeness.wait(site.base_url, delay)
    response = session.get(url, timeout=10)
    if response.status_code == 429:
        # Rate-limited: wait out the site's own pacing once, then a
        # second 429 is a block.
        time.sleep(_retry_after(response))
        response = session.get(url, timeout=10)
        if response.status_code == 429:
            raise BlockedError(f"Still rate-limited: {url!r}")
    if response.status_code in (401, 402, 403):
        raise BlockedError(f"Blocked with HTTP {response.status_code}: {url!r}")
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
