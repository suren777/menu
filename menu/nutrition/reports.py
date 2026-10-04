"""Nutrition reports: derivation over the whole database, validation
against published values, and coverage of the reference data.

The reports are the feedback loop: validation shows how far the
derived totals can be trusted, coverage shows which ingredients to
map next and which regional source to import.
"""

import statistics
from collections import defaultdict

from sqlalchemy import and_, func, select

from menu.db.connection import get_session
from menu.db.database import (
    Ingredient,
    IngredientFoodRef,
    RecipeIngredient,
    RecipeNutrition,
    RecipeUrls,
)
from menu.nutrition.derive import derive_nutrition, store_published_nutrition

# The nutrients validation compares: the per-serving numbers sites
# publish and people check.
_COMPARED = ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g")

HIGH_ERROR = 0.25
"""Kcal relative error above which a recipe is a high-error one."""


def derive_all_nutrition() -> int:
    """Derive and store nutrition for every recipe: a "derived" row
    from the ingredient lines, a "published" row from the JSON-LD
    block. Returns the recipe count."""
    with get_session() as session:
        recipe_ids = list(session.scalars(select(RecipeUrls.id)))
    for recipe_id in recipe_ids:
        derive_nutrition(recipe_id)
        store_published_nutrition(recipe_id)
    return len(recipe_ids)


def validation_report(min_coverage: float = 0.9) -> None:
    """Derived vs published per serving, for recipes that publish
    nutrition, have servings, and whose derivation covered at least
    min_coverage of their grams. Prints the error distribution per
    site and source, the worst recipes by kcal error, and the
    ingredients most often present in high-error recipes."""
    with get_session() as session:
        recipes = {
            recipe.id: recipe for recipe in session.scalars(select(RecipeUrls))
        }
        published = {
            row.recipe_id: row
            for row in session.scalars(
                select(RecipeNutrition).where(RecipeNutrition.source == "published")
            )
        }
        comparisons: list[
            tuple[str | None, str | None, str, dict[str, float]]
        ] = []
        high_ids: list[int] = []
        for row in session.scalars(
            select(RecipeNutrition).where(RecipeNutrition.source == "derived")
        ):
            recipe = recipes.get(row.recipe_id)
            pub = published.get(row.recipe_id)
            if (
                recipe is None
                or pub is None
                or row.coverage is None
                or row.coverage < min_coverage
                or recipe.servings is None
            ):
                continue
            # Published values are per serving; derived totals are per
            # recipe, so they are divided by servings first.
            servings = recipe.servings
            errors: dict[str, float] = {}
            for nutrient in _COMPARED:
                derived_value = getattr(row, nutrient)
                published_value = getattr(pub, nutrient)
                if derived_value is None or published_value in (None, 0):
                    continue
                errors[nutrient] = (
                    abs(derived_value / servings - published_value)
                    / published_value
                )
            if not errors:
                continue
            comparisons.append((recipe.site, row.sources, recipe.name, errors))
            if errors.get("energy_kcal", 0) > HIGH_ERROR:
                high_ids.append(row.recipe_id)
        high_ingredients = (
            session.execute(
                select(Ingredient.name, func.count())
                .join(
                    RecipeIngredient,
                    RecipeIngredient.ingredient_id == Ingredient.id,
                )
                .where(RecipeIngredient.recipe_id.in_(high_ids))
                .group_by(Ingredient.name)
                .order_by(func.count().desc(), Ingredient.name)
                .limit(10)
            ).all()
            if high_ids
            else []
        )

    by_group: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
    for site, sources, _, errors in comparisons:
        by_group[(site or "?", sources or "?")].append(errors)
    for (site, sources), group in sorted(by_group.items()):
        print(f"{site} via {sources}: {len(group)} recipes")
        for nutrient in _COMPARED:
            values = [e[nutrient] for e in group if nutrient in e]
            if values:
                print(
                    f"  {nutrient}: median relative error "
                    f"{statistics.median(values):.0%}"
                )
    worst = sorted(
        (c for c in comparisons if "energy_kcal" in c[3]),
        key=lambda c: c[3]["energy_kcal"],
        reverse=True,
    )[:10]
    if worst:
        print("Worst recipes by kcal error:")
        for site, _, name, errors in worst:
            print(f"  {errors['energy_kcal']:.0%}  {name} ({site})")
    if high_ingredients:
        print(
            f"Ingredients in high-error recipes "
            f"(kcal relative error > {HIGH_ERROR:.0%}):"
        )
        for name, count in high_ingredients:
            print(f"  {count:5d}  {name}")
    if not comparisons:
        print("Nothing to validate: run --derive-nutrition first")


def coverage_report(limit: int = 50) -> None:
    """High-frequency ingredients with no confirmed nutrition
    reference, grouped by site."""
    with get_session() as session:
        rows = session.execute(
            select(RecipeUrls.site, Ingredient.name, func.count())
            .select_from(RecipeIngredient)
            .join(RecipeUrls, RecipeUrls.id == RecipeIngredient.recipe_id)
            .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
            .outerjoin(
                IngredientFoodRef,
                and_(
                    IngredientFoodRef.ingredient_id == Ingredient.id,
                    IngredientFoodRef.role == "nutrition",
                    IngredientFoodRef.confirmed.is_(True),
                ),
            )
            .where(
                RecipeIngredient.alternative_of.is_(None),
                RecipeIngredient.optional.is_(False),
                RecipeIngredient.quantity.is_not(None),
                IngredientFoodRef.ref_food_id.is_(None),
            )
            .group_by(RecipeUrls.site, Ingredient.name)
            .order_by(func.count().desc(), Ingredient.name)
            .limit(limit)
        ).all()

    by_site: dict[str | None, list[tuple[str, int]]] = defaultdict(list)
    for site, name, count in rows:
        by_site[site].append((name, int(count)))
    for site in sorted(by_site, key=lambda s: s or ""):
        print(f"{site}:")
        for name, count in by_site[site]:
            print(f"  {count:5d}  {name}")
