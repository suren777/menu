"""Aggregate tests: folding recipes into shopping-list lines."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    Ingredient,
    IngredientConversion,
    IngredientTerm,
    RecipeIngredient,
)
from menu.db.ingredients import aggregate
from menu.db.ingredients.seed import seed_ingredient_data

from .helpers import add_recipe, add_recipe_row, add_term, store_lines

FIXTURE = Path(__file__).parents[2] / "ingest" / "fixtures" / "bbc_lines.json"


@pytest.mark.usefixtures("db_engine")
def test_aggregate_folds_volume_into_mass_with_density() -> None:
    """Cordon bleu: 100g flour + 2 tbsp flour -> one flour line in grams."""
    with get_session() as session:
        flour = Ingredient(name="flour")
        emmental = Ingredient(name="emmental")
        session.add_all([flour, emmental])
        session.flush()
        session.add(
            IngredientConversion(
                ingredient_id=flour.id,
                kind="density",
                unit=None,
                modifier=None,
                grams=0.6,
                origin="seed",
            )
        )
        flour_term = add_term(session, "flour", flour)
        emmental_term = add_term(session, "emmental", emmental)
        recipe = add_recipe_row(session, "https://x/cordon-bleu", "Cordon bleu")
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="100g plain flour",
                term_id=flour_term.id,
                quantity=100.0,
                dimension="mass",
                base_unit="g",
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=1,
                raw_text="2 tbsp flour",
                term_id=flour_term.id,
                quantity=29.5735,
                dimension="volume",
                base_unit="ml",
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=2,
                raw_text="150g emmental",
                term_id=emmental_term.id,
                quantity=150.0,
                dimension="mass",
                base_unit="g",
            )
        )

    with get_session() as session:
        lines = aggregate.aggregate([recipe_id], session)

    assert len(lines) == 2
    flour_line = next(line for line in lines if line.label == "flour")
    assert flour_line.dimension == "mass"
    assert flour_line.total == pytest.approx(100 + 0.6 * 29.5735)
    emmental_line = next(line for line in lines if line.label == "emmental")
    assert emmental_line.total == 150.0


@pytest.mark.usefixtures("db_engine")
def test_aggregate_skips_optional_and_alternative_lines() -> None:
    with get_session() as session:
        coconut = Ingredient(name="desiccated coconut")
        milk = Ingredient(name="milk")
        session.add_all([coconut, milk])
        session.flush()
        coconut_term = add_term(session, "desiccated coconut", coconut)
        milk_term = add_term(session, "milk", milk)
        recipe = add_recipe_row(session, "https://x/cake", "Cake")
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="45g desiccated coconut (optional)",
                term_id=coconut_term.id,
                quantity=45.0,
                dimension="mass",
                base_unit="g",
                optional=True,
            )
        )
        vanilla = RecipeIngredient(
            recipe_id=recipe_id,
            position=1,
            raw_text="rosewater",
            term_id=milk_term.id,
        )
        session.add(vanilla)
        session.flush()
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=2,
                raw_text="rosewater or vanilla",
                term_id=milk_term.id,
                alternative_of=vanilla.id,
            )
        )
        session.add(
            RecipeIngredient(recipe_id=recipe_id, position=3, raw_text="salt to taste")
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=3,
                raw_text="250ml milk",
                term_id=milk_term.id,
                quantity=250.0,
                dimension="volume",
                base_unit="ml",
            )
        )

    with get_session() as session:
        lines = aggregate.aggregate([recipe_id], session)

    # Resolved lines sort before unresolved ones; the quantified milk
    # line also covers rosewater's bare "as needed" entry.
    assert [(line.label, line.unresolved, line.as_needed) for line in lines] == [
        ("milk", False, False),
        ("salt to taste", True, True),
    ]
    assert lines[0].total == 250.0

    with get_session() as session:
        included = aggregate.aggregate([recipe_id], session, include_optional=True)
    assert any(line.label == "desiccated coconut" for line in included)


@pytest.mark.usefixtures("db_engine")
def test_aggregate_scales_by_servings() -> None:
    with get_session() as session:
        milk = Ingredient(name="milk")
        session.add(milk)
        session.flush()
        milk_term = add_term(session, "milk", milk)
        recipe = add_recipe_row(session, "https://x/1", "X")
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="250ml milk",
                term_id=milk_term.id,
                quantity=250.0,
                dimension="volume",
                base_unit="ml",
            )
        )

    with get_session() as session:
        lines = aggregate.aggregate([recipe_id], session, servings_scale=2.0)

    assert lines[0].total == 500.0


@pytest.mark.usefixtures("db_engine")
def test_variant_recorded_and_keep_variants_splits() -> None:
    """A term remembers which seed variant its lines resolved through;
    keep_variants splits the shopping list back out by variant."""
    with get_session() as session:
        seed_ingredient_data(session)
    basbousa_id = add_recipe("https://x/basbousa")
    shokupan_id = add_recipe("https://x/shokupan")
    store_lines(basbousa_id, {"recipeIngredient": ["240ml warm milk"]})
    store_lines(shokupan_id, {"recipeIngredient": ["500ml whole milk"]})

    with get_session() as session:
        lines = aggregate.aggregate([basbousa_id, shokupan_id], session)
    assert len(lines) == 1
    assert (lines[0].label, lines[0].variant) == ("milk", None)
    # Every line converted (seeded milk density): one gram line.
    assert lines[0].total == pytest.approx((240 + 500) * 1.03)

    with get_session() as session:
        split = aggregate.aggregate(
            [basbousa_id, shokupan_id], session, keep_variants=True
        )
    assert sorted((line.label, line.total) for line in split) == pytest.approx(
        [("milk (warm)", 240 * 1.03), ("milk (whole)", 500 * 1.03)]
    )


@pytest.mark.usefixtures("db_engine")
def test_aggregate_uses_range_upper_end() -> None:
    """ "2-3 onions" buys 3: under-buying a shopping list is worse
    than over-buying it."""
    recipe_id = add_recipe("https://x/curry")

    store_lines(recipe_id, {"recipeIngredient": ["2-3 large onions, sliced"]})

    with get_session() as session:
        lines = aggregate.aggregate([recipe_id], session)

    onion = next(line for line in lines if line.label == "onion")
    assert (onion.dimension, onion.total, onion.base_unit) == ("count", 3.0, "piece")


@pytest.mark.usefixtures("db_engine")
def test_aggregate_uses_grams_when_every_line_has_them() -> None:
    """Every line converted: the group becomes one gram line instead
    of per-dimension sums ("1 egg" + "50 ml egg" -> ~101 g)."""
    with get_session() as session:
        egg = Ingredient(name="egg")
        session.add(egg)
        session.flush()
        egg_term = add_term(session, "egg", egg)
        recipe = add_recipe_row(session, "https://x/omelette", "Omelette")
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
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
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=1,
                raw_text="50 ml egg",
                term_id=egg_term.id,
                quantity=50.0,
                dimension="volume",
                base_unit="ml",
                grams=51.4,
                grams_source="fdc",
            )
        )

    with get_session() as session:
        lines = aggregate.aggregate([recipe_id], session)

    assert len(lines) == 1
    assert (lines[0].label, lines[0].dimension, lines[0].base_unit) == (
        "egg",
        "mass",
        "g",
    )
    assert lines[0].total == pytest.approx(50.0 + 51.4)


@pytest.mark.usefixtures("db_engine")
def test_not_food_line_stored_but_not_shopping() -> None:
    """Equipment the parser calls an ingredient ("12-hole mini bundt
    tin") stays stored on its not_food term but is excluded from the
    shopping list."""
    with get_session() as session:
        seed_ingredient_data(session)
    recipe_id = add_recipe("https://x/bundt")

    store_lines(
        recipe_id,
        {"recipeIngredient": ["12-hole mini bundt tin", "250ml milk"]},
    )

    with get_session() as session:
        tin = session.scalars(
            select(RecipeIngredient).where(
                RecipeIngredient.raw_text == "12-hole mini bundt tin"
            )
        ).one()
        term = session.get(IngredientTerm, tin.term_id)
        assert term is not None
        assert term.resolution == "not_food"
        assert term.ingredient_id is None

    with get_session() as session:
        lines = aggregate.aggregate([recipe_id], session)

    assert [line.label for line in lines] == ["milk"]
    assert lines[0].total == pytest.approx(250 * 1.03)


@pytest.mark.usefixtures("db_engine")
def test_aggregate_real_cached_cordon_bleu() -> None:
    """The real cached cordon bleu recipe: one flour line folding
    100 g + 2 imperial tbsp (density 0.55), one emmental line — from
    lines taken verbatim off the saved pages."""
    with get_session() as session:
        seed_ingredient_data(session)
    recipe_id = add_recipe("https://x/cordon-bleu")
    case = next(
        case
        for case in json.loads(FIXTURE.read_text())
        if "cordon" in str(case["name"]).lower()
    )

    store_lines(recipe_id, {"recipeIngredient": case["lines"]})

    with get_session() as session:
        lines = aggregate.aggregate([recipe_id], session)

    flour = next(line for line in lines if line.label == "flour")
    assert flour.total == pytest.approx(100 + 2 * 17.7581640625 * 0.55)
    emmental = next(line for line in lines if line.label == "emmental")
    assert emmental.total == 150.0
