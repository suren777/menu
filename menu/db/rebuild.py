"""Rebuild the whole scratch database from local files only.

`menu-rebuild` produces a fresh database from the disk cache of
sitemaps and pages, the curated seeds and any downloaded reference
releases — no network access (data_model_plan.md principle 6). Pages
missing from the cache stay queued, so the next live crawl picks them
up; a reference release that isn't downloaded is skipped with a
warning naming its import command.
"""

import logging
from argparse import ArgumentParser
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, func, select

import menu.db.connection
from menu.db.connection import get_session
from menu.db.database import Page, Recipe, initialise
from menu.db.engine import DB_PATH, configure_sqlite
from menu.db.ingredients.seed import seed_ingredient_data
from menu.ingest import sites
from menu.ingest.errors import FetchError
from menu.ingest.fetch import DEFAULT_CACHE_DIR
from menu.ingest.pipeline import crawl_sitemap, import_sitemap
from menu.nutrition.reports import derive_all_nutrition
from menu.nutrition.sources.cnf import CNF_URL, import_cnf
from menu.nutrition.sources.cofid import COFID_URL, import_cofid
from menu.nutrition.sources.fdc import FNDDS_URL, SR_URL, import_fdc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from menu.ingest.registry import SiteConfig

logger = logging.getLogger(__name__)

# A rebuilt database must never be the live one by default.
DEFAULT_TARGET = DB_PATH.with_name("database.rebuilt.db")


@dataclass
class SiteSummary:
    """One site's replay outcome: its pages by status and the recipes
    stored from them."""

    pages: dict[str, int] = field(default_factory=dict)
    recipes: int = 0


@contextmanager
def _default_engine(engine: Engine) -> Iterator[None]:
    """Make `engine` the default for the run: get_session and every
    action resolve menu.db.connection.default_engine at call time
    precisely so this works."""
    original = menu.db.connection.default_engine  # type: ignore[attr-defined]
    menu.db.connection.default_engine = engine  # type: ignore[attr-defined]
    try:
        yield
    finally:
        menu.db.connection.default_engine = original  # type: ignore[attr-defined]


def _skip_source(source: str, sources_dir: Path) -> None:
    logger.warning(
        "%s: release not found under %s — skipped; run "
        "`uv run menu-%s-import` to download it first",
        source,
        sources_dir,
        source,
    )


def _import_source(source: str, sources_dir: Path, session: Session) -> bool:
    """Import one reference release when it is on disk under
    sources_dir, else warn with the command that fetches it. The
    importers read the layout their own main() writes: FDC and CNF
    extracted dataset directories, CoFID the downloaded file itself."""
    if source == "fdc":
        paths = [sources_dir / "fdc" / Path(url).stem for url in (SR_URL, FNDDS_URL)]
        if not all(path.is_dir() for path in paths):
            _skip_source(source, sources_dir)
            return False
        import_fdc(paths, session)
    elif source == "cofid":
        path = sources_dir / "cofid" / Path(COFID_URL).name
        if not path.is_file():
            _skip_source(source, sources_dir)
            return False
        import_cofid(path, session)
    else:  # cnf
        path = sources_dir / "cnf" / Path(CNF_URL).stem
        if not path.is_dir():
            _skip_source(source, sources_dir)
            return False
        import_cnf(path, session)
    return True


def _site_summary(site: SiteConfig, session: Session) -> SiteSummary:
    """The site's replay outcome from the rebuilt database."""
    status_rows = session.execute(
        select(Page.status, func.count())
        .where(Page.site == site.name)
        .group_by(Page.status)
    ).all()
    recipes = session.scalar(
        select(func.count())
        .select_from(Recipe)
        .join(Page, Page.id == Recipe.page_id)
        .where(Page.site == site.name)
    )
    return SiteSummary(
        pages={str(status): int(count) for status, count in status_rows},
        recipes=int(recipes or 0),
    )


def rebuild(
    target: Path, *, sources_dir: Path = DEFAULT_CACHE_DIR / "sources"
) -> dict[str, Any]:
    """Build a complete database at target from local files only.

    Refuses to overwrite an existing target (delete it, or pass
    --force on the command line). Returns a summary: per-site pages by
    status and recipes stored, and which reference sources were
    imported or skipped.
    """
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"{target} already exists — delete it or pass --force")

    engine = create_engine(f"sqlite:///{target}")
    configure_sqlite(engine)
    imported: list[str] = []
    skipped: list[str] = []
    summaries: dict[str, SiteSummary] = {}
    with _default_engine(engine):
        initialise(engine)
        with get_session() as session:
            for source in ("fdc", "cofid", "cnf"):
                if _import_source(source, sources_dir, session):
                    imported.append(source)
                else:
                    skipped.append(source)
            seed_ingredient_data(session)
        for site in sites.registry().values():
            try:
                import_sitemap(site, offline=True)
                crawl_sitemap(site, offline=True)
            except FetchError as exc:
                logger.warning(
                    "%s: top-level sitemap not in the offline cache, "
                    "site skipped: %s",
                    site.name,
                    exc,
                )
                continue
            with get_session() as session:
                summaries[site.name] = _site_summary(site, session)
        derived = derive_all_nutrition()
    engine.dispose()

    summary: dict[str, Any] = {
        "sites": {
            name: {"pages": counts.pages, "recipes": counts.recipes}
            for name, counts in summaries.items()
        },
        "sources": {"imported": imported, "skipped": skipped},
        "nutrition_recipes": derived,
    }
    for name, counts in summaries.items():
        counted = ", ".join(
            f"{status}={count}" for status, count in sorted(counts.pages.items())
        )
        print(f"{name}: pages {counted or 'none'}; recipes stored {counts.recipes}")
    print(
        f"Sources imported: {', '.join(imported) or 'none'}; "
        f"skipped: {', '.join(skipped) or 'none'}; "
        f"nutrition derived for {derived} recipes"
    )
    return summary


def main() -> None:
    logging.basicConfig()
    parser = ArgumentParser(
        description="Rebuild the database from the local cache, seeds "
        "and downloaded reference releases — no network."
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help="database file to build (default: %(default)s)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete an existing target database first",
    )
    args = parser.parse_args()
    if args.force:
        for path in (
            args.target,
            Path(f"{args.target}-wal"),
            Path(f"{args.target}-shm"),
        ):
            path.unlink(missing_ok=True)
    rebuild(args.target)


if __name__ == "__main__":
    main()
