"""Discovery: find candidate recipe URLs per site via sitemaps."""

import re
from typing import TYPE_CHECKING

from defusedxml.ElementTree import fromstring

from menu.ingest.errors import FetchError
from menu.ingest.http import session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from menu.ingest.registry import SiteConfig


def request_xml(url: str) -> tuple[str, list[str]]:
    """Fetch a sitemap and return its root kind ("sitemapindex" or
    "urlset") with the URLs listed in it, matching food-guru's
    _urls_from_xml."""
    response = session.get(url, timeout=10)
    if not response.ok:
        raise FetchError(f"Can't fetch {url!r}")

    tree = fromstring(response.content)
    kind = tree.tag.rsplit("}", 1)[-1]
    locs = [
        child.text
        for child in tree.iter()
        if child.tag.endswith("loc") and child.text is not None
    ]
    return kind, locs


def discover_urls(site: SiteConfig, sitemap_url: str | None = None) -> Iterator[str]:
    """Yield candidate recipe URLs for a site, honouring its URL pattern."""
    pattern = re.compile(site.url_pattern) if site.url_pattern else None
    sitemaps = [sitemap_url] if sitemap_url else site.sitemap_urls
    for sitemap in sitemaps:
        _, locs = request_xml(sitemap)
        for url in locs:
            if pattern is None or pattern.match(url):
                yield url
