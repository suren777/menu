"""The menu-ingest CLI: argument parsing and dispatch over the ingest
pipeline, the reports and the metrics snapshot.

Run a site end-to-end with:

    uv run menu-ingest crawl bbc_good_food

Every subcommand opens its own sessions (initialise, seed, then the
handler) — library code takes a session from the caller instead.
"""

import json
import logging
from argparse import ArgumentParser, ArgumentTypeError
from pathlib import Path
from typing import TYPE_CHECKING

from menu.db.connection import get_session
from menu.db.database import initialise
from menu.db.ingredients.resolve import re_resolve
from menu.db.ingredients.seed import seed_ingredient_data
from menu.ingest.errors import BlockedError
from menu.ingest.ingredient_reports import fdc_id_conflicts, ref_review
from menu.ingest.pipeline import (
    crawl_sitemap,
    import_sitemap,
    reparse_site,
    suggest_command,
    suggest_embedder,
)
from menu.ingest.registry import get_site
from menu.nutrition.reports import (
    coverage_report,
    derive_all_nutrition,
    latest_run_id,
    run_comparison_report,
    run_recipe_count,
    validation_report,
)
from menu.scripts.metrics import collect_metrics

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Sequence

_SUGGEST_SETS = ("queue", "audit", "pair", "food_ref")


def _comma_sets(value: str) -> list[str]:
    """The --sets comma list: deduplicated, with unknown names rejected
    so argparse reports them like any other bad argument."""
    names = list(dict.fromkeys(value.split(",")))
    unknown = [name for name in names if name not in _SUGGEST_SETS]
    if unknown:
        raise ArgumentTypeError(f"unknown input set(s): {', '.join(unknown)}")
    return names


def _positive_int(value: str) -> int:
    """A --limit value of at least 1."""
    number = int(value)
    if number < 1:
        raise ArgumentTypeError("must be at least 1")
    return number


def _cmd_crawl(args: Namespace) -> None:
    site = get_site(args.site)
    try:
        import_sitemap(site)
        crawl_sitemap(site)
    except BlockedError as exc:
        # A blocked sitemap or crawl is a site-wide block: stop cleanly
        # instead of a traceback. Unfinished pages stay queued.
        raise SystemExit(f"{site.name}: blocked: {exc}") from exc


def _cmd_reparse(args: Namespace) -> None:
    reparse_site(get_site(args.site))


def _cmd_re_resolve(_args: Namespace) -> None:
    with get_session() as session:
        terms_changed, regrammed, pruned = re_resolve(session)
    print(
        f"Re-resolved: {terms_changed} terms changed, "
        f"{regrammed} lines regrammed, {pruned} concepts pruned"
    )


def _cmd_suggest(args: Namespace) -> None:
    suggest_command(args.sets, limit=args.limit, dry_run=args.dry_run)


def _cmd_derive_nutrition(args: Namespace) -> None:
    with get_session() as session:
        run = derive_all_nutrition(session, note=args.note)
        recipes = run_recipe_count(session, run.id)
    print(f"Derivation run {run.id}: {recipes} recipes")


def _cmd_report_fdc_conflicts(_args: Namespace) -> None:
    with get_session() as session:
        conflicts = fdc_id_conflicts(session)
    for food, names in conflicts:
        print(f"{food}: {', '.join(names)}")


def _cmd_report_ref_review(_args: Namespace) -> None:
    with get_session() as session:
        rows = ref_review(session, embedder=suggest_embedder())
    for count, name, candidate, matches, neighbours in rows:
        head = f"{count:5d}  {name}:"
        if candidate is not None:
            head += f" parser candidate: {candidate}"
        else:
            head += " no parser candidate"
        print(" | ".join([head, *matches]))
        if neighbours:
            print(f"    {' | '.join(neighbours)}")


def _cmd_report_validate_nutrition(args: Namespace) -> None:
    with get_session() as session:
        if args.against is not None:
            # --against compares --run (default: the latest) with run M
            # instead of the published values.
            run_id = args.run if args.run is not None else latest_run_id(session)
            if run_id is None:
                print("Nothing to validate: run derive-nutrition first")
                return
            run_comparison_report(session, run_id, args.against)
        else:
            validation_report(session, run_id=args.run)


def _cmd_report_coverage(_args: Namespace) -> None:
    with get_session() as session:
        coverage_report(session)


def _cmd_metrics(args: Namespace) -> None:
    with get_session() as session:
        metrics = collect_metrics(session)
    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    print(f"Metrics snapshot written to {path}")


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Ingest recipes for a registered site.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl_parser = subparsers.add_parser(
        "crawl", help="import the site's sitemaps and crawl its queued pages"
    )
    crawl_parser.add_argument("site", help="site name from the registry")
    crawl_parser.set_defaults(handler=_cmd_crawl)

    reparse_parser = subparsers.add_parser(
        "reparse", help="re-parse stored ingredient lines without re-fetching"
    )
    reparse_parser.add_argument("site", help="site name from the registry")
    reparse_parser.set_defaults(handler=_cmd_reparse)

    re_resolve_parser = subparsers.add_parser(
        "re-resolve",
        help="re-apply the seeds and review heuristics to the terms "
        "without re-running the parser",
    )
    re_resolve_parser.set_defaults(handler=_cmd_re_resolve)

    suggest_parser = subparsers.add_parser(
        "suggest",
        help="send the review input sets through the LLM and append the "
        "verdicts to suggestions.json",
    )
    suggest_parser.add_argument(
        "--sets",
        type=_comma_sets,
        default=list(_SUGGEST_SETS),
        metavar="SETS",
        help="comma list of queue,audit,pair,food_ref (default all four)",
    )
    suggest_parser.add_argument(
        "--limit",
        type=_positive_int,
        metavar="N",
        help="cap the items per input set",
    )
    suggest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print per-set item counts and an estimated token budget "
        "without calling the LLM",
    )
    suggest_parser.set_defaults(handler=_cmd_suggest)

    derive_parser = subparsers.add_parser(
        "derive-nutrition",
        help="derive nutrition for every stored recipe as one run and "
        "backfill the published rows",
    )
    derive_parser.add_argument(
        "--note",
        help="label the run (a baseline, an experiment) — stored on the run row",
    )
    derive_parser.set_defaults(handler=_cmd_derive_nutrition)

    report_parser = subparsers.add_parser("report", help="read-only reports")
    reports = report_parser.add_subparsers(dest="report", required=True)
    fdc_parser = reports.add_parser(
        "fdc-conflicts",
        help="list canonical ingredients sharing a reference food with "
        "no alias between them (candidates to hand-seed)",
    )
    fdc_parser.set_defaults(handler=_cmd_report_fdc_conflicts)
    ref_review_parser = reports.add_parser(
        "ref-review",
        help="list the top ingredients without a confirmed reference by "
        "ingredient line count, with text and embedding neighbours",
    )
    ref_review_parser.set_defaults(handler=_cmd_report_ref_review)
    validate_parser = reports.add_parser(
        "validate-nutrition",
        help="compare derived vs published nutrition per serving, or "
        "two derivation runs with each other",
    )
    validate_parser.add_argument(
        "--run",
        type=int,
        metavar="N",
        help="the derivation run to validate (default: the latest)",
    )
    validate_parser.add_argument(
        "--against",
        type=int,
        metavar="M",
        help="compare run N with run M instead of the published values",
    )
    validate_parser.set_defaults(handler=_cmd_report_validate_nutrition)
    coverage_parser = reports.add_parser(
        "coverage",
        help="list high-frequency ingredients with no confirmed nutrition reference",
    )
    coverage_parser.set_defaults(handler=_cmd_report_coverage)

    metrics_parser = subparsers.add_parser(
        "metrics", help="write a JSON metrics snapshot"
    )
    metrics_parser.add_argument("path", metavar="PATH", help="where to write it")
    metrics_parser.set_defaults(handler=_cmd_metrics)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig()
    args = _build_parser().parse_args(argv)

    initialise()
    # Seed before parsing or reparsing: the aliases decide canonical
    # resolution, so a fresh database must not parse unseeded. The
    # ref-review report reads the same state — unconfirmed there means
    # unseeded.
    with get_session() as session:
        seed_ingredient_data(session)
    args.handler(args)


if __name__ == "__main__":
    main()
