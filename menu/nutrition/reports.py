"""Nutrition reports: derivation runs over the whole database,
validation against published values, run-to-run comparison, and
coverage of the reference data.

The reports are the feedback loop: validation shows how far the
derived totals can be trusted, coverage shows which ingredients to
map next and which regional source to import, and the run comparison
shows what a seed or logic change did to the numbers.
"""

import importlib.metadata
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, insert, select

from menu.db.database import (
    DerivationRun,
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrient,
    RecipeNutritionRun,
    RecipePublishedNutrient,
)
from menu.db.ingredients import repository
from menu.db.ingredients.seed import seed_digest
from menu.ingest.registry import get_site
from menu.nutrition.derive import (
    derive_recipe,
    load_nutrition_data,
    published_nutrients,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# The nutrients validation compares: the per-serving numbers sites
# publish and people check.
COMPARED = ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g")

HIGH_ERROR = 0.25
"""Kcal relative error above which a recipe is a high-error one."""

CHANGE_THRESHOLD = 0.01
"""Relative change above which a nutrient counts as changed."""


def derive_all_nutrition(
    session: Session, *, note: str | None = None, batch_size: int = 500
) -> DerivationRun:
    """Derive nutrition for every recipe as one stored run: a
    recipe_nutrition_runs row per recipe and a recipe_nutrients row
    per contributed nutrient, plus a refresh of the raw
    recipe_published_nutrients. The reference data is loaded once and
    the recipes stream through in batches — no query per line. Runs
    in the caller's transaction, prunes older nutrition runs, and
    returns the run."""
    run = DerivationRun(
        kind="nutrition",
        parser_version=importlib.metadata.version("ingredient-parser-nlp"),
        seed_digest=seed_digest(),
        sources=dict(
            session.execute(select(FoodSource.name, FoodSource.version)).all()
        ),
        note=note,
    )
    session.add(run)
    session.flush()  # assign run.id for the batch rows
    data = load_nutrition_data(session)
    recipe_ids = list(session.scalars(select(Recipe.id).order_by(Recipe.id)))
    for offset in range(0, len(recipe_ids), batch_size):
        batch = recipe_ids[offset : offset + batch_size]
        sites_and_data = {
            recipe_id: (site, recipe_data)
            for recipe_id, site, recipe_data in session.execute(
                select(Recipe.id, Page.site, Recipe.data)
                .join(Page, Page.id == Recipe.page_id)
                .where(Recipe.id.in_(batch))
            ).all()
        }
        lines: dict[int, list[tuple[RecipeIngredient, IngredientTerm | None]]] = (
            defaultdict(list)
        )
        for line, term in session.execute(
            repository.lines_with_terms_select(
                RecipeIngredient.recipe_id.in_(batch),
                RecipeIngredient.alternative_of.is_(None),
                RecipeIngredient.optional.is_(False),
            )
        ).all():
            lines[line.recipe_id].append((line, term))
        run_rows: list[dict[str, Any]] = []
        nutrient_rows: list[dict[str, Any]] = []
        published_rows: list[dict[str, Any]] = []
        for recipe_id in batch:
            site, recipe_data = sites_and_data[recipe_id]
            preference = (
                get_site(site).nutrition_sources if site is not None else ("fdc",)
            )
            derivation = derive_recipe(lines.get(recipe_id, []), preference, data)
            run_rows.append(
                {
                    "run_id": run.id,
                    "recipe_id": recipe_id,
                    "coverage": derivation.coverage,
                    "unconverted_lines": derivation.unconverted_lines,
                    "sources": sorted(derivation.sources),
                    "definitions_mixed": derivation.definitions_mixed,
                }
            )
            nutrient_rows.extend(
                {
                    "run_id": run.id,
                    "recipe_id": recipe_id,
                    "nutrient": nutrient,
                    "amount": total.amount,
                    "definitions": sorted(total.definitions),
                    "sources": sorted(total.sources),
                }
                for nutrient, total in derivation.nutrients.items()
            )
            for nutrient, (amount, raw_text) in published_nutrients(
                recipe_data
            ).items():
                published_rows.append(
                    {
                        "recipe_id": recipe_id,
                        "nutrient": nutrient,
                        "amount": amount,
                        "raw_text": raw_text,
                    }
                )
        # Published values are raw capture: replace the batch's rows.
        session.execute(
            delete(RecipePublishedNutrient).where(
                RecipePublishedNutrient.recipe_id.in_(batch)
            )
        )
        if run_rows:
            session.execute(insert(RecipeNutritionRun), run_rows)
        if nutrient_rows:
            session.execute(insert(RecipeNutrient), nutrient_rows)
        if published_rows:
            session.execute(insert(RecipePublishedNutrient), published_rows)
    prune_runs(session)
    return run


def prune_runs(session: Session, keep: int = 10) -> None:
    """Delete nutrition runs older than the last `keep`; the
    per-recipe and per-nutrient rows cascade."""
    keep_ids = list(
        session.scalars(
            select(DerivationRun.id)
            .where(DerivationRun.kind == "nutrition")
            .order_by(DerivationRun.id.desc())
            .limit(keep)
        )
    )
    if not keep_ids:
        return
    session.execute(
        delete(DerivationRun).where(
            DerivationRun.kind == "nutrition", DerivationRun.id.not_in(keep_ids)
        )
    )


def latest_run_id(session: Session) -> int | None:
    """The newest nutrition run, or None before the first one."""
    return session.scalar(
        select(DerivationRun.id)
        .where(DerivationRun.kind == "nutrition")
        .order_by(DerivationRun.id.desc())
        .limit(1)
    )


def validation_comparisons(
    session: Session, run_id: int | None = None, min_coverage: float = 0.9
) -> list[tuple[int, str | None, str | None, str, dict[str, float]]]:
    """Derived vs published per serving, for recipes that publish
    nutrition, have servings, and whose derivation covered at least
    min_coverage of their grams: (recipe_id, site, sources, name,
    {nutrient: relative error}). Derived values come from the run's
    rows (default: the latest run); shared by the printed report and
    the metrics snapshot."""
    if run_id is None:
        run_id = latest_run_id(session)
    if run_id is None:
        return []
    comparisons: list[tuple[int, str | None, str | None, str, dict[str, float]]] = []
    # The recipe's site is its page's site (recipes have none of
    # their own).
    recipes = {
        recipe.id: (recipe, site)
        for recipe, site in session.execute(
            select(Recipe, Page.site).join(Page, Page.id == Recipe.page_id)
        ).all()
    }
    published: dict[int, dict[str, float]] = defaultdict(dict)
    for published_row in session.scalars(select(RecipePublishedNutrient)):
        by_recipe = published[published_row.recipe_id]
        by_recipe[published_row.nutrient] = published_row.amount
    run_rows = {
        row.recipe_id: row
        for row in session.scalars(
            select(RecipeNutritionRun).where(RecipeNutritionRun.run_id == run_id)
        )
    }
    derived = {
        (row.recipe_id, row.nutrient): row.amount
        for row in session.scalars(
            select(RecipeNutrient).where(RecipeNutrient.run_id == run_id)
        )
    }
    for row in run_rows.values():
        match = recipes.get(row.recipe_id)
        pub = published.get(row.recipe_id)
        if (
            match is None
            or pub is None
            or row.coverage is None
            or row.coverage < min_coverage
            or not match[0].servings
        ):
            continue
        recipe, site = match
        # Published values are per serving; derived totals are per
        # recipe, so they are divided by servings first.
        servings = recipe.servings
        errors: dict[str, float] = {}
        for nutrient in COMPARED:
            derived_value = derived.get((row.recipe_id, nutrient))
            published_value = pub.get(nutrient)
            if derived_value is None or not servings or published_value in (None, 0):
                continue
            errors[nutrient] = (
                abs(derived_value / servings - published_value) / published_value
            )
        if not errors:
            continue
        comparisons.append(
            (
                row.recipe_id,
                site,
                ",".join(sorted(row.sources)) or None,
                recipe.name,
                errors,
            )
        )
    return comparisons


def validation_report(
    session: Session, run_id: int | None = None, min_coverage: float = 0.9
) -> None:
    """Derived vs published per serving for one run (default: the
    latest), for recipes that publish nutrition, have servings, and
    whose derivation covered at least min_coverage of their grams.
    Prints the run it validated, the error distribution per site and
    source, the worst recipes by kcal error, and the ingredients most
    often present in high-error recipes."""
    if run_id is None:
        run_id = latest_run_id(session)
    if run_id is None:
        print("Nothing to validate: run derive-nutrition first")
        return
    comparisons = validation_comparisons(session, run_id, min_coverage)
    if not comparisons:
        print(f"Nothing to validate in run {run_id}")
        return
    print(f"Validating derivation run {run_id}")
    high_ids = [
        recipe_id
        for recipe_id, _, _, _, errors in comparisons
        if errors.get("energy_kcal", 0) > HIGH_ERROR
    ]
    high_ingredients = (
        session.execute(
            select(Ingredient.name, func.count())
            .join(IngredientTerm, IngredientTerm.ingredient_id == Ingredient.id)
            .join(RecipeIngredient, RecipeIngredient.term_id == IngredientTerm.id)
            .where(
                RecipeIngredient.recipe_id.in_(high_ids),
                *repository.effective_ingredient_predicates(),
            )
            .group_by(Ingredient.name)
            .order_by(func.count().desc(), Ingredient.name)
            .limit(10)
        ).all()
        if high_ids
        else []
    )

    by_group: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
    for _recipe_id, site, sources, _, errors in comparisons:
        by_group[(site or "?", sources or "?")].append(errors)
    for (site, sources), group in sorted(by_group.items()):
        print(f"{site} via {sources}: {len(group)} recipes")
        for nutrient in COMPARED:
            values = [e[nutrient] for e in group if nutrient in e]
            if values:
                print(
                    f"  {nutrient}: median relative error "
                    f"{statistics.median(values):.0%}"
                )
    worst = sorted(
        (c for c in comparisons if "energy_kcal" in c[4]),
        key=lambda c: c[4]["energy_kcal"],
        reverse=True,
    )[:10]
    if worst:
        print("Worst recipes by kcal error:")
        for _, site, _, name, errors in worst:
            print(f"  {errors['energy_kcal']:.0%}  {name} ({site})")
    if high_ingredients:
        print(
            f"Ingredients in high-error recipes "
            f"(kcal relative error > {HIGH_ERROR:.0%}):"
        )
        for name, count in high_ingredients:
            print(f"  {count:5d}  {name}")


@dataclass(frozen=True)
class RunInfo:
    """A run's provenance: what produced its numbers."""

    run_id: int
    seed_digest: str
    parser_version: str


@dataclass(frozen=True)
class RunComparison:
    """What changed between two nutrition runs, over the recipes both
    cover: per-nutrient movement, each run's mean coverage, and how
    many recipes changed coverage."""

    run: RunInfo
    against: RunInfo
    nutrients: dict[str, tuple[int, float | None]]
    """Per COMPARED nutrient: (recipes whose amount changed by more
    than 1%, median relative change)."""
    mean_coverage: float | None
    mean_coverage_against: float | None
    coverage_changed: int


def run_comparison(session: Session, run_id: int, against_id: int) -> RunComparison:
    """Compare two derivation runs over the recipes in both: per
    nutrient in COMPARED, how many recipes moved by more than 1% and
    the median relative change; plus each run's mean coverage and the
    count of recipes whose coverage changed."""

    def info(identifier: int) -> RunInfo:
        run = session.get(DerivationRun, identifier)
        if run is None:
            raise ValueError(f"no derivation run {identifier}")
        return RunInfo(
            run_id=run.id,
            seed_digest=run.seed_digest,
            parser_version=run.parser_version,
        )

    def amounts(identifier: int) -> dict[tuple[int, str], float]:
        return {
            (row.recipe_id, row.nutrient): row.amount
            for row in session.scalars(
                select(RecipeNutrient).where(RecipeNutrient.run_id == identifier)
            )
        }

    def coverages(identifier: int) -> dict[int, float]:
        return {
            row.recipe_id: row.coverage
            for row in session.scalars(
                select(RecipeNutritionRun).where(
                    RecipeNutritionRun.run_id == identifier
                )
            )
            if row.coverage is not None
        }

    def fmean(coverages: dict[int, float]) -> float | None:
        return statistics.fmean(coverages.values()) if coverages else None

    before = amounts(against_id)
    after = amounts(run_id)
    common = {recipe_id for recipe_id, _ in before} & {
        recipe_id for recipe_id, _ in after
    }
    nutrients: dict[str, tuple[int, float | None]] = {}
    for nutrient in COMPARED:
        changes = []
        changed = 0
        for recipe_id in common:
            old = before.get((recipe_id, nutrient))
            new = after.get((recipe_id, nutrient))
            if old is None or new is None or old == 0:
                continue
            change = abs(new - old) / old
            changes.append(change)
            if change > CHANGE_THRESHOLD:
                changed += 1
        nutrients[nutrient] = (
            changed,
            statistics.median(changes) if changes else None,
        )
    before_coverage = coverages(against_id)
    after_coverage = coverages(run_id)
    coverage_changed = sum(
        1
        for recipe_id, coverage in after_coverage.items()
        if recipe_id in before_coverage and before_coverage[recipe_id] != coverage
    )
    return RunComparison(
        run=info(run_id),
        against=info(against_id),
        nutrients=nutrients,
        mean_coverage=fmean(after_coverage),
        mean_coverage_against=fmean(before_coverage),
        coverage_changed=coverage_changed,
    )


def _share(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "n/a"


def run_comparison_report(session: Session, run_id: int, against_id: int) -> None:
    """Print what changed between two derivation runs: both runs'
    provenance (seed digest and parser version, so a reviewer sees
    what changed), the per-nutrient movement, mean coverage of each,
    and how many recipes changed coverage."""
    comparison = run_comparison(session, run_id, against_id)
    for info in (comparison.against, comparison.run):
        print(
            f"Run {info.run_id}: seed {info.seed_digest}, parser {info.parser_version}"
        )
    print(
        f"Mean coverage: {_share(comparison.mean_coverage_against)} -> "
        f"{_share(comparison.mean_coverage)}"
    )
    print(f"Recipes whose coverage changed: {comparison.coverage_changed}")
    for nutrient, (changed, median_change) in comparison.nutrients.items():
        median_text = (
            f"{median_change:.1%}"
            if median_change is not None
            else "no comparable recipes"
        )
        print(
            f"  {nutrient}: {changed} changed by more than 1%, "
            f"median change {median_text}"
        )


def coverage_report(session: Session, limit: int = 50) -> None:
    """High-frequency ingredients with no confirmed nutrition
    reference, grouped by site."""
    rows = session.execute(
        select(Page.site, Ingredient.name, func.count())
        .select_from(RecipeIngredient)
        .join(IngredientTerm, IngredientTerm.id == RecipeIngredient.term_id)
        .join(Ingredient, Ingredient.id == IngredientTerm.ingredient_id)
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        .join(Page, Page.id == Recipe.page_id)
        .outerjoin(
            IngredientFoodRef,
            and_(
                IngredientFoodRef.ingredient_id == Ingredient.id,
                IngredientFoodRef.role == "nutrition",
                IngredientFoodRef.status == "confirmed",
            ),
        )
        .where(
            RecipeIngredient.alternative_of.is_(None),
            RecipeIngredient.optional.is_(False),
            RecipeIngredient.quantity.is_not(None),
            IngredientFoodRef.ref_food_id.is_(None),
            *repository.effective_ingredient_predicates(),
        )
        .group_by(Page.site, Ingredient.name)
        .order_by(func.count().desc(), Ingredient.name)
        .limit(limit)
    ).all()

    by_site: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for site, name, count in rows:
        by_site[site].append((name, int(count)))
    for site in sorted(by_site, key=lambda s: s or ""):
        print(f"{site}:")
        for name, count in by_site[site]:
            print(f"  {count:5d}  {name}")
