"""FDC bulk import against a hand-picked CSV slice; never downloads."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.database import FdcFood, FdcNutrient, FdcPortion
from menu.nutrition.fdc import import_fdc

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fdc_sample"


@pytest.fixture
def counts(session: Session) -> dict[str, int]:
    return import_fdc(
        [FIXTURE_DIR / "sr", FIXTURE_DIR / "survey"], session
    )


def test_foods(counts: dict[str, int], session: Session) -> None:
    assert counts["foods"] == 3
    assert counts["portions"] == 10
    assert counts["nutrients"] == 16
    egg = session.scalars(select(FdcFood).where(FdcFood.fdc_id == 171287)).one()
    assert egg.description == "Egg, whole, raw, fresh"
    assert egg.data_type == "sr_legacy_food"
    assert egg.category == "Dairy and Egg Products"
    human = session.scalars(select(FdcFood).where(FdcFood.fdc_id == 2705383)).one()
    assert human.data_type == "survey_fndds_food"
    assert human.category == "Human milk"


@pytest.mark.usefixtures("counts")
def test_sr_portions(session: Session) -> None:
    """SR portion rows carry the unit text in `modifier`."""
    egg = {
        row.seq_num: row
        for row in session.scalars(
            select(FdcPortion).where(FdcPortion.fdc_id == 171287)
        )
    }
    assert egg[1].unit == "large"
    assert egg[1].gram_weight == 50.0
    assert egg[6].unit == "medium"
    assert egg[6].gram_weight == 44.0
    assert egg[5].unit == "cup (4.86 large eggs)"
    assert egg[5].gram_weight == 243.0


@pytest.mark.usefixtures("counts")
def test_fndds_portion_unit_from_description(session: Session) -> None:
    """FNDDS portion rows put "1 cup" in portion_description; the unit
    is the text after the amount."""
    cup = session.scalars(
        select(FdcPortion).where(FdcPortion.fdc_id == 2705383, FdcPortion.seq_num == 1)
    ).one()
    assert cup.unit == "cup"
    assert cup.amount == 1.0
    assert cup.gram_weight == 246.0


@pytest.mark.usefixtures("counts")
def test_unweighted_portions_skipped(session: Session) -> None:
    """seq 2 is "Quantity not specified" at 0.0 g — dropped."""
    rows = session.scalars(
        select(FdcPortion).where(FdcPortion.fdc_id == 2705383)
    ).all()
    assert {row.seq_num for row in rows} == {1, 3}


@pytest.mark.usefixtures("counts")
def test_sr_nutrients(session: Session) -> None:
    egg = {
        row.nutrient: row.amount_per_100g
        for row in session.scalars(
            select(FdcNutrient).where(FdcNutrient.fdc_id == 171287)
        )
    }
    assert egg == {
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
    milk = {
        row.nutrient: row.amount_per_100g
        for row in session.scalars(
            select(FdcNutrient).where(FdcNutrient.fdc_id == 2705384)
        )
    }
    assert milk == {
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
    assert len(session.scalars(select(FdcFood)).all()) == 3
