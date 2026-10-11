"""Discovery: sitemap XML through the page fetch path.

`request_xml` fetches one sitemap document — robots, politeness and
the disk cache all apply, exactly like a page — and returns its root
kind with the URLs listed in it, each with the lastmod the sitemap
gives. Live crawls fetch sitemaps fresh (they change) but cache them,
so a later rebuild can replay the crawl.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from defusedxml.ElementTree import fromstring

from menu.ingest.fetch import fetch_page

if TYPE_CHECKING:
    from menu.ingest.registry import SiteConfig


@dataclass
class SitemapEntry:
    """One <url> or <sitemap> element of a sitemap document."""

    loc: str
    lastmod: str | None = None


def request_xml(url: str, site: SiteConfig) -> tuple[str, list[SitemapEntry]]:
    """Fetch a sitemap and return its root kind ("sitemapindex" or
    "urlset") with the entries listed in it, matching food-guru's
    _urls_from_xml. The fetch is a normal page fetch: robots.txt,
    politeness and the disk cache apply."""
    content = fetch_page(url, site, use_cache=False)
    tree = fromstring(content)
    kind = tree.tag.rsplit("}", 1)[-1]
    entries: list[SitemapEntry] = []
    for child in tree:
        loc = lastmod = None
        for sub in child.iter():
            tag = sub.tag.rsplit("}", 1)[-1]
            if tag == "loc" and sub.text and loc is None:
                loc = sub.text
            elif tag == "lastmod" and sub.text and lastmod is None:
                lastmod = sub.text
        if loc is not None:
            entries.append(SitemapEntry(loc=loc, lastmod=lastmod))
    return kind, entries
