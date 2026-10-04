"""Ingest pipeline: discover -> fetch -> extract -> scratch store.

Run a site end-to-end with:

    uv run python -m menu.ingest bbc_good_food

The local SQLite database is scratch storage for experiments; it is
never the source of truth. Code here graduates to food-guru's
backend/app/ingest/ with minimal rework.
"""

import logging
from argparse import ArgumentParser
from dataclasses import dataclass

from sqlalchemy import or_, select

from menu.db.connection import get_session
from menu.db.database import RecipeUrls, Sitemap, initialise
from menu.db.ingredients.actions import (
    fdc_id_conflicts,
    prune_orphan_ingredients,
    ref_review,
    store_recipe_ingredients,
)
from menu.db.ingredients.seed import seed_ingredient_data
from menu.db.recipe_urls.actions import add_recipe
from menu.db.sitemap.actions import finalise_sitemap
from menu.db.sitemap.repository import get_unfinished, url_exists
from menu.ingest.discover import discover_urls, request_xml
from menu.ingest.errors import BlockedError, CrawlBlockedError
from menu.ingest.extract import extract_recipe_data
from menu.ingest.fetch import fetch_recipe
from menu.ingest.registry import SiteConfig, get_site

logger = logging.getLogger(__name__)

# Consecutive blocked URLs before the crawl gives up on the site.
_BLOCK_LIMIT = 5


@dataclass
class CrawlReport:
    """Counts for a crawl run, mirroring food-guru's IngestReport."""

    fetched: int = 0
    stored: int = 0
    failed: int = 0

    def merge(self, other: CrawlReport) -> None:
        self.fetched += other.fetched
        self.stored += other.stored
        self.failed += other.failed


def import_sitemap(site: SiteConfig, sitemap_url: str | None = None) -> None:
    """Store the sitemap's URLs in the scratch database, ready to crawl.

    A sitemap index stores its children — the sub-sitemaps to crawl.
    A plain urlset stores the sitemap URL itself as the single row, so
    process_sitemap reads its page URLs as normal.
    """
    urls = [sitemap_url] if sitemap_url else list(site.sitemap_urls)

    with get_session() as session:
        for url in urls:
            kind, locs = request_xml(url, site)
            if kind == "urlset":
                locs = [url]
            for loc in locs:
                if not url_exists(loc, site.name, session):
                    session.add(Sitemap(url=loc, site=site.name))


def process_url(url: str, site: SiteConfig) -> bool:
    """Fetch one recipe URL (cached, polite) and store its raw JSON-LD
    plus its parsed ingredient lines in the scratch database.

    Returns True if a recipe was stored, False if the page yielded no
    recipe data.
    """
    soup = fetch_recipe(url, site)

    recipe_data = extract_recipe_data(site, soup)
    if recipe_data is None:
        return False

    name = soup.title.text if soup.title else ""
    add_recipe(url, name, recipe_data)
    store_recipe_ingredients(url, recipe_data, site)
    return True


def process_sitemap(url: str, site: SiteConfig) -> CrawlReport:
    """Crawl one sitemap's recipe URLs, then finalise it.

    A bad URL (404, timeout, undecodable page) is logged and skipped:
    BBC's sitemap lists tens of thousands of URLs, so a dead link or
    two is likely and one of them must not stop the crawl. The sitemap
    is finalised either way — a permanently dead URL would otherwise
    wedge the queue forever, since it fails identically on every re-run.

    A block is different: consecutive BlockedErrors mean the site is
    shutting the crawl out as a whole, so this sitemap is abandoned
    UNfinalised (its URLs stay queued for a later run) and
    CrawlBlockedError stops the whole crawl.
    """
    report = CrawlReport()
    blocked = 0
    for recipe_url in discover_urls(site, url):
        report.fetched += 1
        try:
            if process_url(recipe_url, site):
                report.stored += 1
            blocked = 0
        except BlockedError as exc:
            blocked += 1
            logger.warning("Blocked on %s: %s", recipe_url, exc)
            if blocked >= _BLOCK_LIMIT:
                raise CrawlBlockedError(
                    f"{site.name}: {blocked} URLs blocked in a row"
                ) from exc
        except Exception as exc:  # noqa: BLE001 - a bad URL must not stop the crawl
            blocked = 0
            report.failed += 1
            logger.warning("Skipping %s: %s: %s", recipe_url, type(exc).__name__, exc)
    finalise_sitemap(url, site.name)
    return report


def crawl_sitemap(site: SiteConfig) -> CrawlReport:
    """Crawl every unfinished sitemap for a site.

    Single process on purpose: the per-host politeness delay lives in
    process memory, so parallel workers would each hammer the site at
    full speed (food-guru's worker is single-process for the same
    reason).

    A sitemap whose discovery fails (unreachable, broken XML) is logged
    and finalised so the crawl moves on to the next one. A block is the
    opposite — a blocked sitemap fetch (402/403/429 from the sitemap
    itself) is a site-wide block: the run stops and everything
    unfinalised stays queued for a later run.
    """
    with get_session() as session:
        urls = [s.url for s in get_unfinished(session, site.name)]

    report = CrawlReport()
    for url in urls:
        try:
            report.merge(process_sitemap(url, site))
        except CrawlBlockedError as exc:
            logger.error("Stopping %s: %s", site.name, exc)
            break
        except BlockedError as exc:
            logger.error("Stopping %s: sitemap %s blocked: %s", site.name, url, exc)
            break
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            logger.warning("Skipping sitemap %s: %s: %s", url, type(exc).__name__, exc)
            finalise_sitemap(url, site.name)

    print(
        f"Done: {report.fetched} fetched, "
        f"{report.stored} stored, {report.failed} failed"
    )
    return report


def reparse_site(site: SiteConfig) -> None:
    """Backfill: re-parse stored ingredient lines without re-fetching.

    Only the site's recipes are reparsed — plus, for the BBC alone,
    rows stamped before sites existed (site IS NULL), which get
    stamped here. Reads each stored recipe's raw JSON-LD again and
    runs it through the parser. Per-recipe failures are counted and
    logged, like a crawl's bad URLs; store_recipe_ingredients replaces
    a recipe's previous rows, so reparsing is idempotent. Afterwards
    ingredients no line and no alias points at are pruned: the reparse
    re-resolves lines through the seeded aliases, stranding the junk
    canonical ingredients the first pass created.
    """
    conditions = [RecipeUrls.site == site.name]
    if site.name == "bbc_good_food":
        # Only the BBC predates the site column. Another site's
        # reparse must not adopt someone else's unstamped rows.
        conditions.append(RecipeUrls.site.is_(None))
    with get_session() as session:
        recipes = [
            (record.url, dict(record.data))
            for record in session.scalars(select(RecipeUrls).where(or_(*conditions)))
        ]

    stored = 0
    failed = 0
    for url, recipe_data in recipes:
        try:
            store_recipe_ingredients(url, recipe_data, site)
            stored += 1
        except Exception as exc:  # noqa: BLE001 - a bad recipe must not stop the backfill
            failed += 1
            logger.warning(
                "Reparse failed for %s: %s: %s", url, type(exc).__name__, exc
            )
    with get_session() as session:
        pruned = prune_orphan_ingredients(session)
    print(f"Reparsed: {stored} recipes, {failed} failed, {pruned} orphans pruned")


def main() -> None:
    logging.basicConfig()
    parser = ArgumentParser(description="Ingest recipes for a registered site.")
    parser.add_argument("site", help="site name from the registry")
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="re-parse stored ingredient lines without re-fetching",
    )
    parser.add_argument(
        "--fdc-report",
        action="store_true",
        help="list canonical ingredients sharing a reference food with "
        "no alias between them (candidates to hand-seed), then exit",
    )
    parser.add_argument(
        "--ref-review",
        action="store_true",
        help="list the top ingredients without a confirmed reference "
        "by ingredient line count, then exit",
    )
    args = parser.parse_args()

    site = get_site(args.site)
    initialise()
    # Seed before parsing or reparsing: the aliases decide canonical
    # resolution, so a fresh database must not parse unseeded. The
    # ref-review report reads the same state — unconfirmed there means
    # unseeded.
    with get_session() as session:
        seed_ingredient_data(session)
    if args.fdc_report:
        for food, names in fdc_id_conflicts():
            print(f"{food}: {', '.join(names)}")
        return
    if args.ref_review:
        for count, name, candidate, matches in ref_review():
            head = f"{count:5d}  {name}:"
            if candidate is not None:
                head += f" parser candidate: {candidate}"
            else:
                head += " no parser candidate"
            print(" | ".join([head, *matches]))
        return
    if args.reparse:
        reparse_site(site)
        return
    try:
        import_sitemap(site)
        crawl_sitemap(site)
    except BlockedError as exc:
        # A blocked sitemap or crawl is a site-wide block: stop cleanly
        # instead of a traceback. Unfinished sitemaps stay queued.
        raise SystemExit(f"{site.name}: blocked: {exc}") from exc


if __name__ == "__main__":
    main()
