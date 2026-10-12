"""FDC bulk import against a hand-picked CSV slice; never downloads."""

import io
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientConversion,
    IngredientFoodRef,
    NutrientMap,
    RefFood,
    RefNutrient,
    RefPortion,
)
from menu.nutrition.sources import fdc
from menu.nutrition.sources.fdc import FNDDS_URL, SR_URL, import_fdc

if TYPE_CHECKING:
    from collections.abc import Iterator

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


@pytest.mark.usefixtures("counts")
def test_reimport_keeps_food_ids(session: Session) -> None:
    """A confirmed mapping pins its food: the re-import upserts the
    ref_food rows, so ids — and what the mapping points at — never
    move."""
    before = dict(session.execute(select(RefFood.source_food_id, RefFood.id)).all())
    egg = Ingredient(name="egg")
    session.add(egg)
    session.flush()
    food = _ref_food(session, "171287")
    mapping = IngredientFoodRef(
        ingredient_id=egg.id,
        ref_food_id=food.id,
        source_id=food.source_id,
        role="nutrition",
        status="confirmed",
        origin="seed",
    )
    session.add(mapping)
    session.flush()

    import_fdc([FIXTURE_DIR / "sr", FIXTURE_DIR / "survey"], session)

    assert (
        dict(session.execute(select(RefFood.source_food_id, RefFood.id)).all())
        == before
    )
    session.expire_all()
    resolved = session.get(IngredientFoodRef, mapping.id)
    assert resolved is not None
    pinned = session.get(RefFood, resolved.ref_food_id)
    assert pinned is not None
    assert pinned.source_food_id == "171287"


@pytest.mark.usefixtures("counts")
def test_reimport_with_derived_conversions(session: Session) -> None:
    """Derived conversion rows name the portion they came from, but a
    re-import replaces portions, so that provenance must not block it."""
    egg = Ingredient(name="egg")
    session.add(egg)
    session.flush()
    portion = session.scalars(
        select(RefPortion).where(
            RefPortion.ref_food_id == _ref_food(session, "171287").id
        )
    ).first()
    assert portion is not None
    session.add(
        IngredientConversion(
            ingredient_id=egg.id,
            kind="portion",
            unit=portion.unit or "piece",
            grams=portion.gram_weight,
            origin="fdc",
            ref_food_id=portion.ref_food_id,
            ref_seq_num=portion.seq_num,
        )
    )
    session.flush()

    import_fdc([FIXTURE_DIR / "sr", FIXTURE_DIR / "survey"], session)
    session.flush()


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


def _release_zip(directory: Path, name: str) -> bytes:
    """A release download: a zip whose entries sit under `name`/ — the
    layout _extract unpacks into the dataset directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.iterdir()):
            archive.write(path, arcname=f"{name}/{path.name}")
    return buffer.getvalue()


@pytest.fixture
def fake_get(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, bytes]:
    """`requests.get` answered from a dict, the importer's cache pointed
    at tmp_path: nothing touches the network or the real .cache."""
    payloads: dict[str, bytes] = {}

    def get(url: str, **_kwargs: object) -> FakeResponse:
        return FakeResponse(payloads[url])

    monkeypatch.setattr("menu.nutrition.sources.fdc.requests.get", get)
    monkeypatch.setattr("menu.nutrition.sources.fdc.CACHE_DIR", tmp_path / "cache")
    return payloads


def test_download_writes_the_release_into_the_cache(
    fake_get: dict[str, bytes],
) -> None:
    fake_get[SR_URL] = b"zip-bytes"
    target = fdc._download(SR_URL)
    assert target == fdc.CACHE_DIR / Path(SR_URL).name
    assert target.read_bytes() == b"zip-bytes"


def test_download_reuses_the_cached_file(fake_get: dict[str, bytes]) -> None:
    fake_get[SR_URL] = b"zip-bytes"
    first = fdc._download(SR_URL)
    fake_get.clear()  # a second fetch would KeyError: the cache must serve
    assert fdc._download(SR_URL) == first


def test_extract_unzips_next_to_the_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("menu.nutrition.sources.fdc.CACHE_DIR", tmp_path)
    archive_path = tmp_path / "release.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("release/food.csv", "fdc_id,description\n171287,Egg\n")

    dataset_dir = fdc._extract(archive_path)
    assert dataset_dir == tmp_path / "release"
    assert (dataset_dir / "food.csv").read_text() == "fdc_id,description\n171287,Egg\n"

    (dataset_dir / "food.csv").unlink()
    assert fdc._extract(archive_path) == dataset_dir
    assert not (dataset_dir / "food.csv").exists()  # not re-extracted


def test_release_without_optional_files_and_unusable_rows(
    tmp_path: Path, session: Session
) -> None:
    """No measure-unit or category file: portions keep the modifier and
    the category stays NULL. A row with an unknown nutrient code, no
    amount, or no unit anywhere is skipped, not stored."""
    minimal = tmp_path / "minimal"
    minimal.mkdir()
    shutil.copy(FIXTURE_DIR / "sr" / "food.csv", minimal / "food.csv")
    (minimal / "food_nutrient.csv").write_text(
        (FIXTURE_DIR / "sr" / "food_nutrient.csv").read_text()
        + '"2","171287","9999","5","0","1","","","","",""\n'  # unknown code
        + '"3","171287","1008","","0","1","","","","",""\n'  # no amount
    )
    (minimal / "food_portion.csv").write_text(
        (FIXTURE_DIR / "sr" / "food_portion.csv").read_text()
        + '"1","171287","9","1","","2","","10","0","","",""\n'  # no unit anywhere
    )

    counts = import_fdc([minimal], session)
    assert counts == {"foods": 1, "portions": 6, "nutrients": 8, "kept_missing": 0}
    egg = _ref_food(session, "171287")
    assert egg.category is None
    assert {row.seq_num for row in session.scalars(select(RefPortion)).all()} == {
        1,
        2,
        3,
        5,
        6,
        7,
    }


def test_main_downloads_extracts_and_imports(
    fake_get: dict[str, bytes],
    session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both releases download (faked), unzip into the cache and import
    into the database the use_engine override points at."""
    fake_get[SR_URL] = _release_zip(FIXTURE_DIR / "sr", Path(SR_URL).stem)
    fake_get[FNDDS_URL] = _release_zip(FIXTURE_DIR / "survey", Path(FNDDS_URL).stem)

    fdc.main()

    assert "FDC imported: 3 foods, 10 portions, 16 nutrients" in capsys.readouterr().out
    assert session.scalar(select(func.count()).select_from(RefFood)) == 3
