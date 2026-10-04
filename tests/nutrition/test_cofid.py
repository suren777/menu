"""CoFID bulk import against a hand-picked workbook slice; never downloads."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.database import FoodSource, NutrientMap, RefFood, RefNutrient
from menu.nutrition.sources.cofid import import_cofid

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FIXTURE = Path(__file__).parent / "fixtures" / "cofid_sample.xlsx"


def _ref_food(session: Session, source_food_id: str) -> RefFood:
    return session.scalars(
        select(RefFood).where(RefFood.source_food_id == source_food_id)
    ).one()


def _values(session: Session, food: RefFood) -> dict[str, float]:
    return {
        row.nutrient: row.amount_per_100g
        for row in session.scalars(
            select(RefNutrient).where(RefNutrient.ref_food_id == food.id)
        )
    }


@pytest.fixture
def counts(session: Session) -> dict[str, int]:
    return import_cofid(FIXTURE, session)


@pytest.mark.usefixtures("counts")
def test_source_row(session: Session) -> None:
    """Attribution is data: the import stamps the source with the
    version and licence food-guru must carry."""
    source = session.scalars(select(FoodSource)).one()
    assert source.name == "cofid"
    assert source.version == "CoFID 2021"
    assert source.licence == "Open Government Licence v3.0"
    assert "Open Government Licence v3.0" in source.citation


@pytest.mark.usefixtures("counts")
def test_nutrient_map(session: Session) -> None:
    """The CoFID codes collapse onto the fixed vocabulary, and CHO's
    definition records the available-monosaccharide basis — CoFID's
    carbohydrate is not FDC's by-difference figure."""
    source = session.scalars(select(FoodSource)).one()
    rows = session.scalars(
        select(NutrientMap).where(NutrientMap.source_id == source.id)
    ).all()
    assert {
        (row.source_nutrient_code, row.nutrient, row.definition) for row in rows
    } == {
        ("KCALS", "energy_kcal", "kcal"),
        ("PROT", "protein_g", "by_weight"),
        ("FAT", "fat_g", "by_weight"),
        ("SATFOD", "saturated_fat_g", "by_weight"),
        ("CHO", "carbohydrate_g", "available_monosaccharide"),
        ("TOTSUG", "sugars_g", "total"),
        ("AOACFIB", "fibre_g", "total_dietary"),
        ("NA", "sodium_mg", "by_weight"),
    }


def test_foods(counts: dict[str, int], session: Session) -> None:
    assert counts == {"foods": 3, "portions": 0, "nutrients": 22}
    milk = _ref_food(session, "12-320")
    assert milk.description == "Milk, whole, UHT"
    assert milk.category == "BAK"


@pytest.mark.usefixtures("counts")
def test_milk_nutrients(session: Session) -> None:
    """Values read off the real workbook's 12-320 row."""
    milk = _ref_food(session, "12-320")
    assert _values(session, milk) == {
        "energy_kcal": 66.0,
        "protein_g": 3.2,
        "fat_g": 3.9,
        "saturated_fat_g": 2.36,
        "carbohydrate_g": 4.8,
        "sugars_g": 4.8,
        "fibre_g": 0.0,
        "sodium_mg": 55.0,
    }


@pytest.mark.usefixtures("counts")
def test_trace_is_zero(session: Session) -> None:
    """A "Tr" (trace) value stores as 0.0: CoFID traces the almond's
    sodium."""
    almonds = _ref_food(session, "14-898")
    assert _values(session, almonds)["sodium_mg"] == 0.0


@pytest.mark.usefixtures("counts")
def test_not_measured_has_no_row(session: Session) -> None:
    """An "N" (not measured) or blank value carries no ref_nutrient
    row: the ackee's saturated fat is "N" and its AOAC fibre blank."""
    ackee = _ref_food(session, "13-145")
    values = _values(session, ackee)
    assert set(values) == {
        "energy_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
        "sugars_g",
        "sodium_mg",
    }
    assert values["energy_kcal"] == 151.0
    assert values["sodium_mg"] == 240.0


@pytest.mark.usefixtures("counts")
def test_refuse_pct(session: Session) -> None:
    """Edible proportion becomes refuse: the almonds' 0.37 keeps 63 %
    refuse, the milk's "1.00" keeps 0, and the ackee's "N" stays
    NULL."""
    almonds = _ref_food(session, "14-898")
    milk = _ref_food(session, "12-320")
    ackee = _ref_food(session, "13-145")
    assert almonds.refuse_pct == 63.0
    assert milk.refuse_pct == 0.0
    assert ackee.refuse_pct is None


def test_reimport_is_idempotent(counts: dict[str, int], session: Session) -> None:
    again = import_cofid(FIXTURE, session)
    assert again == counts
    assert len(session.scalars(select(RefFood)).all()) == 3


def test_fdc_rows_untouched(session: Session) -> None:
    """A CoFID import replaces only its own source's rows."""
    fdc = FoodSource(
        name="fdc",
        version="SR Legacy 2018-04; FNDDS 2024-10-31",
        licence="public domain",
        citation="USDA FoodData Central, fdc.nal.usda.gov",
    )
    session.add(fdc)
    session.flush()
    egg = RefFood(
        source_id=fdc.id,
        source_food_id="171287",
        description="Egg, whole, raw, fresh",
        category="Dairy and Egg Products",
    )
    session.add(egg)
    session.flush()

    assert import_cofid(FIXTURE, session)["foods"] == 3
    assert egg.description == "Egg, whole, raw, fresh"
    assert session.scalars(select(RefFood).where(RefFood.source_id == fdc.id)).one()
    egg_nutrients = session.scalars(
        select(RefNutrient).where(RefNutrient.ref_food_id == egg.id)
    ).all()
    assert egg_nutrients == []
    assert len(session.scalars(select(RefFood)).all()) == 4
