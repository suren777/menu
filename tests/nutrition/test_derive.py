"""Nutrition tests: the published-nutrition parser and derivation."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    RecipeIngredient,
    RecipeNutrition,
    RecipeUrls,
    RefFood,
    RefNutrient,
)
from menu.nutrition.derive import derive_nutrition, parse_published_nutrition

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FIXTURE = Path(__file__).parent / "fixtures" / "published_nutrition.json"


def test_parse_published_nutrition_bbc() -> None:
    """BBC spells values out ("22 grams fat", "0.61 milligram of
    sodium")."""
    data = json.loads(FIXTURE.read_text())
    assert parse_published_nutrition({"nutrition": data["bbc_good_food"]}) == {
        "energy_kcal": 681.0,
        "protein_g": 16.0,
        "fat_g": 22.0,
        "saturated_fat_g": 11.0,
        "carbohydrate_g": 98.0,
        "sugars_g": 37.0,
        "fibre_g": 4.0,
        "sodium_mg": 0.61,
    }


def test_parse_published_nutrition_king_arthur() -> None:
    """King Arthur spells values compactly ("20g") and publishes keys
    outside the fixed set (cholesterol, trans fat, servingSize),
    which are ignored."""
    data = json.loads(FIXTURE.read_text())
    assert parse_published_nutrition({"nutrition": data["king_arthur"]}) == {
        "energy_kcal": 150.0,
        "protein_g": 4.0,
        "fat_g": 6.0,
        "saturated_fat_g": 1.5,
        "carbohydrate_g": 20.0,
        "sugars_g": 3.0,
        "fibre_g": 2.0,
        "sodium_mg": 200.0,
    }


def test_parse_published_nutrition_missing_block() -> None:
    """Recipes without a nutrition block parse to nothing."""
    assert parse_published_nutrition({}) == {}


def _add_nutrition_food(
    session: Session,
    source_food_id: str,
    description: str,
    nutrients: list[tuple[str, float, str]],
) -> int:
    """One fdc ref_food with per-100g nutrient rows."""
    source = session.scalar(select(FoodSource).where(FoodSource.name == "fdc"))
    if source is None:
        source = FoodSource(
            name="fdc",
            version="test",
            licence="public domain",
            citation="test",
        )
        session.add(source)
        session.flush()
    food = RefFood(
        source_id=source.id,
        source_food_id=source_food_id,
        description=description,
    )
    session.add(food)
    session.flush()
    for nutrient, amount, definition in nutrients:
        session.add(
            RefNutrient(
                ref_food_id=food.id,
                nutrient=nutrient,
                amount_per_100g=amount,
                definition=definition,
            )
        )
    return food.id


@pytest.mark.usefixtures("db_engine")
def test_derive_nutrition_sums_confirmed_reference() -> None:
    """Each counted line contributes grams x per-100g; rerunning
    updates the one derived row."""
    with get_session() as session:
        food_id = _add_nutrition_food(
            session,
            "171287",
            "Egg, whole, raw, fresh",
            [
                ("protein_g", 12.56, "by_weight"),
                ("energy_kcal", 143.0, "kcal"),
            ],
        )
        egg = Ingredient(name="egg")
        recipe = RecipeUrls(
            url="https://x/omelette",
            name="Omelette",
            data={},
            site="bbc_good_food",
        )
        session.add_all([egg, recipe])
        session.flush()
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=food_id,
                role="nutrition",
                confirmed=True,
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=0,
                raw_text="1 egg",
                ingredient_id=egg.id,
                quantity=1.0,
                dimension="count",
                base_unit="piece",
                grams=50.0,
                grams_source="seed",
            )
        )
        recipe_id = recipe.id

    derive_nutrition(recipe_id)
    derive_nutrition(recipe_id)
    with get_session() as session:
        rows = session.scalars(
            select(RecipeNutrition).where(RecipeNutrition.recipe_id == recipe_id)
        ).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.source == "derived"
        assert row.protein_g == pytest.approx(50.0 / 100 * 12.56)
        assert row.energy_kcal == pytest.approx(50.0 / 100 * 143.0)
        assert row.coverage == 1.0
        assert row.unconverted_lines == 0
        assert row.sources == "fdc"
        assert row.definitions_mixed is False


@pytest.mark.usefixtures("db_engine")
def test_derive_nutrition_midpoint_and_skips() -> None:
    """Ranges eat the midpoint; no-quantity lines count as
    unconverted; optional and alternative lines are skipped."""
    with get_session() as session:
        food_id = _add_nutrition_food(
            session,
            "171287",
            "Egg, whole, raw, fresh",
            [("protein_g", 10.0, "by_weight")],
        )
        egg = Ingredient(name="egg")
        salt = Ingredient(name="salt")
        recipe = RecipeUrls(
            url="https://x/curry", name="Curry", data={}, site="bbc_good_food"
        )
        session.add_all([egg, salt, recipe])
        session.flush()
        recipe_id = recipe.id
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=food_id,
                role="nutrition",
                confirmed=True,
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=0,
                raw_text="2-3 eggs",
                ingredient_id=egg.id,
                quantity=2.0,
                quantity_max=3.0,
                dimension="count",
                base_unit="piece",
                grams=50.0,
                grams_max=100.0,
                grams_source="seed",
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=1,
                raw_text="salt to serve",
                ingredient_id=salt.id,
                dimension="count",
                base_unit="piece",
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=2,
                raw_text="1 egg (optional)",
                ingredient_id=egg.id,
                quantity=1.0,
                dimension="count",
                base_unit="piece",
                grams=50.0,
                grams_source="seed",
                optional=True,
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=3,
                raw_text="2 tsp vanilla",
                ingredient_id=egg.id,
                quantity=2.0,
                dimension="volume",
                base_unit="ml",
                grams=2.0,
                grams_source="seed",
                alternative_of=1,
            )
        )

    derive_nutrition(recipe_id)
    with get_session() as session:
        row = session.scalars(
            select(RecipeNutrition).where(RecipeNutrition.recipe_id == recipe_id)
        ).one()
        assert row.protein_g == pytest.approx(75.0 / 100 * 10.0)
        assert row.coverage == 1.0
        assert row.unconverted_lines == 1


@pytest.mark.usefixtures("db_engine")
def test_derive_nutrition_coverage_and_definitions() -> None:
    """Unmapped ingredients drag coverage down; nutrients summed from
    differing definitions are flagged."""
    with get_session() as session:
        egg_food = _add_nutrition_food(
            session, "171287", "Egg", [("protein_g", 12.56, "by_weight")]
        )
        flour_food = _add_nutrition_food(
            session, "168936", "Flour", [("protein_g", 10.0, "by_difference")]
        )
        egg = Ingredient(name="egg")
        flour = Ingredient(name="flour")
        water = Ingredient(name="water")
        recipe = RecipeUrls(
            url="https://x/dough", name="Dough", data={}, site="bbc_good_food"
        )
        session.add_all([egg, flour, water, recipe])
        session.flush()
        recipe_id = recipe.id
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=egg_food,
                role="nutrition",
                confirmed=True,
            )
        )
        session.add(
            IngredientFoodRef(
                ingredient_id=flour.id,
                ref_food_id=flour_food,
                role="nutrition",
                confirmed=True,
            )
        )
        foods = ((egg, 100.0), (flour, 100.0), (water, 100.0))
        for position, (ingredient, grams) in enumerate(foods):
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=position,
                    raw_text=ingredient.name,
                    ingredient_id=ingredient.id,
                    quantity=grams,
                    dimension="mass",
                    base_unit="g",
                    grams=grams,
                    grams_source="seed",
                )
            )

    derive_nutrition(recipe_id)
    with get_session() as session:
        row = session.scalars(
            select(RecipeNutrition).where(RecipeNutrition.recipe_id == recipe_id)
        ).one()
        assert row.protein_g == pytest.approx(12.56 + 10.0)
        assert row.coverage == pytest.approx(200.0 / 300.0)
        assert row.unconverted_lines == 1
        assert row.definitions_mixed is True
        assert row.sources == "fdc"
