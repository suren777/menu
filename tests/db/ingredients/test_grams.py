"""Grams tests: the conversion precedence over the ingredient's rows."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientConversion,
    IngredientFoodRef,
    RefFood,
    RefPortion,
)
from menu.db.ingredients import grams
from menu.db.ingredients.seed import seed_ingredient_data

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
    assert grams.to_grams(
        1.0, None, "count", "piece", None, None, "large", conversions
    ) == (50.0, None, "fdc", 4)
    assert grams.to_grams(
        2.0, None, "count", "piece", None, None, "medium", conversions
    ) == (88.0, None, "fdc", 3)


def test_to_grams_count_falls_back_to_seed_weight() -> None:
    """No size: the seeded unit weight converts ("2 eggs" -> 100 g)."""
    assert grams.to_grams(
        2.0, None, "count", "piece", None, None, None, _egg_conversions()
    ) == (100.0, None, "seed", 1)


def test_to_grams_volume_uses_unit_portion() -> None:
    """1 cup egg -> 243 g through the "cup (4.86 large eggs)" row."""
    assert grams.to_grams(
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
    assert grams.to_grams(100.0, None, "volume", "ml", "ml", None, None, rows) == (
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
    grams_value, grams_max, source, conversion_id = grams.to_grams(
        0.36, None, "volume", "ml", "pinch", None, None, [tsp]
    )
    assert grams_value == pytest.approx(0.36 * 6.0 / 4.928921610938, rel=1e-3)
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
    assert grams.to_grams(
        2.0, None, "count", "stick", "sticks", None, None, [stick]
    ) == (226.0, None, "fdc", 6)


def test_to_grams_never_guesses() -> None:
    """No conversion rows: grams stays NULL."""
    assert grams.to_grams(2.0, None, "count", "piece", None, None, None, []) == (
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
    assert grams.to_grams(2.0, 3.0, "volume", "ml", "cups", None, None, [cup]) == (
        250.0,
        375.0,
        "fdc",
        10,
    )
    assert grams.to_grams(100.0, 120.0, "mass", "g", "g", None, None, []) == (
        100.0,
        120.0,
        None,
        None,
    )


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
