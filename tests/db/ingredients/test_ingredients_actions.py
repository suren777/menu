"""Action tests: storing parsed lines and aggregating a shopping list."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import Ingredient, IngredientAlias, RecipeIngredient, RecipeUrls
from menu.db.ingredients import actions
from menu.db.ingredients.seed import seed_ingredient_data
from menu.ingest.ingredients import parse_line
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

FIXTURE = Path(__file__).parents[2] / "ingest" / "fixtures" / "bbc_lines.json"

if TYPE_CHECKING:
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
    """"70g milk or dark chocolate roughly chopped" parses as name
    "milk" with confidence 1.0 — no threshold catches it, only the
    curated line override does."""
    recipe_id = add_recipe("https://x/babka")

    actions.store_recipe_ingredients(
        "https://x/babka",
        {"recipeIngredient": ["70g milk or dark chocolate roughly chopped"]},
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
    assert (lines[0].label, lines[0].total, lines[0].variant) == (
        "milk",
        740.0,
        None,
    )

    split = actions.aggregate([basbousa_id, shokupan_id], keep_variants=True)
    assert sorted((line.label, line.total) for line in split) == [
        ("milk (warm)", 240.0),
        ("milk (whole)", 500.0),
    ]


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
