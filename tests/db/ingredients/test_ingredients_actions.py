"""Action tests: storing parsed lines and aggregating a shopping list."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientAlias,
    IngredientFoodRef,
    RecipeIngredient,
    RecipeUrls,
    RefFood,
    RefPortion,
)
from menu.db.ingredients import actions, repository
from menu.db.ingredients.seed import seed_ingredient_data
from menu.ingest.ingredients import parse_line
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

FIXTURE = Path(__file__).parents[2] / "ingest" / "fixtures" / "bbc_lines.json"

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from menu.ingest.ingredients import ParsedLine
    from menu.ingest.registry import SiteConfig


def add_recipe(url: str) -> int:
    """Insert a bare recipe row; the action opens its own session, so
    the seed must be committed before it runs."""
    with get_session() as session:
        recipe = RecipeUrls(url=url, name="Test", data={})
        session.add(recipe)
        session.flush()
        return recipe.id


def _egg() -> repository.IngredientModel:
    return repository.IngredientModel(
        id=1, name="egg", density_g_per_ml=None, unit_weight_g=50.0
    )


def _egg_portions() -> list[RefPortion]:
    """The egg portion rows the FDC import stores: sizes plus the cup."""
    return [
        RefPortion(
            ref_food_id=1,
            seq_num=1,
            amount=1.0,
            unit="small",
            modifier="small",
            gram_weight=38.0,
        ),
        RefPortion(
            ref_food_id=1,
            seq_num=2,
            amount=1.0,
            unit="medium",
            modifier="medium",
            gram_weight=44.0,
        ),
        RefPortion(
            ref_food_id=1,
            seq_num=3,
            amount=1.0,
            unit="large",
            modifier="large",
            gram_weight=50.0,
        ),
        RefPortion(
            ref_food_id=1,
            seq_num=4,
            amount=1.0,
            unit="cup (4.86 large eggs)",
            modifier="cup (4.86 large eggs)",
            gram_weight=243.0,
        ),
    ]


def test_to_grams_count_uses_size_matched_portion() -> None:
    """1 large egg -> 50 g, 2 medium eggs -> 88 g: the portion
    matching the line's size beats the seeded default."""
    egg = _egg()
    assert (
        actions.to_grams(
            1.0, None, "count", "piece", None, None, "large", egg,
            _egg_portions(), "fdc",
        )
        == (50.0, None, "fdc")
    )
    assert (
        actions.to_grams(
            2.0, None, "count", "piece", None, None, "medium", egg,
            _egg_portions(), "fdc",
        )
        == (88.0, None, "fdc")
    )


def test_to_grams_count_falls_back_to_seed_weight() -> None:
    """No size: the seeded unit weight converts ("2 eggs" -> 100 g)."""
    assert (
        actions.to_grams(
            2.0, None, "count", "piece", None, None, None, _egg(),
            _egg_portions(), "fdc",
        )
        == (100.0, None, "seed")
    )


def test_to_grams_volume_uses_unit_portion() -> None:
    """1 cup egg -> 243 g through the "cup (4.86 large eggs)" row."""
    assert (
        actions.to_grams(
            1.0, None, "volume", "ml", "cup", None, None, _egg(),
            _egg_portions(), "fdc",
        )
        == (243.0, None, "fdc")
    )


def test_to_grams_volume_seed_density_beats_implied() -> None:
    """A seed beats the reference: 100 ml of a 2.0 g/ml seed is
    200 g even though the cup portion implies less."""
    cup = RefPortion(
        ref_food_id=2,
        seq_num=1,
        amount=1.0,
        unit="cup",
        modifier="cup",
        gram_weight=200.0,
    )
    watery = repository.IngredientModel(
        id=2, name="watery", density_g_per_ml=2.0, unit_weight_g=None
    )
    assert (
        actions.to_grams(
            100.0, None, "volume", "ml", "ml", None, None, watery, [cup], "fdc"
        )
        == (200.0, None, "seed")
    )


def test_to_grams_volume_implied_density() -> None:
    """A pinch of salt converts through the density its teaspoon
    portion implies."""
    tsp = RefPortion(
        ref_food_id=3,
        seq_num=1,
        amount=1.0,
        unit="tsp",
        modifier="tsp",
        gram_weight=6.0,
    )
    salt = repository.IngredientModel(
        id=3, name="salt", density_g_per_ml=None, unit_weight_g=None
    )
    # "a pinch of salt" stores quantity 0.36 ml (the fixed amount
    # units._FIXED_AMOUNTS assigns a pinch), then converts via the
    # density the tsp portion implies.
    grams, grams_max, source = actions.to_grams(
        0.36, None, "volume", "ml", "pinch", None, None, salt, [tsp], "fdc"
    )
    assert grams == pytest.approx(0.36 * 6.0 / 4.928921610938, rel=1e-3)
    assert grams_max is None
    assert source == "fdc"


def test_to_grams_named_count_unit() -> None:
    """2 sticks of butter convert through the stick portion."""
    stick = RefPortion(
        ref_food_id=4,
        seq_num=1,
        amount=1.0,
        unit="stick",
        modifier="stick",
        gram_weight=113.0,
    )
    butter = repository.IngredientModel(
        id=4, name="butter", density_g_per_ml=0.96, unit_weight_g=None
    )
    assert (
        actions.to_grams(
            2.0, None, "count", "stick", "sticks", None, None, butter,
            [stick], "fdc",
        )
        == (226.0, None, "fdc")
    )


def test_to_grams_never_guesses() -> None:
    """No portion, no seed: grams stays NULL."""
    garlic = repository.IngredientModel(
        id=5, name="garlic", density_g_per_ml=None, unit_weight_g=None
    )
    assert (
        actions.to_grams(
            2.0, None, "count", "piece", None, None, None, garlic, [], "fdc"
        )
        == (None, None, None)
    )


def test_to_grams_ranges_and_mass() -> None:
    """Ranges convert the upper end too; mass lines need no lookup."""
    flour = repository.IngredientModel(
        id=6, name="flour", density_g_per_ml=0.55, unit_weight_g=None
    )
    cup = RefPortion(
        ref_food_id=7,
        seq_num=1,
        amount=1.0,
        unit="cup",
        modifier="cup",
        gram_weight=125.0,
    )
    assert (
        actions.to_grams(
            2.0, 3.0, "volume", "ml", "cups", None, None, flour, [cup], "fdc"
        )
        == (250.0, 375.0, "fdc")
    )
    assert (
        actions.to_grams(
            100.0, 120.0, "mass", "g", "g", None, None, flour, [], "fdc"
        )
        == (100.0, 120.0, None)
    )


@pytest.mark.usefixtures("db_engine")
def test_store_writes_grams_from_confirmed_ref() -> None:
    """Storing a line converts it to grams through the ingredient's
    confirmed conversion reference."""
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
        food = RefFood(
            source_id=source.id,
            source_food_id="171287",
            description="Egg, whole, raw, fresh",
        )
        session.add(food)
        session.flush()
        session.add(
            RefPortion(
                ref_food_id=food.id,
                seq_num=1,
                amount=1.0,
                unit="medium",
                modifier="medium",
                gram_weight=44.0,
            )
        )
        egg = Ingredient(name="egg")
        session.add(egg)
        session.flush()
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=food.id,
                role="conversion",
                confirmed=True,
            )
        )

    actions.store_recipe_ingredients(
        "https://x/cake", {"recipeIngredient": ["2 medium eggs"]}, BBC_GOOD_FOOD
    )
    with get_session() as session:
        line = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).first()
        assert line is not None
        assert line.grams == 88.0
        assert line.grams_max is None
        assert line.grams_source == "fdc"


@pytest.mark.usefixtures("db_engine")
def test_store_recipe_ingredients() -> None:
    add_recipe("https://x/pancakes")
    data = {
        "recipeIngredient": ["240ml warm milk", "2 eggs, beaten", "oil, for proving"],
        "recipeYield": "Serves 12",
    }

    actions.store_recipe_ingredients("https://x/pancakes", data, BBC_GOOD_FOOD)

    with get_session() as session:
        recipe = session.scalars(
            select(RecipeUrls).where(RecipeUrls.url == "https://x/pancakes")
        ).first()
        assert recipe is not None
        assert (recipe.site, recipe.servings) == ("bbc_good_food", 12)
        lines = session.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe.id)
            .order_by(RecipeIngredient.position)
        ).all()
        assert [line.raw_text for line in lines] == [
            "240ml warm milk",
            "2 eggs, beaten",
            "oil, for proving",
        ]
        milk, eggs, oil = lines
        assert (milk.quantity, milk.dimension, milk.base_unit) == (
            240.0,
            "volume",
            "ml",
        )
        assert (eggs.quantity, eggs.dimension, eggs.base_unit) == (
            2.0,
            "count",
            "piece",
        )
        assert eggs.preparation == "beaten"
        assert oil.quantity is None
        assert oil.dimension is None
        assert oil.note == "for proving"
        assert all(line.ingredient_id is not None for line in lines)
        assert all(line.parse_confidence is not None for line in lines)


@pytest.mark.usefixtures("db_engine")
def test_reparse_replaces_rows() -> None:
    add_recipe("https://x/pancakes")
    actions.store_recipe_ingredients(
        "https://x/pancakes",
        {"recipeIngredient": ["240ml warm milk", "100g butter"]},
        BBC_GOOD_FOOD,
    )

    actions.store_recipe_ingredients(
        "https://x/pancakes", {"recipeIngredient": ["2 eggs"]}, BBC_GOOD_FOOD
    )

    with get_session() as session:
        lines = session.scalars(select(RecipeIngredient)).all()
        assert [line.raw_text for line in lines] == ["2 eggs"]


@pytest.mark.usefixtures("db_engine")
def test_alternatives_stored_as_separate_rows() -> None:
    recipe_id = add_recipe("https://x/cake")

    actions.store_recipe_ingredients(
        "https://x/cake",
        {"recipeIngredient": ["rosewater or vanilla extract"]},
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.id)
        ).all()
        assert len(lines) == 2
        primary, alternative = lines
        assert primary.alternative_of is None
        assert alternative.alternative_of == primary.id


@pytest.mark.usefixtures("db_engine")
def test_bad_line_does_not_stop_the_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def flaky(text: str, site: SiteConfig) -> ParsedLine:
        if "bad" in text:
            raise ValueError("nope")
        return parse_line(text, site)

    monkeypatch.setattr(actions, "parse_line", flaky)
    add_recipe("https://x/pancakes")

    actions.store_recipe_ingredients(
        "https://x/pancakes",
        {"recipeIngredient": ["240ml warm milk", "bad line", "2 eggs"]},
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).order_by(RecipeIngredient.position)
        ).all()
        assert [line.raw_text for line in lines] == ["240ml warm milk", "2 eggs"]


@pytest.mark.usefixtures("db_engine")
def test_aggregate_folds_volume_into_mass_with_density() -> None:
    """Cordon bleu: 100g flour + 2 tbsp flour -> one flour line in grams."""
    with get_session() as session:
        flour = Ingredient(name="flour", density_g_per_ml=0.6)
        emmental = Ingredient(name="emmental")
        recipe = RecipeUrls(url="https://x/cordon-bleu", name="Cordon bleu", data={})
        session.add_all([flour, emmental, recipe])
        session.flush()
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="100g plain flour",
                ingredient_id=flour.id,
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
                ingredient_id=flour.id,
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
                ingredient_id=emmental.id,
                quantity=150.0,
                dimension="mass",
                base_unit="g",
            )
        )

    lines = actions.aggregate([recipe_id])

    assert len(lines) == 2
    flour_line = next(line for line in lines if line.label == "flour")
    assert flour_line.dimension == "mass"
    assert flour_line.total == pytest.approx(100 + 0.6 * 29.5735)
    emmental_line = next(line for line in lines if line.label == "emmental")
    assert emmental_line.total == 150.0


@pytest.mark.usefixtures("db_engine")
def test_aggregate_rolls_alias_variants_up() -> None:
    """Basbousa's warm milk + Shokupan's whole milk -> one milk line."""
    with get_session() as session:
        milk = Ingredient(name="milk")
        session.add(milk)
        session.flush()
        session.add(IngredientAlias(alias="warm milk", ingredient_id=milk.id))
        session.add(IngredientAlias(alias="whole milk", ingredient_id=milk.id))

    basbousa_id = add_recipe("https://x/basbousa")
    shokupan_id = add_recipe("https://x/shokupan")
    actions.store_recipe_ingredients(
        "https://x/basbousa", {"recipeIngredient": ["240ml warm milk"]}, BBC_GOOD_FOOD
    )
    actions.store_recipe_ingredients(
        "https://x/shokupan", {"recipeIngredient": ["500ml whole milk"]}, BBC_GOOD_FOOD
    )

    lines = actions.aggregate([basbousa_id, shokupan_id])

    assert len(lines) == 1
    assert (lines[0].label, lines[0].total, lines[0].base_unit) == ("milk", 740.0, "ml")


@pytest.mark.usefixtures("db_engine")
def test_aggregate_skips_optional_and_alternative_lines() -> None:
    with get_session() as session:
        coconut = Ingredient(name="desiccated coconut")
        milk = Ingredient(name="milk")
        recipe = RecipeUrls(url="https://x/cake", name="Cake", data={})
        session.add_all([coconut, milk, recipe])
        session.flush()
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="45g desiccated coconut (optional)",
                ingredient_id=coconut.id,
                quantity=45.0,
                dimension="mass",
                base_unit="g",
                optional=True,
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=1,
                raw_text="rosewater or vanilla",
                ingredient_id=milk.id,
                alternative_of=999,
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id, position=2, raw_text="salt to taste"
            )
        )
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=3,
                raw_text="250ml milk",
                ingredient_id=milk.id,
                quantity=250.0,
                dimension="volume",
                base_unit="ml",
            )
        )

    lines = actions.aggregate([recipe_id])

    # Resolved lines sort before unresolved ones.
    assert [(line.label, line.unresolved, line.as_needed) for line in lines] == [
        ("milk", False, False),
        ("salt to taste", True, True),
    ]
    assert lines[0].total == 250.0

    included = actions.aggregate([recipe_id], include_optional=True)
    assert any(line.label == "desiccated coconut" for line in included)


@pytest.mark.usefixtures("db_engine")
def test_aggregate_scales_by_servings() -> None:
    with get_session() as session:
        milk = Ingredient(name="milk")
        recipe = RecipeUrls(url="https://x/1", name="X", data={})
        session.add_all([milk, recipe])
        session.flush()
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="250ml milk",
                ingredient_id=milk.id,
                quantity=250.0,
                dimension="volume",
                base_unit="ml",
            )
        )

    lines = actions.aggregate([recipe_id], servings_scale=2.0)

    assert lines[0].total == 500.0


@pytest.mark.usefixtures("db_engine")
def test_servings_parsed_from_yield_variants() -> None:
    """recipeYield is messy: "Makes 1 loaf" and plain 6."""
    for url, data in (
        ("https://x/a", {"recipeYield": "Makes 1 loaf"}),
        ("https://x/b", {"recipeYield": 6}),
    ):
        add_recipe(url)
        actions.store_recipe_ingredients(url, data, BBC_GOOD_FOOD)

    with get_session() as session:
        yields = {
            recipe.url: recipe.servings
            for recipe in session.scalars(select(RecipeUrls))
        }
    assert yields["https://x/a"] == 1
    assert yields["https://x/b"] == 6


@pytest.mark.usefixtures("db_engine")
def test_review_queue_catches_mangled_names() -> None:
    """Names the parser likely mangled (long modifier chains, unsplit
    conjunctions) stay unresolved for review instead of becoming
    canonical ingredients."""
    recipe_id = add_recipe("https://x/cake")

    actions.store_recipe_ingredients(
        "https://x/cake",
        {"recipeIngredient": ["200g good-quality candied orange and lemon peel"]},
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        assert len(lines) == 1
        assert lines[0].ingredient_id is None
        assert lines[0].raw_text == "200g good-quality candied orange and lemon peel"


@pytest.mark.usefixtures("db_engine")
def test_lost_conjunction_goes_to_review_queue() -> None:
    """"pink and yellow food colouring gels" parses as "pink food
    colouring gel" with "yellow food colouring gel" as an alternative —
    but a shopping list needs both gels, so the line stays unresolved
    for review."""
    recipe_id = add_recipe("https://x/cake")

    actions.store_recipe_ingredients(
        "https://x/cake",
        {"recipeIngredient": ["pink and yellow food colouring gels"]},
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        assert primary.ingredient_id is None


@pytest.mark.usefixtures("db_engine")
def test_seeded_alias_rescues_lost_conjunction() -> None:
    """A seeded alias wins over the lost-conjunction heuristic, like it
    does over the mangled-name one."""
    with get_session() as session:
        ingredient = Ingredient(name="food colouring gel")
        session.add(ingredient)
        session.flush()
        session.add(
            IngredientAlias(
                alias="pink food colouring gel", ingredient_id=ingredient.id
            )
        )
    recipe_id = add_recipe("https://x/cake")

    actions.store_recipe_ingredients(
        "https://x/cake",
        {"recipeIngredient": ["pink and yellow food colouring gels"]},
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        assert primary.ingredient_id is not None
        resolved = session.get(Ingredient, primary.ingredient_id)
        assert resolved is not None
        assert resolved.name == "food colouring gel"


@pytest.mark.usefixtures("db_engine")
def test_fdc_id_conflicts_reported_not_merged() -> None:
    """Canonical ingredients sharing an unconfirmed candidate
    reference food with no alias between them are reported as
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
                IngredientFoodRef(ingredient_id=almond.id, ref_food_id=extract.id),
                IngredientFoodRef(ingredient_id=vanilla.id, ref_food_id=extract.id),
                IngredientFoodRef(
                    ingredient_id=rosewater.id, ref_food_id=extract.id
                ),
                IngredientFoodRef(
                    ingredient_id=butter.id, ref_food_id=butter_food.id
                ),
            ]
        )

    assert actions.fdc_id_conflicts() == [
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
        session.add(IngredientAlias(alias="vanilla", ingredient_id=record.id))

    # The curated alias links the pair, so the group is no longer a conflict.
    assert actions.fdc_id_conflicts() == []


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
                role="nutrition",
                confirmed=True,
            )
        )
        session.add(
            IngredientFoodRef(ingredient_id=mystery.id, ref_food_id=baobab.id)
        )
        session.add_all(
            [
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=0,
                    raw_text="2 eggs",
                    ingredient_id=egg.id,
                ),
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=1,
                    raw_text="1 tsp mystery powder",
                    ingredient_id=mystery.id,
                ),
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=2,
                    raw_text="more mystery",
                    ingredient_id=mystery.id,
                ),
            ]
        )

    assert actions.ref_review() == [(2, "mystery powder", "Baobab powder", ())]


@pytest.mark.usefixtures("db_engine")
def test_seeded_alias_rescues_mangled_name() -> None:
    """A seeded alias wins over the review heuristic: the real cached
    line "30g whole blanched almonds roughly chopped or flaked almonds"
    parses to the name "whole blanched almonds roughly chopped" and
    resolves to blanched almond instead of sitting unresolved."""
    with get_session() as session:
        seed_ingredient_data(session)
    recipe_id = add_recipe("https://x/cake")

    actions.store_recipe_ingredients(
        "https://x/cake",
        {
            "recipeIngredient": [
                "30g whole blanched almonds roughly chopped or flaked almonds"
            ]
        },
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        assert primary.ingredient_id is not None
        ingredient = session.get(Ingredient, primary.ingredient_id)
        assert ingredient is not None
        assert ingredient.name == "blanched almond"


@pytest.mark.usefixtures("db_engine")
def test_line_override_fixes_wrong_merge() -> None:
    """"70g  milk or dark chocolate roughly chopped (optional)" — the
    real cached line, double space and all — parses as name "milk"
    with confidence 1.0. No threshold catches it, only the curated
    line override does, and the override matches the squashed line."""
    recipe_id = add_recipe("https://x/babka")

    actions.store_recipe_ingredients(
        "https://x/babka",
        {
            "recipeIngredient": [
                "70g  milk or dark chocolate roughly chopped (optional)"
            ]
        },
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        assert primary.ingredient_id is not None
        ingredient = session.get(Ingredient, primary.ingredient_id)
        assert ingredient is not None
        assert ingredient.name == "milk chocolate"


@pytest.mark.usefixtures("db_engine")
def test_quantity_level_or_alternative_kept() -> None:
    """"2 tsp vanilla or 1 tsp essential oil": the parser drops the
    second option into the note; splitting on "or" keeps it as an
    alternative row with its own quantity."""
    recipe_id = add_recipe("https://x/cake")

    actions.store_recipe_ingredients(
        "https://x/cake",
        {"recipeIngredient": ["2 tsp vanilla or 1 tsp essential oil"]},
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.id)
        ).all()
        assert len(lines) == 2
        primary, alternative = lines
        assert primary.alternative_of is None
        assert alternative.alternative_of == primary.id
        assert alternative.quantity == pytest.approx(5.919388020833333)
        assert alternative.dimension == "volume"


@pytest.mark.usefixtures("db_engine")
def test_nameless_or_remainders_not_stored() -> None:
    """Bracketed parentheticals and nameless remainders — "(or 3
    small)", "or to taste", "or more if needed" — are not alternatives;
    storing them would flood the review queue with nameless rows."""
    recipe_id = add_recipe("https://x/curry")

    actions.store_recipe_ingredients(
        "https://x/curry",
        {
            "recipeIngredient": [
                "2 large eggs (or 3 small)",
                "1 tsp salt or to taste",
                "1 tbsp milk, or more if needed",
            ]
        },
        BBC_GOOD_FOOD,
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        assert len(lines) == 3
        assert all(line.alternative_of is None for line in lines)


@pytest.mark.usefixtures("db_engine")
def test_variant_recorded_and_keep_variants_splits() -> None:
    """A line remembers which alias variant it resolved through;
    keep_variants splits the shopping list back out by variant."""
    with get_session() as session:
        seed_ingredient_data(session)
    basbousa_id = add_recipe("https://x/basbousa")
    shokupan_id = add_recipe("https://x/shokupan")
    actions.store_recipe_ingredients(
        "https://x/basbousa", {"recipeIngredient": ["240ml warm milk"]}, BBC_GOOD_FOOD
    )
    actions.store_recipe_ingredients(
        "https://x/shokupan", {"recipeIngredient": ["500ml whole milk"]}, BBC_GOOD_FOOD
    )

    lines = actions.aggregate([basbousa_id, shokupan_id])
    assert len(lines) == 1
    assert (lines[0].label, lines[0].variant) == ("milk", None)
    # Every line converted (seeded milk density): one gram line
    # replaces the old 740 ml volume sum.
    assert lines[0].total == pytest.approx((240 + 500) * 1.03)

    split = actions.aggregate([basbousa_id, shokupan_id], keep_variants=True)
    assert sorted((line.label, line.total) for line in split) == pytest.approx(
        [("milk (warm)", 240 * 1.03), ("milk (whole)", 500 * 1.03)]
    )


@pytest.mark.usefixtures("db_engine")
def test_aggregate_uses_range_upper_end() -> None:
    """"2-3 onions" buys 3: under-buying a shopping list is worse
    than over-buying it."""
    recipe_id = add_recipe("https://x/curry")

    actions.store_recipe_ingredients(
        "https://x/curry",
        {"recipeIngredient": ["2-3 large onions, sliced"]},
        BBC_GOOD_FOOD,
    )

    lines = actions.aggregate([recipe_id])

    onion = next(line for line in lines if line.label == "onion")
    assert (onion.dimension, onion.total, onion.base_unit) == ("count", 3.0, "piece")


@pytest.mark.usefixtures("db_engine")
def test_aggregate_uses_grams_when_every_line_has_them() -> None:
    """Every line converted: the group becomes one gram line instead
    of per-dimension sums ("1 egg" + "50 ml egg" -> ~101 g)."""
    with get_session() as session:
        egg = Ingredient(name="egg")
        recipe = RecipeUrls(url="https://x/omelette", name="Omelette", data={})
        session.add_all([egg, recipe])
        session.flush()
        recipe_id = recipe.id
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
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
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=1,
                raw_text="50 ml egg",
                ingredient_id=egg.id,
                quantity=50.0,
                dimension="volume",
                base_unit="ml",
                grams=51.4,
                grams_source="fdc",
            )
        )

    lines = actions.aggregate([recipe_id])

    assert len(lines) == 1
    assert (lines[0].label, lines[0].dimension, lines[0].base_unit) == (
        "egg",
        "mass",
        "g",
    )
    assert lines[0].total == pytest.approx(50.0 + 51.4)


@pytest.mark.usefixtures("db_engine")
def test_prune_orphan_ingredients() -> None:
    """A parse-time self-alias must not keep an unused ingredient
    alive; an ingredient with lines or inbound aliases stays."""
    recipe_id = add_recipe("https://x/1")

    with get_session() as session:
        used = Ingredient(name="milk")
        orphan = Ingredient(name="warm milk")
        session.add_all([used, orphan])
        session.flush()
        session.add(IngredientAlias(alias="milk", ingredient_id=used.id))
        session.add(IngredientAlias(alias="warm milk", ingredient_id=orphan.id))
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="250ml milk",
                ingredient_id=used.id,
                quantity=250.0,
                dimension="volume",
                base_unit="ml",
            )
        )
        used_id = used.id
        orphan_id = orphan.id

    with get_session() as session:
        assert actions.prune_orphan_ingredients(session) == 1

    with get_session() as session:
        assert session.get(Ingredient, orphan_id) is None
        assert session.get(Ingredient, used_id) is not None


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

    actions.store_recipe_ingredients(
        "https://x/cordon-bleu",
        {"recipeIngredient": case["lines"]},
        BBC_GOOD_FOOD,
    )

    lines = actions.aggregate([recipe_id])

    flour = next(line for line in lines if line.label == "flour")
    assert flour.total == pytest.approx(100 + 2 * 17.7581640625 * 0.55)
    emmental = next(line for line in lines if line.label == "emmental")
    assert emmental.total == 150.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (6, 6),
        ("Serves 12", 12),
        ("Makes 1 loaf", 1),
        # Ranges buy the upper end, like ingredient lines.
        ("Servings: 3 to 4", 4),
        # King Arthur yields are lists: the first entry wins.
        (["12", "1 loaf"], 12),
        (["60", "60 bites"], 60),
    ],
)
def test_servings_from_yield(value: object, expected: int) -> None:
    assert actions._servings_from_yield(value) == expected


def test_conversion_portions_prefer_fdc_over_cnf(session: Session) -> None:
    """seed > FDC > CNF: with confirmed conversion references in both,
    lines convert through FDC's portions (not alphabetical order)."""
    egg = Ingredient(name="egg")
    session.add(egg)
    session.flush()
    for name, weight in (("cnf", 60.0), ("fdc", 50.0)):
        source = FoodSource(name=name, version="test", licence="test", citation="test")
        session.add(source)
        session.flush()
        food = RefFood(source_id=source.id, source_food_id="1", description="Egg")
        session.add(food)
        session.flush()
        session.add_all(
            [
                RefPortion(
                    ref_food_id=food.id,
                    seq_num=1,
                    amount=1.0,
                    unit="large",
                    modifier=None,
                    gram_weight=weight,
                ),
                IngredientFoodRef(
                    ingredient_id=egg.id,
                    ref_food_id=food.id,
                    role="conversion",
                    confirmed=True,
                ),
            ]
        )
    session.flush()

    source_name, portions = actions._conversion_portions(egg.id, session)

    assert source_name == "fdc"
    assert [portion.gram_weight for portion in portions] == [50.0]
