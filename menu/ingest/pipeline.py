"""Ingest pipeline: discover -> fetch -> extract -> scratch store.

Run a site end-to-end with:

    uv run python -m menu.ingest bbc_good_food

The local SQLite database is scratch storage for experiments; it is
never the source of truth. Code here graduates to food-guru's
backend/app/ingest/ with minimal rework.
"""

from argparse import ArgumentParser

from menu.db.connection import get_session
from menu.db.database import Sitemap, initialise
from menu.db.recipe_urls.actions import add_recipe
from menu.db.sitemap.actions import finalise_sitemap
from menu.db.sitemap.repository import SitemapRepository
from menu.ingest.discover import discover_urls, request_xml
from menu.ingest.extract import extract_recipe_data
from menu.ingest.fetch import fetch_recipe
from menu.ingest.registry import SiteConfig, get_site


def import_sitemap(site: SiteConfig, sitemap_url: str | None = None) -> None:
    """Store the sitemap's URLs in the scratch database, ready to crawl."""
    urls = request_xml(sitemap_url or site.sitemap_url)

    with get_session() as session:
        for url in urls:
            if not SitemapRepository.url_exists(url, site.name, session):
                session.add(Sitemap(url=url, site=site.name))


def process_url(url: str, site: SiteConfig) -> None:
    """Fetch one recipe URL (cached, polite) and store its raw JSON-LD
    in the scratch database."""
    soup = fetch_recipe(url, site)

    recipe_data = extract_recipe_data(site, soup)
    if recipe_data is None:
        return

    name = soup.title.text if soup.title else ""
    add_recipe(url, name, recipe_data)


def process_sitemap(url: str, site: SiteConfig) -> None:
    for recipe_url in discover_urls(site, url):
        process_url(recipe_url, site)
    finalise_sitemap(url, site.name)


def crawl_sitemap(site: SiteConfig) -> None:
    """Crawl every unfinished sitemap for a site.

    Single process on purpose: the per-host politeness delay lives in
    process memory, so parallel workers would each hammer the site at
    full speed (food-guru's worker is single-process for the same
    reason).
    """
    with get_session() as session:
        urls = [
            s.url for s in SitemapRepository.get_unfinished(session, site.name)
        ]

    for url in urls:
        process_sitemap(url, site)


def main() -> None:
    parser = ArgumentParser(description="Ingest recipes for a registered site.")
    parser.add_argument("site", help="site name from the registry")
    args = parser.parse_args()

    site = get_site(args.site)
    initialise()
    import_sitemap(site)
    crawl_sitemap(site)


if __name__ == "__main__":
    main()
