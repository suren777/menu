"""CoFID bulk import against a hand-picked workbook slice; never downloads."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    NutrientMap,
    RefFood,
    RefNutrient,
)
from menu.nutrition.sources import cofid
from menu.nutrition.sources.cofid import COFID_URL, import_cofid

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    assert counts == {
        "foods": 3,
        "portions": 0,
        "nutrients": 22,
        "kept_missing": 0,
    }
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


@pytest.mark.usefixtures("counts")
def test_reimport_keeps_food_ids(session: Session) -> None:
    """A confirmed mapping pins its food: the re-import upserts the
    ref_food rows, so ids — and what the mapping points at — never
    move."""
    before = dict(session.execute(select(RefFood.source_food_id, RefFood.id)).all())
    milk = Ingredient(name="milk")
    session.add(milk)
    session.flush()
    food = _ref_food(session, "12-320")
    mapping = IngredientFoodRef(
        ingredient_id=milk.id,
        ref_food_id=food.id,
        source_id=food.source_id,
        role="nutrition",
        status="confirmed",
        origin="seed",
    )
    session.add(mapping)
    session.flush()

    import_cofid(FIXTURE, session)

    assert (
        dict(session.execute(select(RefFood.source_food_id, RefFood.id)).all())
        == before
    )
    session.expire_all()
    resolved = session.get(IngredientFoodRef, mapping.id)
    assert resolved is not None
    pinned = session.get(RefFood, resolved.ref_food_id)
    assert pinned is not None
    assert pinned.source_food_id == "12-320"


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


class FakeResponse:
    """requests.Response stand-in: one payload, usable as a block."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for start in range(0, len(self.payload), chunk_size):
            yield self.payload[start : start + chunk_size]


@pytest.fixture
def fake_get(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, bytes]:
    """`requests.get` answered from a dict, the importer's cache pointed
    at tmp_path: nothing touches the network or the real .cache."""
    payloads: dict[str, bytes] = {}

    def get(url: str, **_kwargs: object) -> FakeResponse:
        return FakeResponse(payloads[url])

    monkeypatch.setattr("menu.nutrition.sources.cofid.requests.get", get)
    monkeypatch.setattr("menu.nutrition.sources.cofid.CACHE_DIR", tmp_path / "cache")
    return payloads


def test_download_writes_the_workbook_into_the_cache(
    fake_get: dict[str, bytes],
) -> None:
    fake_get[COFID_URL] = FIXTURE.read_bytes()
    path = cofid._download(COFID_URL)
    assert path == cofid.CACHE_DIR / Path(COFID_URL).name
    assert path.read_bytes() == FIXTURE.read_bytes()

    fake_get.clear()  # a second fetch would KeyError: the cache must serve
    assert cofid._download(COFID_URL) == path


def test_main_downloads_and_imports(
    fake_get: dict[str, bytes],
    session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The workbook downloads (faked) into the cache and imports into
    the database the use_engine override points at."""
    fake_get[COFID_URL] = FIXTURE.read_bytes()

    cofid.main()

    assert (
        "CoFID imported: 3 foods, 0 portions, 22 nutrients" in capsys.readouterr().out
    )
    assert session.scalar(select(func.count()).select_from(RefFood)) == 3
