"""Nutrition tests: the published-nutrition parser, the pure
derivation, and the stored derivation runs."""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    DerivationRun,
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrient,
    RecipeNutritionRun,
    RecipePublishedNutrient,
    RefFood,
    RefNutrient,
)
from menu.nutrition.derive import (
    NutritionData,
    derive_recipe,
    parse_published_nutrition,
    published_nutrients,
)
from menu.nutrition.reports import derive_all_nutrition, prune_runs

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


def test_published_nutrients_keeps_the_raw_text() -> None:
    """Each published nutrient keeps the JSON-LD string it was parsed
    from, and parse_published_nutrition reads the same amounts."""
    parsed = published_nutrients({"nutrition": {"calories": "350 calories"}})
    assert parsed == {"energy_kcal": (350.0, "350 calories")}
    assert parse_published_nutrition({"nutrition": {"calories": "350 calories"}}) == {
        "energy_kcal": 350.0
    }


def _line(**kwargs: Any) -> RecipeIngredient:
    """A hand-built (unstored) ingredient line for derive_recipe."""
    defaults: dict[str, Any] = {
        "recipe_id": 1,
        "position": 0,
        "raw_text": "line",
        "parse_status": "ok",
    }
    return RecipeIngredient(**{**defaults, **kwargs})


def _term(
    ingredient_id: int | None = None,
    variant: str | None = None,
    resolution: str = "seed",
) -> IngredientTerm:
    return IngredientTerm(
        term="t", ingredient_id=ingredient_id, variant=variant, resolution=resolution
    )


def test_derive_recipe_sums_the_mapped_lines() -> None:
    """Hand-built lines and data, no database: ranges eat the
    midpoint, per-nutrient definitions and sources are tracked, and
    no-quantity, not_food and failed lines are not counted."""
    data = NutritionData(
        refs={1: {"fdc": 10}},
        nutrients={
            10: [("protein_g", 10.0, "by_weight"), ("energy_kcal", 100.0, "kcal")]
        },
        prepared_forms={},
        names={1: "egg", 2: "salt", 3: "tin"},
    )
    lines = [
        # 2-3 eggs: midpoint 75 g.
        (
            _line(
                term_id=1,
                quantity=2.0,
                quantity_max=3.0,
                base_unit="piece",
                grams=50.0,
                grams_max=100.0,
            ),
            _term(1),
        ),
        # No quantity: unconverted.
        (_line(term_id=2, base_unit="piece"), _term(2)),
        # Equipment: skipped outright.
        (
            _line(term_id=3, quantity=1.0, base_unit="piece", grams=100.0),
            _term(None, resolution="not_food"),
        ),
        # Failed parse: skipped outright.
        (_line(raw_text="unparseable", parse_status="failed"), None),
    ]

    derivation = derive_recipe(lines, ("fdc",), data)

    assert derivation.nutrients["protein_g"].amount == pytest.approx(75.0 / 100 * 10.0)
    assert derivation.nutrients["protein_g"].definitions == frozenset({"by_weight"})
    assert derivation.nutrients["protein_g"].sources == frozenset({"fdc"})
    assert derivation.nutrients["energy_kcal"].amount == pytest.approx(75.0)
    assert derivation.coverage == 1.0
    assert derivation.unconverted_lines == 1
    assert derivation.sources == frozenset({"fdc"})
    assert derivation.definitions_mixed is False


def test_derive_recipe_falls_back_from_an_unloaded_prepared_form() -> None:
    """A prepared form whose food has no loaded nutrient rows falls
    back to the confirmed reference."""
    data = NutritionData(
        refs={1: {"fdc": 10}},
        nutrients={10: [("protein_g", 12.56, "by_weight")]},
        prepared_forms={("fdc", "172184"): 99},
        names={1: "egg"},
    )
    derivation = derive_recipe(
        [
            (
                _line(term_id=1, quantity=1.0, base_unit="piece", grams=34.0),
                _term(1, variant="yolk"),
            )
        ],
        ("fdc",),
        data,
    )
    assert derivation.nutrients["protein_g"].amount == pytest.approx(34.0 / 100 * 12.56)


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


def _run_row(session: Session, run_id: int, recipe_id: int) -> RecipeNutritionRun:
    return session.scalars(
        select(RecipeNutritionRun).where(
            RecipeNutritionRun.run_id == run_id,
            RecipeNutritionRun.recipe_id == recipe_id,
        )
    ).one()


def _amounts(
    session: Session, run_id: int, recipe_id: int
) -> dict[str, RecipeNutrient]:
    rows = session.scalars(
        select(RecipeNutrient).where(
            RecipeNutrient.run_id == run_id,
            RecipeNutrient.recipe_id == recipe_id,
        )
    ).all()
    return {row.nutrient: row for row in rows}


@pytest.mark.usefixtures("db_engine")
def test_derive_all_nutrition_sums_confirmed_reference() -> None:
    """Each counted line contributes grams x per-100g; rerunning
    stores a second run instead of overwriting the first."""
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

    with get_session() as session:
        run_id = derive_all_nutrition(session).id
        derive_all_nutrition(session)
    with get_session() as session:
        assert len(session.scalars(select(DerivationRun)).all()) == 2
        amounts = _amounts(session, run_id, recipe_id)
        assert amounts["protein_g"].amount == pytest.approx(50.0 / 100 * 12.56)
        assert amounts["protein_g"].definitions == ["by_weight"]
        assert amounts["protein_g"].sources == ["fdc"]
        assert amounts["energy_kcal"].amount == pytest.approx(50.0 / 100 * 143.0)
        row = _run_row(session, run_id, recipe_id)
        assert row.coverage == 1.0
        assert row.unconverted_lines == 0
        assert row.sources == ["fdc"]
        assert row.definitions_mixed is False


@pytest.mark.usefixtures("db_engine")
def test_derive_all_nutrition_midpoint_and_skips() -> None:
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

    with get_session() as session:
        run_id = derive_all_nutrition(session).id
    with get_session() as session:
        amounts = _amounts(session, run_id, recipe_id)
        assert amounts["protein_g"].amount == pytest.approx(75.0 / 100 * 10.0)
        row = _run_row(session, run_id, recipe_id)
        assert row.coverage == 1.0
        assert row.unconverted_lines == 1


@pytest.mark.usefixtures("db_engine")
def test_derive_all_nutrition_coverage_and_definitions() -> None:
    """Unmapped ingredients drag coverage down; nutrients summed from
    differing definitions are flagged on the run and named on the
    nutrient row."""
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

    with get_session() as session:
        run_id = derive_all_nutrition(session).id
    with get_session() as session:
        amounts = _amounts(session, run_id, recipe_id)
        assert amounts["protein_g"].amount == pytest.approx(12.56 + 10.0)
        assert amounts["protein_g"].definitions == ["by_difference", "by_weight"]
        row = _run_row(session, run_id, recipe_id)
        assert row.coverage == pytest.approx(200.0 / 300.0)
        assert row.unconverted_lines == 1
        assert row.definitions_mixed is True
        assert row.sources == ["fdc"]


@pytest.mark.usefixtures("db_engine")
def test_derive_all_nutrition_uses_prepared_form() -> None:
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

    with get_session() as session:
        run_id = derive_all_nutrition(session).id
    with get_session() as session:
        amounts = _amounts(session, run_id, recipe_id)
        assert amounts["protein_g"].amount == pytest.approx(
            34.0 / 100 * 15.86 + 50.0 / 100 * 12.56
        )
        assert amounts["protein_g"].sources == ["fdc"]


@pytest.mark.usefixtures("db_engine")
def test_derive_all_nutrition_skips_not_food_lines() -> None:
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

    with get_session() as session:
        run_id = derive_all_nutrition(session).id
    with get_session() as session:
        amounts = _amounts(session, run_id, recipe_id)
        assert amounts["protein_g"].amount == pytest.approx(50.0 / 100 * 12.56)
        row = _run_row(session, run_id, recipe_id)
        assert row.coverage == 1.0
        assert row.unconverted_lines == 0


@pytest.mark.usefixtures("db_engine")
def test_derive_all_nutrition_rows_recipes_with_no_lines() -> None:
    """Every recipe gets its run row, also ones with no lines:
    coverage NULL, nothing unconverted, no nutrient rows."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/empty", "Empty")
        recipe_id = recipe.id

    with get_session() as session:
        run_id = derive_all_nutrition(session).id
    with get_session() as session:
        row = _run_row(session, run_id, recipe_id)
        assert row.coverage is None
        assert row.unconverted_lines == 0
        assert _amounts(session, run_id, recipe_id) == {}


@pytest.mark.usefixtures("db_engine")
def test_derive_all_nutrition_refreshes_published_rows() -> None:
    """Published values are raw capture with their JSON-LD text; a
    re-run replaces them, and a recipe that publishes nothing again
    loses its rows."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/pie", "Pie")
        recipe.data = {"nutrition": {"calories": "350 calories"}}
        recipe_id = recipe.id

    with get_session() as session:
        derive_all_nutrition(session)
    with get_session() as session:
        rows = session.scalars(
            select(RecipePublishedNutrient).where(
                RecipePublishedNutrient.recipe_id == recipe_id
            )
        ).all()
        assert {(row.nutrient, row.amount, row.raw_text) for row in rows} == {
            ("energy_kcal", 350.0, "350 calories")
        }
        stored = session.get(Recipe, recipe_id)
        assert stored is not None
        stored.data = {}

    with get_session() as session:
        derive_all_nutrition(session)
    with get_session() as session:
        assert session.scalars(select(RecipePublishedNutrient)).all() == []


@pytest.mark.usefixtures("db_engine")
def test_prune_runs_keeps_the_newest_with_its_rows() -> None:
    """prune_runs(keep=1) drops the older run and cascades its
    per-recipe and per-nutrient rows; the newest run's stay."""
    with get_session() as session:
        food = _add_nutrition_food(
            session, "171287", "Egg", [("protein_g", 12.56, "by_weight")]
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

    with get_session() as session:
        first_id = derive_all_nutrition(session).id
    with get_session() as session:
        second_id = derive_all_nutrition(session).id

    with get_session() as session:
        prune_runs(session, keep=1)
    with get_session() as session:
        assert session.get(DerivationRun, first_id) is None
        assert (
            session.scalars(
                select(RecipeNutritionRun).where(RecipeNutritionRun.run_id == first_id)
            ).all()
            == []
        )
        assert (
            session.scalars(
                select(RecipeNutrient).where(RecipeNutrient.run_id == first_id)
            ).all()
            == []
        )
        assert session.get(DerivationRun, second_id) is not None
        assert _run_row(session, second_id, recipe_id).coverage == 1.0
        assert _amounts(session, second_id, recipe_id)["protein_g"].amount == (
            pytest.approx(50.0 / 100 * 12.56)
        )
