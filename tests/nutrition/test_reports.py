"""Nutrition report tests."""

from typing import TYPE_CHECKING

import pytest

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrition,
    RefFood,
)
from menu.nutrition.reports import coverage_report, validation_report

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def add_term(session: Session, term: str, ingredient: Ingredient) -> IngredientTerm:
    """A resolved term for a hand-built line to point at."""
    row = IngredientTerm(
        term=term, ingredient_id=ingredient.id, variant=None, resolution="seed"
    )
    session.add(row)
    session.flush()
    return row


def _add_recipe(
    session: Session, url: str, name: str, servings: int | None = None
) -> Recipe:
    """A bare recipe row, reached through its page (the ledger)."""
    page = Page(url=url, site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id,
        name=name,
        data={},
        data_sha256=url,
        extractor="json_ld",
        servings=servings,
    )
    session.add(recipe)
    session.flush()
    return recipe


@pytest.mark.usefixtures("db_engine")
def test_validation_report_compares_per_serving(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Derived totals are divided by servings before comparing with
    the published per-serving values."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/omelette", "Omelette", servings=2)
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
        recipe = _add_recipe(session, "https://x/soup", "Soup", servings=2)
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
def test_validation_report_skips_zero_servings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Some BBC recipes publish a yield of 0; there is no per-serving
    value to compare, so they are skipped rather than divided by."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/stock", "Stock", servings=0)
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
        session.add_all([egg, water])
        recipe = _add_recipe(session, "https://x/x", "X")
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=food.id,
                source_id=food.source_id,
                role="nutrition",
                status="confirmed",
                origin="seed",
            )
        )
        egg_term = add_term(session, "egg", egg)
        water_term = add_term(session, "water", water)
        foods = ((egg_term, 2.0), (egg_term, 1.0), (water_term, 250.0))
        for position, (term, quantity) in enumerate(foods):
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=position,
                    raw_text=term.term,
                    term_id=term.id,
                    quantity=quantity,
                    dimension="count" if term is egg_term else "volume",
                    base_unit="piece" if term is egg_term else "ml",
                )
            )

    coverage_report()
    out = capsys.readouterr().out
    assert "bbc_good_food:" in out
    assert "water" in out
    assert "egg" not in out
