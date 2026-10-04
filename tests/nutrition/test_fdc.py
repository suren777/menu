"""FDC bulk import against a hand-picked CSV slice; never downloads."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.database import (
    FoodSource,
    NutrientMap,
    RefFood,
    RefNutrient,
    RefPortion,
)
from menu.nutrition.sources.fdc import import_fdc

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fdc_sample"


def _ref_food(session: Session, source_food_id: str) -> RefFood:
    return session.scalars(
        select(RefFood).where(RefFood.source_food_id == source_food_id)
    ).one()


@pytest.fixture
def counts(session: Session) -> dict[str, int]:
    return import_fdc([FIXTURE_DIR / "sr", FIXTURE_DIR / "survey"], session)


@pytest.mark.usefixtures("counts")
def test_source_row(session: Session) -> None:
    """Attribution is data: the import stamps the source with the
    version and licence food-guru must carry."""
    source = session.scalars(select(FoodSource)).one()
    assert source.name == "fdc"
    assert source.version
    assert source.licence == "public domain"
    assert source.citation


@pytest.mark.usefixtures("counts")
def test_nutrient_map(session: Session) -> None:
    """Both releases' nutrient codes collapse onto the fixed
    vocabulary, with the definition each code means."""
    source = session.scalars(select(FoodSource)).one()
    rows = session.scalars(
        select(NutrientMap).where(NutrientMap.source_id == source.id)
    ).all()
    assert len(rows) == 16


def test_foods(counts: dict[str, int], session: Session) -> None:
    assert counts["foods"] == 3
    assert counts["portions"] == 10
    assert counts["nutrients"] == 16
    egg = _ref_food(session, "171287")
    assert egg.description == "Egg, whole, raw, fresh"
    assert egg.category == "Dairy and Egg Products"
    human = _ref_food(session, "2705383")
    assert human.category == "Human milk"


@pytest.mark.usefixtures("counts")
def test_sr_portions(session: Session) -> None:
    """SR portion rows carry the unit text in `modifier`."""
    egg = _ref_food(session, "171287")
    rows = {
        row.seq_num: row
        for row in session.scalars(
            select(RefPortion).where(RefPortion.ref_food_id == egg.id)
        )
    }
    assert rows[1].unit == "large"
    assert rows[1].gram_weight == 50.0
    assert rows[6].unit == "medium"
    assert rows[6].gram_weight == 44.0
    assert rows[5].unit == "cup (4.86 large eggs)"
    assert rows[5].gram_weight == 243.0


@pytest.mark.usefixtures("counts")
def test_fndds_portion_unit_from_description(session: Session) -> None:
    """FNDDS portion rows put "1 cup" in portion_description; the unit
    is the text after the amount."""
    human = _ref_food(session, "2705383")
    cup = session.scalars(
        select(RefPortion).where(
            RefPortion.ref_food_id == human.id, RefPortion.seq_num == 1
        )
    ).one()
    assert cup.unit == "cup"
    assert cup.amount == 1.0
    assert cup.gram_weight == 246.0


@pytest.mark.usefixtures("counts")
def test_unweighted_portions_skipped(session: Session) -> None:
    """seq 2 is "Quantity not specified" at 0.0 g — dropped."""
    human = _ref_food(session, "2705383")
    rows = session.scalars(
        select(RefPortion).where(RefPortion.ref_food_id == human.id)
    ).all()
    assert {row.seq_num for row in rows} == {1, 3}


@pytest.mark.usefixtures("counts")
def test_sr_nutrients(session: Session) -> None:
    egg = _ref_food(session, "171287")
    values = {
        row.nutrient: row.amount_per_100g
        for row in session.scalars(
            select(RefNutrient).where(RefNutrient.ref_food_id == egg.id)
        )
    }
    assert values == {
        "energy_kcal": 143.0,
        "protein_g": 12.56,
        "fat_g": 9.51,
        "saturated_fat_g": 3.126,
        "carbohydrate_g": 0.72,
        "sugars_g": 0.37,
        "fibre_g": 0.0,
        "sodium_mg": 142.0,
    }


@pytest.mark.usefixtures("counts")
def test_fndds_nutrient_ids(session: Session) -> None:
    """FNDDS keys food_nutrient rows by the old SR nutrient numbers;
    they map onto the same vocabulary as SR Legacy's."""
    milk = _ref_food(session, "2705384")
    values = {
        row.nutrient: row.amount_per_100g
        for row in session.scalars(
            select(RefNutrient).where(RefNutrient.ref_food_id == milk.id)
        )
    }
    assert values == {
        "energy_kcal": 52.0,
        "protein_g": 3.33,
        "fat_g": 2.14,
        "saturated_fat_g": 1.249,
        "carbohydrate_g": 4.83,
        "sugars_g": 4.88,
        "fibre_g": 0.0,
        "sodium_mg": 39.0,
    }


def test_reimport_is_idempotent(counts: dict[str, int], session: Session) -> None:
    again = import_fdc([FIXTURE_DIR / "sr", FIXTURE_DIR / "survey"], session)
    assert again == counts
    assert len(session.scalars(select(RefFood)).all()) == 3
