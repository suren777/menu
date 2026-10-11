"""Ingest pipeline: discover -> fetch -> extract -> scratch store.

Run a site end-to-end with:

    uv run python -m menu.ingest bbc_good_food

Every URL's outcome is recorded on its `pages` row (the crawl
ledger), so a re-run continues from the queued pages instead of
re-walking sitemaps, and a recipe is written in the same transaction
as its ingredient lines. The local SQLite database is scratch storage
for experiments; it is never the source of truth. Code here graduates
to food-guru's backend/app/ingest/ with minimal rework.
"""

import hashlib
import json
import logging
import re
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

from menu.db.connection import get_session
from menu.db.database import Page, Sitemap, initialise
from menu.db.ingredients.actions import (
    fdc_id_conflicts,
    prune_orphan_ingredients,
    ref_review,
    store_recipe_ingredients,
)
from menu.db.ingredients.seed import seed_ingredient_data
from menu.db.metrics import collect_metrics
from menu.db.pages import actions as page_actions
from menu.db.pages import repository as pages
from menu.db.sitemaps import actions as sitemap_actions
from menu.db.sitemaps import repository as sitemaps
from menu.ingest.discover import request_xml
from menu.ingest.errors import (
    BlockedError,
    CrawlBlockedError,
    DisallowedError,
    FetchError,
)
from menu.ingest.extract import extract_recipe
from menu.ingest.fetch import fetch_page
from menu.ingest.registry import SiteConfig, get_site
from menu.nutrition.reports import (
    coverage_report,
    derive_all_nutrition,
    validation_report,
)

if TYPE_CHECKING:
    from menu.db.pages.repository import PageModel
    from menu.db.sitemaps.repository import SitemapModel

logger = logging.getLogger(__name__)

# Consecutive blocked URLs before the crawl gives up on the site.
_BLOCK_LIMIT = 5


@dataclass
class CrawlReport:
    """Counts for a crawl run, mirroring food-guru's IngestReport."""

    fetched: int = 0
    stored: int = 0
    failed: int = 0


def _utcnow() -> datetime:
    """Naive UTC for fetched_at columns: SQLite's DateTime keeps no
    timezone of its own (pages.actions.utcnow is the same shape)."""
    return datetime.now(UTC).replace(tzinfo=None)


def _exc_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def import_sitemap(site: SiteConfig, sitemap_url: str | None = None) -> None:
    """Store the site's sitemaps in the ledger, ready to crawl.

    A sitemap index stores itself (kind "index", status "done") and
    its children as pending rows with parent_id — the crawl walks
    those. A plain urlset stores itself as one pending row, so the
    crawl reads its page URLs as normal. Existing rows are left
    alone: re-running records nothing twice.
    """
    urls = [sitemap_url] if sitemap_url else list(site.sitemap_urls)

    for url in urls:
        with get_session() as session:
            if sitemaps.find_by_url(url, session) is not None:
                continue
        kind, entries = request_xml(url, site)
        with get_session() as session:
            record = Sitemap(url=url, site=site.name)
            if kind == "sitemapindex":
                record.kind = "index"
                record.status = "done"
                record.fetched_at = _utcnow()
                session.add(record)
                session.flush()
                for entry in entries:
                    if sitemaps.find_by_url(entry.loc, session) is None:
                        session.add(
                            Sitemap(
                                url=entry.loc,
                                site=site.name,
                                parent_id=record.id,
                            )
                        )
            else:
                record.kind = "urlset"
                session.add(record)


def _walk_sitemap(sitemap: SitemapModel, site: SiteConfig) -> None:
    """Fetch one pending sitemap and queue its recipe URLs as pages.

    The sitemap row records the outcome: done, with its kind and a
    fetched_at. URLs already in pages are skipped, so re-walking is
    cheap. An index lists sitemaps, not pages — import_sitemap stores
    its children — so only a urlset queues pages.
    """
    kind, entries = request_xml(sitemap.url, site)
    pattern = re.compile(site.url_pattern) if site.url_pattern else None
    with get_session() as session:
        record = session.get(Sitemap, sitemap.id)
        if record is None:  # pragma: no cover - deleted mid-crawl
            return
        if kind == "urlset":
            for entry in entries:
                if pattern is not None and not pattern.match(entry.loc):
                    continue
                if pages.page_exists(entry.loc, session):
                    continue
                session.add(
                    Page(
                        url=entry.loc,
                        site=site.name,
                        sitemap_id=record.id,
                        lastmod=entry.lastmod,
                    )
                )
        record.kind = "index" if kind == "sitemapindex" else "urlset"
        record.status = "done"
        record.fetched_at = _utcnow()


def _recipe_name(data: dict[str, Any], soup: BeautifulSoup) -> str:
    """The JSON-LD name when the data gives one, else the page
    <title> text, else ""."""
    name = data.get("name")
    if isinstance(name, str) and name:
        return name
    return soup.title.text if soup.title else ""


def _store_recipe(
    page: PageModel,
    site: SiteConfig,
    soup: BeautifulSoup,
    recipe: tuple[dict[str, Any], str] | None,
) -> bool:
    """Store the extracted recipe and its lines in ONE transaction.

    An upsert on data_sha256: a new recipe is inserted, changed data
    replaces it and its lines, unchanged data does nothing (the lines
    stay as they are). Returns whether anything was stored.
    """
    if recipe is None:
        return False
    data, extractor = recipe
    name = _recipe_name(data, soup)
    with get_session() as session:
        stored = pages.upsert_recipe(page.id, name, data, extractor, session)
        if stored is None:
            return False
        store_recipe_ingredients(stored.id, data, site, session)
        return True


def process_url(page: PageModel, site: SiteConfig) -> str:
    """Fetch one page (cached, polite) and record the outcome on its
    ledger row: 'fetched' with the recipe stored, 'no_recipe' when the
    page holds no recipe, 'failed'/'blocked'/'disallowed' with the
    error otherwise. The recipe and its ingredient lines go in one
    transaction.

    Returns the recorded status, so the crawl can count stores and
    failures; a BlockedError is re-raised for the block-streak logic.
    """
    try:
        content = fetch_page(page.url, site)
    except DisallowedError as exc:
        page_actions.record_outcome(page.id, "disallowed", error=_exc_text(exc))
        return "disallowed"
    except BlockedError as exc:
        page_actions.record_outcome(
            page.id, "blocked", http_status=exc.status_code, error=_exc_text(exc)
        )
        raise
    except FetchError as exc:
        page_actions.record_outcome(
            page.id, "failed", http_status=exc.status_code, error=_exc_text(exc)
        )
        return "failed"
    except Exception as exc:  # noqa: BLE001 - a bad URL must not stop the crawl
        page_actions.record_outcome(page.id, "failed", error=_exc_text(exc))
        return "failed"

    content_sha = hashlib.sha256(content).hexdigest()
    soup = BeautifulSoup(content, "html.parser")
    recipe = extract_recipe(site, soup)
    try:
        _store_recipe(page, site, soup, recipe)
    except Exception as exc:  # noqa: BLE001 - a bad recipe must not stop the crawl
        page_actions.record_outcome(
            page.id,
            "failed",
            http_status=200,
            content_sha256=content_sha,
            error=_exc_text(exc),
        )
        return "failed"

    status = "fetched" if recipe is not None else "no_recipe"
    page_actions.record_outcome(
        page.id,
        status,
        http_status=200,
        content_sha256=content_sha,
        fetched=True,
    )
    return status


def _crawl_pages(site: SiteConfig, report: CrawlReport) -> None:
    """Fetch the site's queued and blocked pages in id order, counting
    the outcomes into the report.

    A bad page (404, timeout, undecodable body) is recorded on its row
    and skipped: BBC's sitemaps list tens of thousands of URLs, so a
    dead link or two is likely and one of them must not stop the
    crawl. A block is different: consecutive BlockedErrors mean the
    site is shutting the crawl out as a whole, so CrawlBlockedError
    stops the run with the remaining pages queued for a later run.
    """
    with get_session() as session:
        queue = pages.get_unfinished(session, site.name)

    blocked = 0
    for page in queue:
        report.fetched += 1
        try:
            status = process_url(page, site)
        except BlockedError as exc:
            blocked += 1
            logger.warning("Blocked on %s: %s", page.url, exc)
            if blocked >= _BLOCK_LIMIT:
                raise CrawlBlockedError(
                    f"{site.name}: {blocked} pages blocked in a row"
                ) from exc
            continue
        blocked = 0
        if status == "fetched":
            report.stored += 1
        elif status == "failed":
            report.failed += 1


def crawl_sitemap(site: SiteConfig) -> CrawlReport:
    """Crawl a site: queue pages from its unfetched sitemaps, then
    fetch every queued or blocked page, in id order.

    Single process on purpose: the per-host politeness delay lives in
    process memory, so parallel workers would each hammer the site at
    full speed (food-guru's worker is single-process for the same
    reason).

    A sitemap whose fetch fails (unreachable, broken XML) is marked
    failed and the crawl moves on. A block is the opposite — a blocked
    sitemap fetch (402/403/429 from the sitemap itself) is a site-wide
    block: the run stops and the unfinished work stays queued for a
    later run.
    """
    report = CrawlReport()
    with get_session() as session:
        pending = sitemaps.get_unfinished(session, site.name)

    stopped = False
    for sitemap in pending:
        try:
            _walk_sitemap(sitemap, site)
        except BlockedError as exc:
            sitemap_actions.set_status(
                sitemap.url, site.name, "blocked", error=_exc_text(exc)
            )
            logger.error(
                "Stopping %s: sitemap %s blocked: %s", site.name, sitemap.url, exc
            )
            stopped = True
            break
        except Exception as exc:  # noqa: BLE001 - a dead sitemap must not stop the crawl
            sitemap_actions.set_status(
                sitemap.url, site.name, "failed", error=_exc_text(exc)
            )
            report.failed += 1
            logger.warning(
                "Skipping sitemap %s: %s: %s", sitemap.url, type(exc).__name__, exc
            )

    if not stopped:
        try:
            _crawl_pages(site, report)
        except CrawlBlockedError as exc:
            logger.error("Stopping %s: %s", site.name, exc)

    print(
        f"Done: {report.fetched} fetched, "
        f"{report.stored} stored, {report.failed} failed"
    )
    return report


def reparse_site(site: SiteConfig) -> None:
    """Backfill: re-parse stored ingredient lines without re-fetching.

    The site's recipes — found through their pages — are re-stored,
    one session per recipe. Per-recipe failures are counted and
    logged, like a crawl's bad URLs; store_recipe_ingredients replaces
    a recipe's previous rows, so reparsing is idempotent. Afterwards
    ingredients no line and no alias points at are pruned: the reparse
    re-resolves lines through the seeded aliases, stranding the junk
    canonical ingredients the first pass created.
    """
    with get_session() as session:
        recipes = pages.get_site_recipes(site.name, session)

    stored = 0
    failed = 0
    for recipe, url in recipes:
        try:
            with get_session() as session:
                store_recipe_ingredients(recipe.id, recipe.data, site, session)
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
    parser.add_argument(
        "site",
        nargs="?",
        help="site name from the registry (not needed for the "
        "nutrition reports)",
    )
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
    parser.add_argument(
        "--derive-nutrition",
        action="store_true",
        help="derive nutrition for every stored recipe and backfill "
        "published rows, then exit",
    )
    parser.add_argument(
        "--validate-nutrition",
        action="store_true",
        help="compare derived vs published nutrition per serving, "
        "then exit",
    )
    parser.add_argument(
        "--coverage-report",
        action="store_true",
        help="list high-frequency ingredients with no confirmed "
        "nutrition reference, then exit",
    )
    parser.add_argument(
        "--metrics",
        metavar="PATH",
        help="write a JSON metrics snapshot to PATH, then exit",
    )
    args = parser.parse_args()

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
    if args.derive_nutrition:
        print(f"Derived nutrition for {derive_all_nutrition()} recipes")
        return
    if args.validate_nutrition:
        validation_report()
        return
    if args.coverage_report:
        coverage_report()
        return
    if args.metrics:
        path = Path(args.metrics)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(collect_metrics(), indent=2, sort_keys=True) + "\n"
        )
        print(f"Metrics snapshot written to {path}")
        return
    if args.site is None:
        parser.error("a site is required unless a report flag is given")
    site = get_site(args.site)
    if args.reparse:
        reparse_site(site)
        return
    try:
        import_sitemap(site)
        crawl_sitemap(site)
    except BlockedError as exc:
        # A blocked sitemap or crawl is a site-wide block: stop cleanly
        # instead of a traceback. Unfinished pages stay queued.
        raise SystemExit(f"{site.name}: blocked: {exc}") from exc


if __name__ == "__main__":
    main()
