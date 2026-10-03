"""Discovery: find candidate recipe URLs per site via sitemaps."""

import re
from collections.abc import Iterator
from xml.etree import ElementTree

import requests

from menu.ingest.errors import FetchError
from menu.ingest.registry import SiteConfig

SITEMAP_NAMESPACE = {"sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def request_xml(url: str) -> list[str]:
    """Fetch a sitemap and return the URLs listed in it.

    Handles both plain urlsets (<url><loc>) and sitemap indexes
    (<sitemap><loc>), matching food-guru's _urls_from_xml.
    """
    response = requests.get(url, timeout=10)
    if not response.ok:
        raise FetchError(f"Can't fetch {url!r}")

    tree = ElementTree.fromstring(response.content)
    return [
        child.text
        for child in tree.iter()
        if child.tag.endswith("loc") and child.text is not None
    ]


def discover_urls(site: SiteConfig, sitemap_url: str | None = None) -> Iterator[str]:
    """Yield candidate recipe URLs for a site, honouring its URL pattern."""
    pattern = re.compile(site.url_pattern) if site.url_pattern else None
    for url in request_xml(sitemap_url or site.sitemap_url):
        if pattern is None or pattern.match(url):
            yield url
