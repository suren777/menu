"""CNF bulk import against a hand-picked CSV slice; never downloads."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    NutrientMap,
    RefFood,
    RefNutrient,
    RefPortion,
)
from menu.nutrition.sources.cnf import _parse_measure, import_cnf

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cnf_sample"


def _ref_food(session: Session, source_food_id: str) -> RefFood:
    return session.scalars(
        select(RefFood).where(RefFood.source_food_id == source_food_id)
    ).one()


def _portions_of(session: Session, food: RefFood) -> dict[int, RefPortion]:
    return {
        row.seq_num: row
        for row in session.scalars(
            select(RefPortion).where(RefPortion.ref_food_id == food.id)
        )
    }


@pytest.fixture
def counts(session: Session) -> dict[str, int]:
    return import_cnf(FIXTURE_DIR, session)


@pytest.mark.usefixtures("counts")
def test_source_row(session: Session) -> None:
    """Attribution is data: the import stamps the source with the
    version and licence food-guru must carry."""
    source = session.scalars(select(FoodSource)).one()
    assert source.name == "cnf"
    assert source.version == "CNF 2015"
    assert source.licence == (
        "Health Canada: free use with attribution; values must not be modified"
    )
    assert source.citation == (
        "Canadian Nutrient File (CNF), 2015. Health Canada. "
        "Values are reproduced unmodified."
    )


@pytest.mark.usefixtures("counts")
def test_nutrient_map(session: Session) -> None:
    """CNF's nutrient ids collapse onto the fixed vocabulary, with the
    definition each code means."""
    source = session.scalars(select(FoodSource)).one()
    rows = session.scalars(
        select(NutrientMap).where(NutrientMap.source_id == source.id)
    ).all()
    assert {row.source_nutrient_code for row in rows} == {
        "203",
        "204",
        "205",
        "208",
        "269",
        "291",
        "307",
        "606",
    }


def test_foods(counts: dict[str, int], session: Session) -> None:
    assert counts["foods"] == 3
    assert counts["portions"] == 18
    assert counts["nutrients"] == 24
    egg = _ref_food(session, "125")
    assert egg.description == "Egg, chicken, whole, fresh or frozen, raw"
    assert egg.category == "Dairy and Egg Products"
    assert egg.refuse_pct == 12.0
    chicken = _ref_food(session, "600")
    assert chicken.category == "Poultry Products"
    # the first refuse row per food wins (33, not 13 or 46)
    assert chicken.refuse_pct == 33.0
    souffle = _ref_food(session, "2")
    assert souffle.refuse_pct == 0.0


@pytest.mark.usefixtures("counts")
def test_nutrients(session: Session) -> None:
    """Exact values read from the real release, with the definition
    each code means."""
    egg = _ref_food(session, "125")
    rows = session.scalars(
        select(RefNutrient).where(RefNutrient.ref_food_id == egg.id)
    ).all()
    values = {row.nutrient: (row.amount_per_100g, row.definition) for row in rows}
    assert values == {
        "energy_kcal": (145.0, "kcal"),
        "protein_g": (11.8, "by_weight"),
        "fat_g": (10.01, "by_weight"),
        "saturated_fat_g": (3.043, "by_weight"),
        "carbohydrate_g": (0.98, "by_difference"),
        "sugars_g": (0.77, "total"),
        "fibre_g": (0.0, "total_dietary"),
        "sodium_mg": (125.0, "by_weight"),
    }


@pytest.mark.usefixtures("counts")
def test_ml_portion(session: Session) -> None:
    """A number glued to its unit splits into amount and unit; the
    gram weight is the conversion factor x 100."""
    souffle = _ref_food(session, "2")
    row = _portions_of(session, souffle)[1]
    assert row.amount == 100.0
    assert row.unit == "ml"
    assert row.modifier is None
    assert row.gram_weight == pytest.approx(40.152)  # 0.40152 x 100


@pytest.mark.usefixtures("counts")
def test_count_portion(session: Session) -> None:
    """"1 medium egg" is a medium (unit) egg (modifier); "2 large
    eggs" keeps its amount."""
    egg = _ref_food(session, "125")
    rows = _portions_of(session, egg)
    medium = next(row for row in rows.values() if row.unit == "medium")
    assert medium.amount == 1.0
    assert medium.modifier == "egg"
    assert medium.gram_weight == pytest.approx(46.39)  # 0.46390 x 100
    eggs = next(row for row in rows.values() if row.amount == 2.0)
    assert eggs.unit == "large"
    assert eggs.modifier == "eggs"
    assert eggs.gram_weight == pytest.approx(105.36)  # 1.05360 x 100


@pytest.mark.usefixtures("counts")
def test_fraction_portion(session: Session) -> None:
    """"1/2 back" parses the fraction into the amount."""
    chicken = _ref_food(session, "600")
    row = _portions_of(session, chicken)[1]
    assert row.amount == 0.5
    assert row.unit == "back"
    assert row.modifier is None
    assert row.gram_weight == pytest.approx(58.0)  # 0.58 x 100


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("100ml", (100.0, "ml", None)),
        ("125ml", (125.0, "ml", None)),
        ("100ml chopped", (100.0, "ml", "chopped")),
        ("1 medium", (1.0, "medium", None)),
        ("2 tablespoons", (2.0, "tbsp", None)),
        ("1/2 back", (0.5, "back", None)),
        ("1/6 cake (20cm dia)", (0.1667, "cake", "(20cm dia)")),
        ("1 fish (500 g)", (1.0, "fish", "(500 g)")),
        ("250ml water + powder", (250.0, "ml", "water + powder")),
        # real descriptions conversion can never match on
        ("dough for 1", None),
        ("kellogg's", None),
        ("", None),
    ],
)
def test_parse_measure(
    description: str, expected: tuple[float, str, str | None] | None
) -> None:
    assert _parse_measure(description) == expected


def test_reimport_is_idempotent(counts: dict[str, int], session: Session) -> None:
    again = import_cnf(FIXTURE_DIR, session)
    assert again == counts
    assert len(session.scalars(select(RefFood)).all()) == 3


@pytest.mark.usefixtures("counts")
def test_reimport_keeps_food_ids(session: Session) -> None:
    """A confirmed mapping pins its food: the re-import upserts the
    ref_food rows, so ids — and what the mapping points at — never
    move."""
    before = dict(session.execute(select(RefFood.source_food_id, RefFood.id)).all())
    egg = Ingredient(name="egg")
    session.add(egg)
    session.flush()
    mapping = IngredientFoodRef(
        ingredient_id=egg.id,
        ref_food_id=_ref_food(session, "125").id,
        role="nutrition",
        confirmed=True,
    )
    session.add(mapping)
    session.flush()

    import_cnf(FIXTURE_DIR, session)

    assert dict(
        session.execute(select(RefFood.source_food_id, RefFood.id)).all()
    ) == before
    session.expire_all()
    resolved = session.get(IngredientFoodRef, mapping.id)
    assert resolved is not None
    food = session.get(RefFood, resolved.ref_food_id)
    assert food is not None
    assert food.source_food_id == "125"


def _release_without(food_id: str, target: Path) -> Path:
    """A copy of the fixture release minus one food: the "next
    release" for the stale-food test."""
    variant = target / f"cnf_without_{food_id}"
    variant.mkdir()
    for path in FIXTURE_DIR.glob("*.csv"):
        rows = path.read_text(encoding="latin-1").splitlines(keepends=True)
        if rows and rows[0].startswith("FoodID,"):
            head, data = rows[0], rows[1:]
            rows = [head, *(row for row in data if not row.startswith(f"{food_id},"))]
        (variant / path.name).write_text("".join(rows), encoding="latin-1")
    return variant


@pytest.mark.usefixtures("counts")
def test_stale_food_deleted_or_kept(
    session: Session, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A food the release dropped is deleted — unless a confirmed
    mapping still references it: then it is kept and warned about."""
    chicken = Ingredient(name="chicken")
    session.add(chicken)
    session.flush()
    kept_id = _ref_food(session, "600").id
    session.add(
        IngredientFoodRef(
            ingredient_id=chicken.id,
            ref_food_id=kept_id,
            role="nutrition",
            confirmed=True,
        )
    )
    session.flush()

    with caplog.at_level(logging.WARNING):
        counts = import_cnf(_release_without("600", tmp_path), session)
    assert counts["kept_missing"] == 1
    assert "600" in caplog.text
    assert _ref_food(session, "600").id == kept_id

    import_cnf(_release_without("125", tmp_path), session)
    assert (
        session.scalars(
            select(RefFood).where(RefFood.source_food_id == "125")
        ).first()
        is None
    )


def test_fdc_rows_untouched(session: Session) -> None:
    """A CNF import replaces only CNF rows: an FDC food already in the
    tables survives it."""
    fdc = FoodSource(
        name="fdc",
        version="SR Legacy 2018-04; FNDDS 2024-10-31",
        licence="public domain",
        citation="USDA FoodData Central, fdc.nal.usda.gov",
    )
    session.add(fdc)
    session.flush()
    session.add(
        RefFood(
            source_id=fdc.id,
            source_food_id="171287",
            description="Egg, whole, raw, fresh",
        )
    )
    session.flush()
    counts = import_cnf(FIXTURE_DIR, session)
    assert counts["foods"] == 3
    assert (
        session.scalars(select(RefFood).where(RefFood.source_id == fdc.id))
        .one()
        .description
        == "Egg, whole, raw, fresh"
    )
    # the nutrient_map replacement only touched CNF's own rows
    assert len(session.scalars(select(NutrientMap)).all()) == 8
