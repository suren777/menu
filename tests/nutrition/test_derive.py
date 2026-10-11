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
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrition,
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


def _add_term(
    session: Session,
    term: str,
    ingredient: Ingredient,
    *,
    variant: str | None = None,
    resolution: str = "seed",
) -> IngredientTerm:
    """A resolved term for a hand-built line to point at."""
    row = IngredientTerm(
        term=term, ingredient_id=ingredient.id, variant=variant, resolution=resolution
    )
    session.add(row)
    session.flush()
    return row


def _add_nutrition_food(
    session: Session,
    source_food_id: str,
    description: str,
    nutrients: list[tuple[str, float, str]],
) -> RefFood:
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
    return food


def _add_recipe(session: Session, url: str, name: str) -> Recipe:
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
    )
    session.add(recipe)
    session.flush()
    return recipe


@pytest.mark.usefixtures("db_engine")
def test_derive_nutrition_sums_confirmed_reference() -> None:
    """Each counted line contributes grams x per-100g; rerunning
    updates the one derived row."""
    with get_session() as session:
        food = _add_nutrition_food(
            session,
            "171287",
            "Egg, whole, raw, fresh",
            [
                ("protein_g", 12.56, "by_weight"),
                ("energy_kcal", 143.0, "kcal"),
            ],
        )
        egg = Ingredient(name="egg")
        session.add(egg)
        recipe = _add_recipe(session, "https://x/omelette", "Omelette")
        recipe_id = recipe.id
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
        egg_term = _add_term(session, "egg", egg)
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=0,
                raw_text="1 egg",
                term_id=egg_term.id,
                quantity=1.0,
                dimension="count",
                base_unit="piece",
                grams=50.0,
                grams_source="seed",
            )
        )

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
        food = _add_nutrition_food(
            session,
            "171287",
            "Egg, whole, raw, fresh",
            [("protein_g", 10.0, "by_weight")],
        )
        egg = Ingredient(name="egg")
        salt = Ingredient(name="salt")
        session.add_all([egg, salt])
        recipe = _add_recipe(session, "https://x/curry", "Curry")
        recipe_id = recipe.id
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
        egg_term = _add_term(session, "egg", egg)
        salt_term = _add_term(session, "salt", salt)
        eggs_row = RecipeIngredient(
            recipe_id=recipe.id,
            position=0,
            raw_text="2-3 eggs",
            term_id=egg_term.id,
            quantity=2.0,
            quantity_max=3.0,
            dimension="count",
            base_unit="piece",
            grams=50.0,
            grams_max=100.0,
            grams_source="seed",
        )
        session.add(eggs_row)
        session.flush()
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=1,
                raw_text="salt to serve",
                term_id=salt_term.id,
                dimension="count",
                base_unit="piece",
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=2,
                raw_text="1 egg (optional)",
                term_id=egg_term.id,
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
                term_id=egg_term.id,
                quantity=2.0,
                dimension="volume",
                base_unit="ml",
                grams=2.0,
                grams_source="seed",
                alternative_of=eggs_row.id,
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
        session.add_all([egg, flour, water])
        recipe = _add_recipe(session, "https://x/dough", "Dough")
        recipe_id = recipe.id
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=egg_food.id,
                source_id=egg_food.source_id,
                role="nutrition",
                status="confirmed",
                origin="seed",
            )
        )
        session.add(
            IngredientFoodRef(
                ingredient_id=flour.id,
                ref_food_id=flour_food.id,
                source_id=flour_food.source_id,
                role="nutrition",
                status="confirmed",
                origin="seed",
            )
        )
        egg_term = _add_term(session, "egg", egg)
        flour_term = _add_term(session, "flour", flour)
        water_term = _add_term(session, "water", water)
        for position, (term, grams) in enumerate(
            ((egg_term, 100.0), (flour_term, 100.0), (water_term, 100.0))
        ):
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=position,
                    raw_text=term.term,
                    term_id=term.id,
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


@pytest.mark.usefixtures("db_engine")
def test_derive_nutrition_uses_prepared_form() -> None:
    """A line that resolved through the "yolk" variant term takes the
    yolk food's nutrients; a plain egg line keeps the whole egg's."""
    with get_session() as session:
        whole = _add_nutrition_food(
            session,
            "171287",
            "Egg, whole, raw, fresh",
            [("protein_g", 12.56, "by_weight")],
        )
        _add_nutrition_food(
            session,
            "172184",
            "Egg, yolk, raw, fresh",
            [("protein_g", 15.86, "by_weight")],
        )
        egg = Ingredient(name="egg")
        session.add(egg)
        recipe = _add_recipe(session, "https://x/custard", "Custard")
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=whole.id,
                source_id=whole.source_id,
                role="nutrition",
                status="confirmed",
                origin="seed",
            )
        )
        yolk_term = _add_term(session, "egg yolk", egg, variant="yolk")
        egg_term = _add_term(session, "egg", egg)
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=0,
                raw_text="2 large egg yolks",
                term_id=yolk_term.id,
                quantity=2.0,
                dimension="count",
                base_unit="piece",
                size="large",
                grams=34.0,
                grams_source="seed",
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                position=1,
                raw_text="1 egg",
                term_id=egg_term.id,
                quantity=1.0,
                dimension="count",
                base_unit="piece",
                grams=50.0,
                grams_source="seed",
            )
        )
        recipe_id = recipe.id

    derive_nutrition(recipe_id)
    with get_session() as session:
        row = session.scalars(
            select(RecipeNutrition).where(RecipeNutrition.recipe_id == recipe_id)
        ).one()
        assert row.protein_g == pytest.approx(34.0 / 100 * 15.86 + 50.0 / 100 * 12.56)
        assert row.sources == "fdc"


@pytest.mark.usefixtures("db_engine")
def test_derive_nutrition_skips_not_food_lines() -> None:
    """Equipment lines ("12-hole mini bundt tin" on its not_food term)
    stay stored but are not food: excluded from the totals and the
    coverage denominator."""
    with get_session() as session:
        food = _add_nutrition_food(
            session,
            "171287",
            "Egg, whole, raw, fresh",
            [("protein_g", 12.56, "by_weight")],
        )
        egg = Ingredient(name="egg")
        session.add(egg)
        recipe = _add_recipe(session, "https://x/bundt", "Bundt")
        recipe_id = recipe.id
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
        tin_term = IngredientTerm(term="12-hole mini bundt tin", resolution="not_food")
        session.add(tin_term)
        egg_term = _add_term(session, "egg", egg)
        session.flush()
        session.add_all(
            [
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=0,
                    raw_text="12-hole mini bundt tin",
                    term_id=tin_term.id,
                    quantity=1.0,
                    dimension="count",
                    base_unit="piece",
                    grams=100.0,
                    grams_source="seed",
                ),
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=1,
                    raw_text="1 egg",
                    term_id=egg_term.id,
                    quantity=1.0,
                    dimension="count",
                    base_unit="piece",
                    grams=50.0,
                    grams_source="seed",
                ),
            ]
        )

    derive_nutrition(recipe_id)
    with get_session() as session:
        row = session.scalars(
            select(RecipeNutrition).where(RecipeNutrition.recipe_id == recipe_id)
        ).one()
        assert row.protein_g == pytest.approx(50.0 / 100 * 12.56)
        assert row.coverage == 1.0
        assert row.unconverted_lines == 0
