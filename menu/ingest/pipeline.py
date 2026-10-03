"""Ingest pipeline: discover -> fetch -> extract -> scratch store.

Run a site end-to-end with:

    uv run python -m menu.ingest bbc_good_food

The local SQLite database is scratch storage for experiments; it is
never the source of truth. Code here graduates to food-guru's
backend/app/ingest/ with minimal rework.
"""

from argparse import ArgumentParser
from multiprocessing import Pool

from menu.db.connection import get_session
from menu.db.database import Sitemap, initialise
from menu.db.recipie_urls.actions import add_recipe
from menu.db.sitemap.actions import finalise_sitemap
from menu.db.sitemap.repository import SitemapRepository
from menu.ingest.discover import discover_urls, request_xml
from menu.ingest.extract import extract_recipe_data, looks_like_recipe
from menu.ingest.fetch import fetch_recipe
from menu.ingest.registry import SiteConfig, get_site


def import_sitemap(site: SiteConfig, sitemap_url: str | None = None) -> None:
    """Store the sitemap's URLs in the scratch database, ready to crawl."""
    urls = request_xml(sitemap_url or site.sitemap_url)

    with get_session() as session:
        for url in urls:
            if not SitemapRepository.url_exists(url, session):
                session.add(Sitemap(url=url))


def process_url(url: str, site: SiteConfig) -> None:
    """Fetch one recipe URL (cached, polite), extract its JSON-LD and
    store the raw data in the scratch database."""
    soup = fetch_recipe(url, site)
    if not looks_like_recipe(site, soup):
        return

    recipe_data = extract_recipe_data(site, soup)
    if recipe_data is None:
        return

    name = soup.title.text if soup.title else ""
    add_recipe(url, name, recipe_data)


def process_sitemap(url: str, site: SiteConfig) -> None:
    for recipe_url in discover_urls(site, url):
        process_url(recipe_url, site)
    finalise_sitemap(url)


def crawl_sitemap(site: SiteConfig, processes: int = 4) -> None:
    """Crawl every unfinished sitemap for a site."""
    with get_session() as session:
        urls = [s.url for s in SitemapRepository.get_unfinished(session)]

    with Pool(processes=processes) as pool:
        pool.starmap(process_sitemap, [(url, site) for url in urls])


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
