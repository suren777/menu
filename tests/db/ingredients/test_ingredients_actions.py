"""Action tests: storing parsed lines and aggregating a shopping list."""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientConversion,
    IngredientFoodRef,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RefFood,
    RefPortion,
)
from menu.db.ingredients import actions, repository
from menu.db.ingredients import seed as seed_module
from menu.db.ingredients.seed import seed_ingredient_data
from menu.ingest.ingredients import parse_line
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

FIXTURE = Path(__file__).parents[2] / "ingest" / "fixtures" / "bbc_lines.json"

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from menu.ingest.ingredients import ParsedLine
    from menu.ingest.registry import SiteConfig


def add_recipe(url: str) -> int:
    """Insert a bare recipe row, reached through its page (the
    ledger); the action opens its own session, so the seed must be
    committed before it runs."""
    with get_session() as session:
        page = Page(url=url, site="bbc_good_food")
        session.add(page)
        session.flush()
        recipe = Recipe(
            page_id=page.id,
            name="Test",
            data={},
            data_sha256=url,
            extractor="json_ld",
        )
        session.add(recipe)
        session.flush()
        return recipe.id


def add_recipe_row(session: Session, url: str, name: str) -> Recipe:
    """The same bare recipe, in the caller's transaction."""
    page = Page(url=url, site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id, name=name, data={}, data_sha256=url, extractor="json_ld"
    )
    session.add(recipe)
    session.flush()
    return recipe


def store_lines(recipe_id: int, data: dict[str, Any]) -> None:
    """Store a recipe's lines through its recipe row: the action runs
    in the caller's session (the pipeline supplies it)."""
    with get_session() as session:
        actions.store_recipe_ingredients(recipe_id, data, BBC_GOOD_FOOD, session)


def add_term(
    session: Session,
    term: str,
    ingredient: Ingredient,
    *,
    variant: str | None = None,
    resolution: str = "seed",
) -> IngredientTerm:
    """A term pointing at a concept, for hand-built lines."""
    row = IngredientTerm(
        term=term, ingredient_id=ingredient.id, variant=variant, resolution=resolution
    )
    session.add(row)
    session.flush()
    return row


def _egg_conversions() -> list[IngredientConversion]:
    """The egg's conversion rows: the seeded unit weight plus the
    portion rows seeding derives from the FDC fixture (sizes plus the
    cup). Ids are set so a returned conversion_id is checkable."""
    return [
        IngredientConversion(
            id=1,
            ingredient_id=1,
            kind="unit_weight",
            unit=None,
            modifier=None,
            grams=50.0,
            origin="seed",
        ),
        IngredientConversion(
            id=2,
            ingredient_id=1,
            kind="portion",
            unit="small",
            modifier="small",
            grams=38.0,
            origin="fdc",
            ref_food_id=1,
            ref_seq_num=1,
        ),
        IngredientConversion(
            id=3,
            ingredient_id=1,
            kind="portion",
            unit="medium",
            modifier="medium",
            grams=44.0,
            origin="fdc",
            ref_food_id=1,
            ref_seq_num=2,
        ),
        IngredientConversion(
            id=4,
            ingredient_id=1,
            kind="portion",
            unit="large",
            modifier="large",
            grams=50.0,
            origin="fdc",
            ref_food_id=1,
            ref_seq_num=3,
        ),
        IngredientConversion(
            id=5,
            ingredient_id=1,
            kind="portion",
            unit="cup (4.86 large eggs)",
            modifier="cup (4.86 large eggs)",
            grams=243.0,
            origin="fdc",
            ref_food_id=1,
            ref_seq_num=4,
        ),
    ]


def test_to_grams_count_uses_size_matched_portion() -> None:
    """1 large egg -> 50 g, 2 medium eggs -> 88 g: the portion
    matching the line's size beats the seeded default."""
    conversions = _egg_conversions()
    assert actions.to_grams(
        1.0, None, "count", "piece", None, None, "large", conversions
    ) == (50.0, None, "fdc", 4)
    assert actions.to_grams(
        2.0, None, "count", "piece", None, None, "medium", conversions
    ) == (88.0, None, "fdc", 3)


def test_to_grams_count_falls_back_to_seed_weight() -> None:
    """No size: the seeded unit weight converts ("2 eggs" -> 100 g)."""
    assert actions.to_grams(
        2.0, None, "count", "piece", None, None, None, _egg_conversions()
    ) == (100.0, None, "seed", 1)


def test_to_grams_volume_uses_unit_portion() -> None:
    """1 cup egg -> 243 g through the "cup (4.86 large eggs)" row."""
    assert actions.to_grams(
        1.0, None, "volume", "ml", "cup", None, None, _egg_conversions()
    ) == (243.0, None, "fdc", 5)


def test_to_grams_volume_seed_density_beats_derived() -> None:
    """A seed beats the reference: 100 ml of a 2.0 g/ml seed is
    200 g, whatever the cup portion implies and whatever a derived
    density row says."""
    rows = [
        IngredientConversion(
            id=8,
            ingredient_id=2,
            kind="density",
            unit=None,
            modifier=None,
            grams=2.0,
            origin="seed",
        ),
        IngredientConversion(
            id=9,
            ingredient_id=2,
            kind="density",
            unit=None,
            modifier=None,
            grams=1.0,
            origin="fdc",
            ref_food_id=2,
            ref_seq_num=1,
        ),
        IngredientConversion(
            id=10,
            ingredient_id=2,
            kind="portion",
            unit="cup",
            modifier="cup",
            grams=200.0,
            origin="fdc",
            ref_food_id=2,
            ref_seq_num=2,
        ),
    ]
    # The line's unit (ml) has no portion row, so the seeded density
    # wins over the derived one and the cup row's implication.
    assert actions.to_grams(100.0, None, "volume", "ml", "ml", None, None, rows) == (
        200.0,
        None,
        "seed",
        8,
    )


def test_to_grams_volume_implied_density() -> None:
    """A pinch of salt converts through the density its teaspoon
    portion row implies, recorded against that row."""
    tsp = IngredientConversion(
        id=7,
        ingredient_id=3,
        kind="portion",
        unit="tsp",
        modifier="tsp",
        grams=6.0,
        origin="fdc",
        ref_food_id=3,
        ref_seq_num=1,
    )
    # "a pinch of salt" stores quantity 0.36 ml (the fixed amount
    # units._FIXED_AMOUNTS assigns a pinch), then converts via the
    # density the tsp portion implies.
    grams, grams_max, source, conversion_id = actions.to_grams(
        0.36, None, "volume", "ml", "pinch", None, None, [tsp]
    )
    assert grams == pytest.approx(0.36 * 6.0 / 4.928921610938, rel=1e-3)
    assert grams_max is None
    assert source == "fdc"
    assert conversion_id == 7


def test_to_grams_named_count_unit() -> None:
    """2 sticks of butter convert through the stick row."""
    stick = IngredientConversion(
        id=6,
        ingredient_id=4,
        kind="portion",
        unit="stick",
        modifier="stick",
        grams=113.0,
        origin="fdc",
        ref_food_id=4,
        ref_seq_num=1,
    )
    assert actions.to_grams(
        2.0, None, "count", "stick", "sticks", None, None, [stick]
    ) == (226.0, None, "fdc", 6)


def test_to_grams_never_guesses() -> None:
    """No conversion rows: grams stays NULL."""
    assert actions.to_grams(2.0, None, "count", "piece", None, None, None, []) == (
        None,
        None,
        None,
        None,
    )


def test_to_grams_ranges_and_mass() -> None:
    """Ranges convert the upper end too; mass lines need no lookup."""
    cup = IngredientConversion(
        id=10,
        ingredient_id=6,
        kind="portion",
        unit="cup",
        modifier="cup",
        grams=125.0,
        origin="fdc",
        ref_food_id=7,
        ref_seq_num=1,
    )
    assert actions.to_grams(2.0, 3.0, "volume", "ml", "cups", None, None, [cup]) == (
        250.0,
        375.0,
        "fdc",
        10,
    )
    assert actions.to_grams(100.0, 120.0, "mass", "g", "g", None, None, []) == (
        100.0,
        120.0,
        None,
        None,
    )


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
def test_removing_a_density_seed_regrams_the_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a DENSITIES_G_PER_ML entry: re-seeding deletes its
    row and re-resolve regrams the line — to NULL, milk having no
    reference to fall back on here."""
    with get_session() as session:
        seed_ingredient_data(session)
    recipe_id = add_recipe("https://x/1")
    store_lines(recipe_id, {"recipeIngredient": ["240ml milk"]})
    with get_session() as session:
        line = session.scalars(select(RecipeIngredient)).one()
        assert line.grams == pytest.approx(240 * 1.03)
        assert line.conversion_id is not None

    monkeypatch.setattr(
        seed_module,
        "DENSITIES_G_PER_ML",
        {
            name: value
            for name, value in seed_module.DENSITIES_G_PER_ML.items()
            if name != "milk"
        },
    )
    actions.re_resolve()

    with get_session() as session:
        milk_id = session.scalar(select(Ingredient.id).where(Ingredient.name == "milk"))
        assert milk_id is not None
        rows = session.scalars(
            select(IngredientConversion).where(
                IngredientConversion.ingredient_id == milk_id
            )
        ).all()
        assert rows == []
        line = session.scalars(select(RecipeIngredient)).one()
        assert line.grams is None
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

    monkeypatch.setattr(actions, "parse_line", flaky)
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

    lines = actions.aggregate([recipe_id])

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

    lines = actions.aggregate([recipe_id])

    # Resolved lines sort before unresolved ones; the quantified milk
    # line also covers rosewater's bare "as needed" entry.
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

    lines = actions.aggregate([recipe_id], servings_scale=2.0)

    assert lines[0].total == 500.0


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
        # The curated seed term "vanilla" -> almond links the pair.
        session.add(
            IngredientTerm(term="vanilla", ingredient_id=record.id, resolution="seed")
        )

    # The curated seed links the pair, so the group is no longer a conflict.
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

    assert actions.ref_review() == [(2, "mystery powder", "Baobab powder", ())]


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

    assert actions.ref_review() == [(1, "baobab powder", None, ())]


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

    lines = actions.aggregate([basbousa_id, shokupan_id])
    assert len(lines) == 1
    assert (lines[0].label, lines[0].variant) == ("milk", None)
    # Every line converted (seeded milk density): one gram line.
    assert lines[0].total == pytest.approx((240 + 500) * 1.03)

    split = actions.aggregate([basbousa_id, shokupan_id], keep_variants=True)
    assert sorted((line.label, line.total) for line in split) == pytest.approx(
        [("milk (warm)", 240 * 1.03), ("milk (whole)", 500 * 1.03)]
    )


@pytest.mark.usefixtures("db_engine")
def test_aggregate_uses_range_upper_end() -> None:
    """ "2-3 onions" buys 3: under-buying a shopping list is worse
    than over-buying it."""
    recipe_id = add_recipe("https://x/curry")

    store_lines(recipe_id, {"recipeIngredient": ["2-3 large onions, sliced"]})

    lines = actions.aggregate([recipe_id])

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

    lines = actions.aggregate([recipe_id])

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

    lines = actions.aggregate([recipe_id])

    assert [line.label for line in lines] == ["milk"]
    assert lines[0].total == pytest.approx(250 * 1.03)


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_repoints_terms_without_reparse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The everyday command after a seed change: a stored line's auto
    term is re-pointed at the canonical ingredient — with the parser
    unusable — and the line's grams are recomputed from the stored
    columns. Every line sharing the term moves together."""
    basbousa_id = add_recipe("https://x/basbousa")
    shokupan_id = add_recipe("https://x/shokupan")
    store_lines(basbousa_id, {"recipeIngredient": ["240ml warm milk"]})
    store_lines(shokupan_id, {"recipeIngredient": ["500ml warm milk"]})

    with get_session() as session:
        lines = session.scalars(select(RecipeIngredient)).all()
        assert len(lines) == 2
        terms = {session.get(IngredientTerm, line.term_id) for line in lines}
        assert len(terms) == 1  # both lines share one term
        (term,) = terms
        assert term is not None
        assert term.resolution == "auto"
        old_effective = term.ingredient_id
        assert old_effective is not None

    # Seed the variant and make the parser unusable: re-resolve must
    # work from the stored columns alone.
    monkeypatch.setattr(
        seed_module,
        "ALIASES",
        [*seed_module.ALIASES, ("warm milk", "milk", "warm")],
    )

    def boom(*_args: object, **_kwargs: object) -> ParsedLine:
        raise AssertionError("re-resolve must not parse")

    monkeypatch.setattr(actions, "parse_line", boom)

    regrammed, pruned = actions.re_resolve()[1:]

    assert regrammed == 2  # both lines' effective ingredient changed
    assert pruned == 1  # the orphaned auto concept "warm milk"
    with get_session() as session:
        milk = session.scalars(
            select(Ingredient).where(Ingredient.name == "milk")
        ).one()
        for line in session.scalars(select(RecipeIngredient)).all():
            term = session.get(IngredientTerm, line.term_id)
            assert term is not None
            assert (term.term, term.variant, term.resolution) == (
                "warm milk",
                "warm",
                "seed",
            )
            effective = repository.effective_ingredient_id(line.needs_review, term)
            assert effective == milk.id
            assert effective != old_effective
            # Grams recomputed from the stored columns through milk's
            # seeded density (1.03 g/ml) — no parser involved.
            expected = 240.0 if line.recipe_id == basbousa_id else 500.0
            assert line.grams == pytest.approx(expected * 1.03)
            assert line.grams_source == "seed"


@pytest.mark.usefixtures("db_engine")
def test_prune_orphan_ingredients() -> None:
    """An unused auto term goes, and an auto concept nothing points at
    and no curated reference keeps goes with it; a concept a line
    resolves through stays."""
    recipe_id = add_recipe("https://x/1")

    with get_session() as session:
        used = Ingredient(name="milk")
        orphan = Ingredient(name="warm milk")
        session.add_all([used, orphan])
        session.flush()
        used_term = add_term(session, "milk", used, resolution="auto")
        add_term(session, "warm milk", orphan, resolution="auto")
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="250ml milk",
                term_id=used_term.id,
                quantity=250.0,
                dimension="volume",
                base_unit="ml",
            )
        )
        orphan_id = orphan.id

    with get_session() as session:
        assert actions.prune_orphan_ingredients(session) == 1

    with get_session() as session:
        assert session.get(Ingredient, orphan_id) is None
        assert (
            session.scalars(
                select(IngredientTerm).where(IngredientTerm.term == "warm milk")
            ).first()
            is None
        )
        assert len(session.scalars(select(Ingredient)).all()) == 1


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


def test_seeding_derives_from_the_best_source_only(session: Session) -> None:
    """seed > FDC > CNF: with confirmed conversion references in both,
    the derived conversion rows come from FDC's portions only, never
    mixed with another source's."""
    quail = Ingredient(name="quail egg")
    session.add(quail)
    session.flush()
    for name, weight in (("cnf", 60.0), ("fdc", 50.0)):
        source = FoodSource(name=name, version="test", licence="test", citation="test")
        session.add(source)
        session.flush()
        food = RefFood(source_id=source.id, source_food_id="1", description="Quail")
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
                    ingredient_id=quail.id,
                    ref_food_id=food.id,
                    source_id=food.source_id,
                    role="conversion",
                    status="confirmed",
                    origin="seed",
                ),
            ]
        )
    session.flush()

    seed_ingredient_data(session)

    rows = session.scalars(
        select(IngredientConversion).where(
            IngredientConversion.ingredient_id == quail.id
        )
    ).all()
    # "large" is neither a medium portion (no unit_weight row) nor
    # volumetric (no density row): the single FDC portion row is all.
    assert [(row.origin, row.kind, row.unit, row.grams) for row in rows] == [
        ("fdc", "portion", "large", 50.0)
    ]


@pytest.mark.usefixtures("db_engine")
def test_prune_drops_parser_candidates_but_keeps_curated_refs() -> None:
    """With foreign keys enforced, an orphan's parser candidate goes
    with it; an ingredient with a curated reference is a decision in
    seed.py and is never pruned."""
    with get_session() as session:
        source = FoodSource(name="fdc", version="1", licence="CC0", citation="x")
        session.add(source)
        session.flush()
        food = RefFood(source_id=source.id, source_food_id="1", description="Milk")
        orphan = Ingredient(name="warm milk", origin="auto")
        curated = Ingredient(name="milk", origin="seed")
        session.add_all([food, orphan, curated])
        session.flush()
        session.add_all(
            [
                IngredientFoodRef(
                    ingredient_id=orphan.id,
                    ref_food_id=food.id,
                    source_id=source.id,
                    status="candidate",
                    origin="parser",
                ),
                IngredientFoodRef(
                    ingredient_id=curated.id,
                    ref_food_id=food.id,
                    source_id=source.id,
                    role="nutrition",
                    status="confirmed",
                    origin="seed",
                ),
            ]
        )
        orphan_id, curated_id = orphan.id, curated.id

    with get_session() as session:
        assert actions.prune_orphan_ingredients(session) == 1

    with get_session() as session:
        assert session.get(Ingredient, orphan_id) is None
        assert session.get(Ingredient, curated_id) is not None


def _effective_names() -> list[str | None]:
    """Each primary line's effective ingredient name, in id order."""
    with get_session() as session:
        names: list[str | None] = []
        for line, term in session.execute(
            repository.lines_with_terms_select(
                RecipeIngredient.alternative_of.is_(None)
            ).order_by(RecipeIngredient.id)
        ):
            effective = repository.effective_ingredient_id(line.needs_review, term)
            ingredient = (
                session.get(Ingredient, effective) if effective is not None else None
            )
            names.append(ingredient.name if ingredient is not None else None)
        return names


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_regrams_when_only_a_density_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seed change can move grams without moving the ingredient."""
    store_lines(add_recipe("https://x/1"), {"recipeIngredient": ["240ml oat milk"]})
    with get_session() as session:
        assert session.scalars(select(RecipeIngredient.grams)).one() is None

    monkeypatch.setattr(
        seed_module,
        "DENSITIES_G_PER_ML",
        {**seed_module.DENSITIES_G_PER_ML, "oat milk": 1.03},
    )
    actions.re_resolve()

    assert _effective_names() == ["oat milk"]
    with get_session() as session:
        grams = session.scalars(select(RecipeIngredient.grams)).one()
    assert grams == pytest.approx(240 * 1.03)


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_lets_a_later_seed_rescue_a_flagged_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_lines(
        add_recipe("https://x/cake"),
        {"recipeIngredient": ["pink and yellow food colouring gels"]},
    )
    assert _effective_names() == [None]

    monkeypatch.setattr(
        seed_module,
        "ALIASES",
        [
            *seed_module.ALIASES,
            ("pink food colouring gel", "food colouring gel", None),
        ],
    )
    actions.re_resolve()

    assert _effective_names() == ["food colouring gel"]


@pytest.mark.usefixtures("db_engine")
def test_alias_beats_a_same_named_seed_concept() -> None:
    """ "warm milk" is an ALIASES entry for milk; seeding a density for
    a "warm milk" concept must not steal the term."""
    with get_session() as session:
        seed_ingredient_data(session)
        concept = Ingredient(name="warm milk", origin="seed")
        session.add(concept)
        session.flush()
        seed_module._seed_term("warm milk", concept.id, None, session, repoint=False)
        term = session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "warm milk")
        ).one()
        milk = session.scalars(
            select(Ingredient).where(Ingredient.name == "milk")
        ).one()
        assert term.ingredient_id == milk.id


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_follows_alias_edits_and_removals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-pointing a seeded alias moves its lines; deleting it from
    seed.py hands the term back to the heuristics."""
    original = list(seed_module.ALIASES)
    store_lines(add_recipe("https://x/1"), {"recipeIngredient": ["240ml oat milk"]})

    monkeypatch.setattr(
        seed_module, "ALIASES", [*original, ("oat milk", "milk", "oat")]
    )
    actions.re_resolve()
    assert _effective_names() == ["milk"]

    monkeypatch.setattr(
        seed_module, "ALIASES", [*original, ("oat milk", "cream", None)]
    )
    actions.re_resolve()
    assert _effective_names() == ["cream"]

    monkeypatch.setattr(seed_module, "ALIASES", original)
    actions.re_resolve()
    assert _effective_names() == ["oat milk"]
    with get_session() as session:
        term = session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "oat milk")
        ).one()
        assert term.resolution == "auto"
