"""Recipe nutrition: published totals and derived-from-ingredients.

Published values are parsed from the JSON-LD nutrition block the
sites already ship ("350 calories", "12 g"); derived values are
summed from ingredient lines and their confirmed nutrition
references. The fixed nutrient set is what BBC and King Arthur
publish, so validation compares like with like. Derivation is pure
and set-based: the reference data is loaded once per run
(`load_nutrition_data`) and every recipe is derived in memory
(`derive_recipe`) — no query per line.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, tuple_

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    RecipeIngredient,
    RefFood,
    RefNutrient,
)
from menu.db.ingredients import repository, seed

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def published_nutrients(data: dict[str, Any]) -> dict[str, tuple[float, str]]:
    """The recipe's published nutrition totals with the raw text each
    was parsed from ("350 calories"), for
    recipe_published_nutrients. Empty when the recipe publishes
    none."""
    block = data.get("nutrition")
    if not isinstance(block, dict):
        return {}
    parsed: dict[str, tuple[float, str]] = {}
    for key, nutrient in _PUBLISHED_KEYS.items():
        value = block.get(key)
        if isinstance(value, str) and (match := _LEADING_NUMBER.search(value)):
            parsed[nutrient] = (float(match.group()), value)
    return parsed


def parse_published_nutrition(data: dict[str, Any]) -> dict[str, float]:
    """The recipe's published nutrition totals, parsed from its
    JSON-LD nutrition block ("350 calories", "12 g", "0.61 milligram
    of sodium"). Empty when the recipe publishes none."""
    return {
        nutrient: amount
        for nutrient, (amount, _raw_text) in published_nutrients(data).items()
    }


@dataclass(frozen=True)
class NutritionData:
    """Everything derivation needs to know about the reference data,
    loaded once per run instead of queried per line: the confirmed
    nutrition references, the loaded foods' per-100g nutrient rows,
    the seed's prepared forms resolved to rows, and the ingredient
    names prepared-form matching reads."""

    refs: dict[int, dict[str, int]]
    """ingredient_id -> {source name: ref_food_id}, confirmed
    nutrition references only."""
    nutrients: dict[int, list[tuple[str, float, str]]]
    """ref_food_id -> [(nutrient, amount per 100 g, definition)], for
    every ref food that is a confirmed reference or a prepared
    form."""
    prepared_forms: dict[tuple[str, str], int]
    """(source name, source_food_id) -> ref_food_id, the seed's
    PREPARED_FORMS resolved to rows."""
    names: dict[int, str]
    """ingredient_id -> canonical name."""


def load_nutrition_data(session: Session) -> NutritionData:
    """Load the reference data derivation needs in three queries: the
    confirmed nutrition references, the prepared forms' foods, and
    the nutrient rows of both."""
    refs: dict[int, dict[str, int]] = {}
    for ingredient_id, source_name, ref_food_id in session.execute(
        select(
            IngredientFoodRef.ingredient_id,
            FoodSource.name,
            IngredientFoodRef.ref_food_id,
        )
        .join(FoodSource, FoodSource.id == IngredientFoodRef.source_id)
        .where(
            IngredientFoodRef.role == "nutrition",
            IngredientFoodRef.status == "confirmed",
        )
    ).all():
        refs.setdefault(ingredient_id, {})[source_name] = ref_food_id
    forms = {
        (source_name, source_food_id): ref_food_id
        for source_name, source_food_id, ref_food_id in session.execute(
            select(FoodSource.name, RefFood.source_food_id, RefFood.id)
            .join(RefFood, RefFood.source_id == FoodSource.id)
            .where(
                tuple_(FoodSource.name, RefFood.source_food_id).in_(
                    list(set(seed.PREPARED_FORMS.values()))
                )
            )
        ).all()
    }
    food_ids = {
        ref_food_id for by_source in refs.values() for ref_food_id in by_source.values()
    } | set(forms.values())
    nutrients: dict[int, list[tuple[str, float, str]]] = {}
    if food_ids:
        for ref_food_id, nutrient, amount, definition in session.execute(
            select(
                RefNutrient.ref_food_id,
                RefNutrient.nutrient,
                RefNutrient.amount_per_100g,
                RefNutrient.definition,
            ).where(RefNutrient.ref_food_id.in_(food_ids))
        ).all():
            nutrients.setdefault(ref_food_id, []).append((nutrient, amount, definition))
    names = dict(session.execute(select(Ingredient.id, Ingredient.name)).all())
    return NutritionData(
        refs=refs, nutrients=nutrients, prepared_forms=forms, names=names
    )


@dataclass(frozen=True)
class NutrientTotal:
    """One nutrient's derived total: the amount summed into it, and
    which definitions and reference sources it was summed from."""

    amount: float
    definitions: frozenset[str]
    sources: frozenset[str]


@dataclass(frozen=True)
class RecipeDerivation:
    """One recipe's derived nutrition, in memory: per-nutrient totals
    (only nutrients some line contributed — absent means not
    reported, never zero), the share of grams the reference data
    mapped, and the metadata the run rows store."""

    nutrients: dict[str, NutrientTotal]
    coverage: float | None
    unconverted_lines: int
    sources: frozenset[str]
    definitions_mixed: bool


def derive_recipe(
    lines: Sequence[tuple[RecipeIngredient, IngredientTerm | None]],
    preference: tuple[str, ...],
    data: NutritionData,
) -> RecipeDerivation:
    """Sum one recipe's nutrition from its counted lines — the pure
    heart of derive_all_nutrition, no session involved.

    Each counted line (alternatives and optional lines are excluded
    by the query) contributes grams x per-100g from the prepared form
    when one matches, else the first confirmed nutrition reference in
    the site's nutrition_sources order. Ranges use the midpoint — the
    shopping list's upper end is about buying, not eating. Lines with
    no quantity, no grams, no effective ingredient or no reference
    contribute nothing and are counted. Failed parses and not_food
    equipment lines are excluded outright: they stay stored but are
    not food. A prepared form whose food or nutrients are not loaded
    falls back to the confirmed reference.
    """
    totals: dict[str, float] = {}
    definitions: dict[str, set[str]] = {}
    nutrient_sources: dict[str, set[str]] = {}
    sources: set[str] = set()
    total_grams = 0.0
    mapped_grams = 0.0
    unconverted = 0
    for line, term in lines:
        if line.parse_status == "failed" or (
            term is not None and term.resolution == "not_food"
        ):
            continue  # not ingredient lines at all: not counted
        effective = repository.effective_ingredient_id(line.needs_review, term)
        variant = term.variant if term is not None else None
        if line.quantity is None or line.grams is None or effective is None:
            unconverted += 1
            continue
        # Ranges buy the upper end, but eating is the midpoint.
        grams = (
            (line.grams + line.grams_max) / 2
            if line.grams_max is not None
            else line.grams
        )
        total_grams += grams
        reference: tuple[str, list[tuple[str, float, str]]] | None = None
        form = seed.prepared_form(data.names[effective], line.preparation, variant)
        if form is not None:
            form_food = data.prepared_forms.get(form)
            form_nutrients = (
                data.nutrients.get(form_food, []) if form_food is not None else []
            )
            # The seed is the confirmation; a form whose food or
            # nutrients are missing falls back to the reference.
            if form_nutrients:
                reference = (form[0], form_nutrients)
        if reference is None:
            by_source = data.refs.get(effective, {})
            for source_name in preference:
                ref_food_id = by_source.get(source_name)
                if ref_food_id is None:
                    continue
                reference = (source_name, data.nutrients.get(ref_food_id, []))
                break
        if reference is None:
            unconverted += 1
            continue
        source_name, reference_nutrients = reference
        mapped_grams += grams
        sources.add(source_name)
        share = grams / 100.0
        for nutrient, amount_per_100g, definition in reference_nutrients:
            totals[nutrient] = totals.get(nutrient, 0.0) + amount_per_100g * share
            definitions.setdefault(nutrient, set()).add(definition)
            nutrient_sources.setdefault(nutrient, set()).add(source_name)
    return RecipeDerivation(
        nutrients={
            nutrient: NutrientTotal(
                amount=amount,
                definitions=frozenset(definitions[nutrient]),
                sources=frozenset(nutrient_sources[nutrient]),
            )
            for nutrient, amount in totals.items()
        },
        coverage=mapped_grams / total_grams if total_grams else None,
        unconverted_lines=unconverted,
        sources=frozenset(sources),
        definitions_mixed=any(len(d) > 1 for d in definitions.values()),
    )
