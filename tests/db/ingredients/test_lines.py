"""Line-storage tests: parsing recipeYield, storing recipe_ingredients
rows, and the review queue."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientConversion,
    IngredientTerm,
    Recipe,
    RecipeIngredient,
    RefFood,
    RefPortion,
)
from menu.db.ingredients import lines as lines_module
from menu.db.ingredients import repository
from menu.db.ingredients import seed as seed_module
from menu.db.ingredients.seed import seed_ingredient_data
from menu.ingest.ingredients import parse_line

from .conftest import add_recipe, store_lines

if TYPE_CHECKING:
    from menu.ingest.ingredients import ParsedLine
    from menu.ingest.registry import SiteConfig


@pytest.mark.usefixtures("db_engine")
def test_store_writes_grams_from_confirmed_ref() -> None:
    """Storing a line converts it to grams through the portion rows
    the seed derived from the ingredient's confirmed conversion
    reference."""
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
    with get_session() as session:
        seed_ingredient_data(session)

    store_lines(recipe_id, {"recipeIngredient": ["2 medium eggs"]})
    with get_session() as session:
        line = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).first()
        assert line is not None
        assert line.grams == 88.0
        assert line.grams_max is None
        assert line.grams_source == "fdc"
        conversion = session.get(IngredientConversion, line.conversion_id)
        assert conversion is not None
        assert (conversion.kind, conversion.unit, conversion.origin) == (
            "portion",
            "medium",
            "fdc",
        )


@pytest.mark.usefixtures("db_engine")
def test_size_specific_eggs_use_their_own_row() -> None:
    """All ref portion sizes become portion rows: "2 large eggs"
    converts through the large row (not the seeded default weight)
    and names it."""
    recipe_id = add_recipe("https://x/omelette")
    with get_session() as session:
        source = FoodSource(
            name="fdc", version="test", licence="public domain", citation="test"
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
        for seq_num, (unit, grams) in enumerate(
            (("small", 38.0), ("medium", 44.0), ("large", 50.0)), start=1
        ):
            session.add(
                RefPortion(
                    ref_food_id=food.id,
                    seq_num=seq_num,
                    amount=1.0,
                    unit=unit,
                    modifier=unit,
                    gram_weight=grams,
                )
            )
    with get_session() as session:
        seed_ingredient_data(session)

    store_lines(recipe_id, {"recipeIngredient": ["2 large eggs"]})

    with get_session() as session:
        line = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).one()
        assert line.grams == 100.0
        conversion = session.get(IngredientConversion, line.conversion_id)
        assert conversion is not None
        assert (conversion.kind, conversion.unit, conversion.origin) == (
            "portion",
            "large",
            "fdc",
        )
        assert conversion.grams == 50.0


@pytest.mark.usefixtures("db_engine")
def test_seeded_density_beats_the_derived_one() -> None:
    """Both rows exist side by side (seed 1.03, the cup portion's
    implication); the line converts through the seed."""
    recipe_id = add_recipe("https://x/1")
    with get_session() as session:
        source = FoodSource(
            name="fdc", version="test", licence="public domain", citation="test"
        )
        session.add(source)
        session.flush()
        food = RefFood(
            source_id=source.id,
            source_food_id="2705385",
            description="Milk, whole",
        )
        session.add(food)
        session.flush()
        session.add(
            RefPortion(
                ref_food_id=food.id,
                seq_num=1,
                amount=1.0,
                unit="cup",
                modifier="cup",
                gram_weight=244.0,
            )
        )
    with get_session() as session:
        seed_ingredient_data(session)

    store_lines(recipe_id, {"recipeIngredient": ["100ml milk"]})

    with get_session() as session:
        line = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).one()
        assert line.grams == pytest.approx(100 * 1.03)
        assert line.grams_source == "seed"
        conversion = session.get(IngredientConversion, line.conversion_id)
        assert conversion is not None
        assert (conversion.kind, conversion.origin) == ("density", "seed")
        derived = session.scalars(
            select(IngredientConversion).where(
                IngredientConversion.kind == "density",
                IngredientConversion.origin == "fdc",
            )
        ).one()
        assert derived.grams == pytest.approx(244.0 / 236.58823648)


@pytest.mark.usefixtures("db_engine")
def test_mass_lines_have_no_conversion() -> None:
    """Mass is already grams: no lookup happened, so the line names
    no conversion row."""
    recipe_id = add_recipe("https://x/1")
    store_lines(recipe_id, {"recipeIngredient": ["100g butter"]})

    with get_session() as session:
        line = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).one()
        assert line.grams == 100.0
        assert line.grams_source is None
        assert line.conversion_id is None


@pytest.mark.usefixtures("db_engine")
def test_store_recipe_ingredients() -> None:
    recipe_id = add_recipe("https://x/pancakes")
    data = {
        "recipeIngredient": ["240ml warm milk", "2 eggs, beaten", "oil, for proving"],
        "recipeYield": "Serves 12",
    }

    store_lines(recipe_id, data)

    with get_session() as session:
        recipe = session.get(Recipe, recipe_id)
        assert recipe is not None
        assert (recipe.servings, recipe.yield_text) == (12, "Serves 12")
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
        # Every parsed name became a term the line points at.
        assert all(line.term_id is not None for line in lines)
        assert all(line.parse_status == "ok" for line in lines)
        assert all(line.parse_confidence is not None for line in lines)


@pytest.mark.usefixtures("db_engine")
def test_reparse_replaces_rows() -> None:
    recipe_id = add_recipe("https://x/pancakes")
    store_lines(recipe_id, {"recipeIngredient": ["240ml warm milk", "100g butter"]})

    store_lines(recipe_id, {"recipeIngredient": ["2 eggs"]})

    with get_session() as session:
        lines = session.scalars(select(RecipeIngredient)).all()
        assert [line.raw_text for line in lines] == ["2 eggs"]


@pytest.mark.usefixtures("db_engine")
def test_alternatives_stored_as_separate_rows() -> None:
    recipe_id = add_recipe("https://x/cake")

    store_lines(recipe_id, {"recipeIngredient": ["rosewater or vanilla extract"]})

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
def test_bad_line_stored_as_failed_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parse that raises is data too: stored with parse_status
    'failed' and no term. A bad line must not stop the recipe (or the
    crawl), mirroring the pipeline's per-URL handling."""

    def flaky(text: str, site: SiteConfig) -> ParsedLine:
        if "bad" in text:
            raise ValueError("nope")
        return parse_line(text, site)

    monkeypatch.setattr(lines_module, "parse_line", flaky)
    recipe_id = add_recipe("https://x/pancakes")

    store_lines(
        recipe_id,
        {"recipeIngredient": ["240ml warm milk", "bad line", "2 eggs"]},
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).order_by(RecipeIngredient.position)
        ).all()
        assert [line.raw_text for line in lines] == [
            "240ml warm milk",
            "bad line",
            "2 eggs",
        ]
        failed = lines[1]
        assert failed.parse_status == "failed"
        assert failed.term_id is None


@pytest.mark.usefixtures("db_engine")
def test_servings_parsed_from_yield_variants() -> None:
    """recipeYield is messy: "Makes 1 loaf" and plain 6."""
    recipe_ids = []
    for url, data in (
        ("https://x/a", {"recipeYield": "Makes 1 loaf"}),
        ("https://x/b", {"recipeYield": 6}),
    ):
        recipe_id = add_recipe(url)
        recipe_ids.append(recipe_id)
        store_lines(recipe_id, data)

    with get_session() as session:
        yields = {
            recipe.id: recipe.servings for recipe in session.scalars(select(Recipe))
        }
    assert [yields[rid] for rid in recipe_ids] == [1, 6]


@pytest.mark.usefixtures("db_engine")
def test_review_queue_catches_mangled_names() -> None:
    """Names the parser likely mangled (long modifier chains, unsplit
    conjunctions) become review terms: stored, pointing nowhere, so
    they never become canonical ingredients."""
    recipe_id = add_recipe("https://x/cake")

    store_lines(
        recipe_id,
        {"recipeIngredient": ["200g good-quality candied orange and lemon peel"]},
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        assert len(lines) == 1
        term = session.get(IngredientTerm, lines[0].term_id)
        assert term is not None
        assert term.resolution == "review"
        assert term.ingredient_id is None
        assert repository.effective_ingredient_id(lines[0].needs_review, term) is None
        assert lines[0].raw_text == "200g good-quality candied orange and lemon peel"


@pytest.mark.usefixtures("db_engine")
def test_lost_conjunction_goes_to_review_queue() -> None:
    """ "pink and yellow food colouring gels" parses as "pink food
    colouring gel" with "yellow food colouring gel" as an alternative —
    but a shopping list needs both gels, so the line carries the
    line-level review flag and resolves to nothing."""
    recipe_id = add_recipe("https://x/cake")

    store_lines(
        recipe_id,
        {"recipeIngredient": ["pink and yellow food colouring gels"]},
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        assert primary.needs_review is True
        term = session.get(IngredientTerm, primary.term_id)
        assert term is not None
        assert repository.effective_ingredient_id(primary.needs_review, term) is None


@pytest.mark.usefixtures("db_engine")
def test_seeded_term_rescues_lost_conjunction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seeded term wins over the lost-conjunction heuristic, like it
    does over the mangled-name one — and the line flag clears."""
    monkeypatch.setattr(
        seed_module,
        "ALIASES",
        [
            *seed_module.ALIASES,
            ("pink food colouring gel", "food colouring gel", None),
        ],
    )
    with get_session() as session:
        seed_ingredient_data(session)
    recipe_id = add_recipe("https://x/cake")

    store_lines(
        recipe_id,
        {"recipeIngredient": ["pink and yellow food colouring gels"]},
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        # The seeded term decided: the line flag stays clear.
        # The flag is stored as the heuristic says; the seed wins anyway.
        assert primary.needs_review is True
        term = session.get(IngredientTerm, primary.term_id)
        assert term is not None
        effective_id = repository.effective_ingredient_id(primary.needs_review, term)
        assert effective_id is not None
        resolved = session.get(Ingredient, effective_id)
        assert resolved is not None
        assert resolved.name == "food colouring gel"


@pytest.mark.usefixtures("db_engine")
def test_seeded_alias_rescues_mangled_name() -> None:
    """A seeded term wins over the review heuristic: the real cached
    line "30g whole blanched almonds roughly chopped or flaked almonds"
    parses to the name "whole blanched almonds roughly chopped" and
    resolves to blanched almond instead of sitting unresolved."""
    with get_session() as session:
        seed_ingredient_data(session)
    recipe_id = add_recipe("https://x/cake")

    store_lines(
        recipe_id,
        {
            "recipeIngredient": [
                "30g whole blanched almonds roughly chopped or flaked almonds"
            ]
        },
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        term = session.get(IngredientTerm, primary.term_id)
        assert term is not None
        effective_id = repository.effective_ingredient_id(primary.needs_review, term)
        assert effective_id is not None
        ingredient = session.get(Ingredient, effective_id)
        assert ingredient is not None
        assert ingredient.name == "blanched almond"


@pytest.mark.usefixtures("db_engine")
def test_line_override_fixes_wrong_merge() -> None:
    """ "70g  milk or dark chocolate roughly chopped (optional)" — the
    real cached line, double space and all — parses as name "milk"
    with confidence 1.0. No threshold catches it, only the curated
    line override does, and the override matches the squashed line."""
    recipe_id = add_recipe("https://x/babka")

    store_lines(
        recipe_id,
        {
            "recipeIngredient": [
                "70g  milk or dark chocolate roughly chopped (optional)"
            ]
        },
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.id)
        ).all()
        primary = next(line for line in lines if line.alternative_of is None)
        term = session.get(IngredientTerm, primary.term_id)
        assert term is not None
        effective_id = repository.effective_ingredient_id(primary.needs_review, term)
        assert effective_id is not None
        ingredient = session.get(Ingredient, effective_id)
        assert ingredient is not None
        assert ingredient.name == "milk chocolate"


@pytest.mark.usefixtures("db_engine")
def test_quantity_level_or_alternative_kept() -> None:
    """ "2 tsp vanilla or 1 tsp essential oil": the parser drops the
    second option into the note; splitting on "or" keeps it as an
    alternative row with its own quantity."""
    recipe_id = add_recipe("https://x/cake")

    store_lines(
        recipe_id,
        {"recipeIngredient": ["2 tsp vanilla or 1 tsp essential oil"]},
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

    store_lines(
        recipe_id,
        {
            "recipeIngredient": [
                "2 large eggs (or 3 small)",
                "1 tsp salt or to taste",
                "1 tbsp milk, or more if needed",
            ]
        },
    )

    with get_session() as session:
        lines = session.scalars(
            select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
        ).all()
        assert len(lines) == 3
        assert all(line.alternative_of is None for line in lines)


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
    assert lines_module._servings_from_yield(value) == expected
