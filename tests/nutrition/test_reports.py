"""Nutrition report tests."""

import pytest

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    RecipeIngredient,
    RecipeNutrition,
    RecipeUrls,
    RefFood,
)
from menu.nutrition.reports import coverage_report, validation_report


@pytest.mark.usefixtures("db_engine")
def test_validation_report_compares_per_serving(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Derived totals are divided by servings before comparing with
    the published per-serving values."""
    with get_session() as session:
        recipe = RecipeUrls(
            url="https://x/omelette",
            name="Omelette",
            data={},
            site="bbc_good_food",
            servings=2,
        )
        session.add(recipe)
        session.flush()
        session.add(
            RecipeNutrition(
                recipe_id=recipe.id, source="published", energy_kcal=100.0
            )
        )
        session.add(
            RecipeNutrition(
                recipe_id=recipe.id,
                source="derived",
                energy_kcal=250.0,
                coverage=0.95,
                sources="fdc",
            )
        )

    validation_report()
    out = capsys.readouterr().out
    assert "bbc_good_food via fdc: 1 recipes" in out
    assert "25%" in out
    assert "Worst recipes by kcal error:" in out


@pytest.mark.usefixtures("db_engine")
def test_validation_report_skips_low_coverage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A derivation covering under 90% of the grams is not compared —
    low coverage is marked, not trusted."""
    with get_session() as session:
        recipe = RecipeUrls(
            url="https://x/soup",
            name="Soup",
            data={},
            site="bbc_good_food",
            servings=2,
        )
        session.add(recipe)
        session.flush()
        session.add(
            RecipeNutrition(
                recipe_id=recipe.id, source="published", energy_kcal=100.0
            )
        )
        session.add(
            RecipeNutrition(
                recipe_id=recipe.id,
                source="derived",
                energy_kcal=250.0,
                coverage=0.5,
            )
        )

    validation_report()
    assert "bbc_good_food" not in capsys.readouterr().out


@pytest.mark.usefixtures("db_engine")
def test_coverage_report_lists_unreferenced_ingredients(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ingredients with a confirmed nutrition reference are not
    listed; unreferenced ones are grouped by site."""
    with get_session() as session:
        source = FoodSource(
            name="fdc",
            version="test",
            licence="public domain",
            citation="test",
        )
        session.add(source)
        session.flush()
        food = RefFood(
            source_id=source.id, source_food_id="171287", description="Egg"
        )
        session.add(food)
        session.flush()
        egg = Ingredient(name="egg")
        water = Ingredient(name="water")
        recipe = RecipeUrls(
            url="https://x/x", name="X", data={}, site="bbc_good_food"
        )
        session.add_all([egg, water, recipe])
        session.flush()
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=food.id,
                role="nutrition",
                confirmed=True,
            )
        )
        foods = ((egg, 2.0), (egg, 1.0), (water, 250.0))
        for position, (ingredient, quantity) in enumerate(foods):
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=position,
                    raw_text=ingredient.name,
                    ingredient_id=ingredient.id,
                    quantity=quantity,
                    dimension="count" if ingredient is egg else "volume",
                    base_unit="piece" if ingredient is egg else "ml",
                )
            )

    coverage_report()
    out = capsys.readouterr().out
    assert "bbc_good_food:" in out
    assert "water" in out
    assert "egg" not in out
