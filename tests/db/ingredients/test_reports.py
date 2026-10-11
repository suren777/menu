"""Report tests: the hand-seeding candidates from the reference
imports — a report, never an auto-merge."""

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    RecipeIngredient,
    RefFood,
)
from menu.db.ingredients import reports

from .conftest import add_recipe, add_term


@pytest.mark.usefixtures("db_engine")
def test_fdc_id_conflicts_reported_not_merged() -> None:
    """Canonical ingredients sharing an unconfirmed candidate
    reference food with no seed term between them are reported as
    hand-seeding candidates; a group the seed already linked and
    single-member groups are not."""
    with get_session() as session:
        source = FoodSource(
            name="fdc",
            version="test",
            licence="public domain",
            citation="test",
        )
        session.add(source)
        session.flush()
        extract = RefFood(
            source_id=source.id,
            source_food_id="12756",
            description="Almond, vanilla and rosewater extract",
        )
        butter_food = RefFood(
            source_id=source.id,
            source_food_id="173430",
            description="Butter, without salt",
        )
        session.add_all([extract, butter_food])
        session.flush()
        almond = Ingredient(name="almond")
        vanilla = Ingredient(name="vanilla")
        rosewater = Ingredient(name="rosewater")
        butter = Ingredient(name="butter")
        session.add_all([almond, vanilla, rosewater, butter])
        session.flush()
        session.add_all(
            [
                IngredientFoodRef(
                    ingredient_id=almond.id,
                    ref_food_id=extract.id,
                    source_id=extract.source_id,
                    status="candidate",
                    origin="parser",
                ),
                IngredientFoodRef(
                    ingredient_id=vanilla.id,
                    ref_food_id=extract.id,
                    source_id=extract.source_id,
                    status="candidate",
                    origin="parser",
                ),
                IngredientFoodRef(
                    ingredient_id=rosewater.id,
                    ref_food_id=extract.id,
                    source_id=extract.source_id,
                    status="candidate",
                    origin="parser",
                ),
                IngredientFoodRef(
                    ingredient_id=butter.id,
                    ref_food_id=butter_food.id,
                    source_id=butter_food.source_id,
                    status="candidate",
                    origin="parser",
                ),
            ]
        )

    assert reports.fdc_id_conflicts() == [
        (
            "Almond, vanilla and rosewater extract",
            ["almond", "rosewater", "vanilla"],
        )
    ]

    with get_session() as session:
        record = session.scalars(
            select(Ingredient).where(Ingredient.name == "almond")
        ).first()
        assert record is not None
        # The curated seed term "vanilla" -> almond links the pair.
        session.add(
            IngredientTerm(term="vanilla", ingredient_id=record.id, resolution="seed")
        )

    # The curated seed links the pair, so the group is no longer a conflict.
    assert reports.fdc_id_conflicts() == []


@pytest.mark.usefixtures("db_engine")
def test_ref_review_lists_unconfirmed() -> None:
    """The review report lists ingredients without a confirmed
    reference by line count, with the parser candidate; confirmed
    seeds are excluded."""
    recipe_id = add_recipe("https://x/cake")
    with get_session() as session:
        source = FoodSource(
            name="fdc",
            version="test",
            licence="public domain",
            citation="test",
        )
        session.add(source)
        session.flush()
        egg_food = RefFood(
            source_id=source.id,
            source_food_id="171287",
            description="Egg, whole, raw, fresh",
        )
        baobab = RefFood(
            source_id=source.id,
            source_food_id="167806",
            description="Baobab powder",
        )
        session.add_all([egg_food, baobab])
        session.flush()
        egg = Ingredient(name="egg")
        mystery = Ingredient(name="mystery powder")
        session.add_all([egg, mystery])
        session.flush()
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
                ingredient_id=mystery.id,
                ref_food_id=baobab.id,
                source_id=baobab.source_id,
                status="candidate",
                origin="parser",
            )
        )
        egg_term = add_term(session, "egg", egg)
        mystery_term = add_term(session, "mystery powder", mystery)
        session.add_all(
            [
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=0,
                    raw_text="2 eggs",
                    term_id=egg_term.id,
                ),
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=1,
                    raw_text="1 tsp mystery powder",
                    term_id=mystery_term.id,
                ),
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=2,
                    raw_text="more mystery",
                    term_id=mystery_term.id,
                ),
            ]
        )

    assert reports.ref_review() == [(2, "mystery powder", "Baobab powder", ())]


@pytest.mark.usefixtures("db_engine")
def test_ref_review_excludes_rejected_candidates() -> None:
    """A REJECTED_REFS row takes its food out of the report: not shown
    as the candidate, not offered as a text match. The ingredient
    itself stays listed until it has a confirmed reference."""
    recipe_id = add_recipe("https://x/cake")
    with get_session() as session:
        source = FoodSource(
            name="fdc",
            version="test",
            licence="public domain",
            citation="test",
        )
        session.add(source)
        session.flush()
        baobab = RefFood(
            source_id=source.id,
            source_food_id="167806",
            description="Baobab powder",
        )
        session.add(baobab)
        session.flush()
        mystery = Ingredient(name="baobab powder")
        session.add(mystery)
        session.flush()
        session.add(
            IngredientFoodRef(
                ingredient_id=mystery.id,
                ref_food_id=baobab.id,
                source_id=baobab.source_id,
                status="rejected",
                origin="parser",
            )
        )
        mystery_term = add_term(session, "baobab powder", mystery)
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="1 tsp baobab powder",
                term_id=mystery_term.id,
            )
        )

    assert reports.ref_review() == [(1, "baobab powder", None, ())]
