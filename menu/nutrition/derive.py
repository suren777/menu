"""Recipe nutrition: published totals and derived-from-ingredients.

Published values are parsed from the JSON-LD nutrition block the
sites already ship ("350 calories", "12 g"); derived values are
summed from ingredient lines and their confirmed nutrition
references. The fixed nutrient set is what BBC and King Arthur
publish, so validation compares like with like.
"""

import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    IngredientFoodRef,
    RecipeIngredient,
    RecipeNutrition,
    RecipeUrls,
    RefFood,
    RefNutrient,
)
from menu.ingest.registry import get_site

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_LEADING_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# JSON-LD nutrition keys -> our fixed nutrient vocabulary. Keys
# outside it (cholesterol, trans fat, servingSize) are ignored.
_PUBLISHED_KEYS = {
    "calories": "energy_kcal",
    "proteinContent": "protein_g",
    "fatContent": "fat_g",
    "saturatedFatContent": "saturated_fat_g",
    "carbohydrateContent": "carbohydrate_g",
    "sugarContent": "sugars_g",
    "fiberContent": "fibre_g",
    "sodiumContent": "sodium_mg",
}


def parse_published_nutrition(data: dict[str, Any]) -> dict[str, float]:
    """The recipe's published nutrition totals, parsed from its
    JSON-LD nutrition block ("350 calories", "12 g", "0.61 milligram
    of sodium"). Empty when the recipe publishes none."""
    block = data.get("nutrition")
    if not isinstance(block, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, nutrient in _PUBLISHED_KEYS.items():
        value = block.get(key)
        if isinstance(value, str) and (match := _LEADING_NUMBER.search(value)):
            parsed[nutrient] = float(match.group())
    return parsed


def _nutrition_ref(
    ingredient_id: int,
    preference: tuple[str, ...],
    session: Session,
) -> tuple[str, list[RefNutrient]] | None:
    """The ingredient's confirmed nutrition reference following the
    site's source order, with its per-100g nutrient rows."""
    by_source: dict[str, int] = dict(
        session.execute(
            select(FoodSource.name, RefFood.id)
            .join(RefFood, RefFood.source_id == FoodSource.id)
            .join(IngredientFoodRef, IngredientFoodRef.ref_food_id == RefFood.id)
            .where(
                IngredientFoodRef.ingredient_id == ingredient_id,
                IngredientFoodRef.role == "nutrition",
                IngredientFoodRef.confirmed.is_(True),
            )
        ).all()
    )
    for source_name in preference:
        ref_food_id = by_source.get(source_name)
        if ref_food_id is None:
            continue
        nutrients = list(
            session.scalars(
                select(RefNutrient).where(RefNutrient.ref_food_id == ref_food_id)
            )
        )
        return source_name, nutrients
    return None


def derive_nutrition(recipe_id: int) -> RecipeNutrition:
    """Derive the recipe's nutrition from its ingredient lines and
    store it as the recipe's "derived" row.

    Each counted line (alternatives and optional lines are skipped)
    contributes grams x per-100g from the first confirmed nutrition
    reference in the site's nutrition_sources order. Ranges use the
    midpoint — the shopping list's upper end is about buying, not
    eating. Lines with no quantity or no reference contribute
    nothing and are counted.
    """
    with get_session() as session:
        recipe = session.get(RecipeUrls, recipe_id)
        if recipe is None:
            raise ValueError(f"no recipe {recipe_id}")
        preference = (
            get_site(recipe.site).nutrition_sources
            if recipe.site is not None
            else ("fdc",)
        )
        lines = session.scalars(
            select(RecipeIngredient).where(
                RecipeIngredient.recipe_id == recipe_id,
                RecipeIngredient.alternative_of.is_(None),
                RecipeIngredient.optional.is_(False),
            )
        ).all()
        totals: dict[str, float] = {}
        definitions: dict[str, set[str]] = {}
        sources: set[str] = set()
        total_grams = 0.0
        mapped_grams = 0.0
        unconverted = 0
        for line in lines:
            if (
                line.quantity is None
                or line.grams is None
                or line.ingredient_id is None
            ):
                unconverted += 1
                continue
            # Ranges buy the upper end, but eating is the midpoint.
            grams = (
                (line.grams + line.grams_max) / 2
                if line.grams_max is not None
                else line.grams
            )
            total_grams += grams
            reference = _nutrition_ref(line.ingredient_id, preference, session)
            if reference is None:
                unconverted += 1
                continue
            source_name, nutrients = reference
            mapped_grams += grams
            sources.add(source_name)
            share = grams / 100.0
            for nutrient in nutrients:
                totals[nutrient.nutrient] = (
                    totals.get(nutrient.nutrient, 0.0)
                    + nutrient.amount_per_100g * share
                )
                definitions.setdefault(nutrient.nutrient, set()).add(
                    nutrient.definition
                )
        row = session.scalar(
            select(RecipeNutrition).where(
                RecipeNutrition.recipe_id == recipe_id,
                RecipeNutrition.source == "derived",
            )
        )
        if row is None:
            row = RecipeNutrition(recipe_id=recipe_id, source="derived")
            session.add(row)
        row.energy_kcal = totals.get("energy_kcal")
        row.protein_g = totals.get("protein_g")
        row.fat_g = totals.get("fat_g")
        row.saturated_fat_g = totals.get("saturated_fat_g")
        row.carbohydrate_g = totals.get("carbohydrate_g")
        row.sugars_g = totals.get("sugars_g")
        row.fibre_g = totals.get("fibre_g")
        row.sodium_mg = totals.get("sodium_mg")
        row.coverage = mapped_grams / total_grams if total_grams else None
        row.unconverted_lines = unconverted
        row.sources = ",".join(sorted(sources)) or None
        row.definitions_mixed = any(len(d) > 1 for d in definitions.values())
        return row


def store_published_nutrition(recipe_id: int) -> None:
    """Store the recipe's published nutrition totals (parsed from its
    JSON-LD block) as its "published" row. A recipe that publishes
    nothing gets no row."""
    with get_session() as session:
        recipe = session.get(RecipeUrls, recipe_id)
        if recipe is None:
            return
        values = parse_published_nutrition(recipe.data)
        if not values:
            return
        row = session.scalar(
            select(RecipeNutrition).where(
                RecipeNutrition.recipe_id == recipe_id,
                RecipeNutrition.source == "published",
            )
        )
        if row is None:
            row = RecipeNutrition(recipe_id=recipe_id, source="published")
            session.add(row)
        row.energy_kcal = values.get("energy_kcal")
        row.protein_g = values.get("protein_g")
        row.fat_g = values.get("fat_g")
        row.saturated_fat_g = values.get("saturated_fat_g")
        row.carbohydrate_g = values.get("carbohydrate_g")
        row.sugars_g = values.get("sugars_g")
        row.fibre_g = values.get("fibre_g")
        row.sodium_mg = values.get("sodium_mg")
