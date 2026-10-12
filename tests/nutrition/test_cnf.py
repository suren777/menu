"""CNF bulk import against a hand-picked CSV slice; never downloads."""

import io
import logging
import shutil
import zipfile
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
    RefPortion,
)
from menu.nutrition.sources import cnf
from menu.nutrition.sources.cnf import CNF_URL, _parse_measure, import_cnf

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    """ "1 medium egg" is a medium (unit) egg (modifier); "2 large
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
    """ "1/2 back" parses the fraction into the amount."""
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
        # a bare number leaves conversion no unit to match
        ("2", None),
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
    food = _ref_food(session, "125")
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

    import_cnf(FIXTURE_DIR, session)

    assert (
        dict(session.execute(select(RefFood.source_food_id, RefFood.id)).all())
        == before
    )
    session.expire_all()
    resolved = session.get(IngredientFoodRef, mapping.id)
    assert resolved is not None
    pinned = session.get(RefFood, resolved.ref_food_id)
    assert pinned is not None
    assert pinned.source_food_id == "125"


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
    kept_food = _ref_food(session, "600")
    session.add(
        IngredientFoodRef(
            ingredient_id=chicken.id,
            ref_food_id=kept_food.id,
            source_id=kept_food.source_id,
            role="nutrition",
            status="confirmed",
            origin="seed",
        )
    )
    session.flush()

    with caplog.at_level(logging.WARNING):
        counts = import_cnf(_release_without("600", tmp_path), session)
    assert counts["kept_missing"] == 1
    assert "600" in caplog.text
    assert _ref_food(session, "600").id == kept_food.id

    import_cnf(_release_without("125", tmp_path), session)
    assert (
        session.scalars(select(RefFood).where(RefFood.source_food_id == "125")).first()
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

    monkeypatch.setattr("menu.nutrition.sources.cnf.requests.get", get)
    monkeypatch.setattr("menu.nutrition.sources.cnf.CACHE_DIR", tmp_path / "cache")
    return payloads


def test_download_writes_the_release_into_the_cache(
    fake_get: dict[str, bytes],
) -> None:
    fake_get[CNF_URL] = b"zip-bytes"
    target = cnf._download(CNF_URL)
    assert target == cnf.CACHE_DIR / Path(CNF_URL).name
    assert target.read_bytes() == b"zip-bytes"

    fake_get.clear()  # a second fetch would KeyError: the cache must serve
    assert cnf._download(CNF_URL) == target


def test_extract_unzips_next_to_the_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("menu.nutrition.sources.cnf.CACHE_DIR", tmp_path)
    archive_path = tmp_path / "release.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("release/FOOD NAME.csv", "FoodID,FoodDescription\n2,Food\n")

    dataset_dir = cnf._extract(archive_path)
    assert dataset_dir == tmp_path / "release"
    assert (dataset_dir / "FOOD NAME.csv").exists()

    (dataset_dir / "FOOD NAME.csv").unlink()
    assert cnf._extract(archive_path) == dataset_dir
    assert not (dataset_dir / "FOOD NAME.csv").exists()  # not re-extracted


def test_unusable_factor_and_nutrient_rows_are_skipped(
    tmp_path: Path, session: Session
) -> None:
    """A conversion factor whose measure has no leading amount, and
    nutrient rows with an unknown id or no value, are skipped rather
    than stored."""
    release = tmp_path / "cnf"
    shutil.copytree(FIXTURE_DIR, release)
    with (release / "CONVERSION FACTOR.csv").open("a", encoding="latin-1") as handle:
        handle.write("2,999,0.5,2000-01-01\n")  # measure 999: not in MEASURE NAME
    with (release / "NUTRIENT AMOUNT.csv").open("a", encoding="latin-1") as handle:
        handle.write("2,9999,1.0,0,0,102,2010-04-16\n")  # unknown nutrient id
        handle.write("2,203,,0,0,102,2010-04-16\n")  # no value

    counts = import_cnf(release, session)
    assert counts == {"foods": 3, "portions": 18, "nutrients": 24, "kept_missing": 0}


def test_main_downloads_extracts_and_imports(
    fake_get: dict[str, bytes],
    session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The release downloads (faked), unzips into the cache and imports
    into the database the use_engine override points at."""
    fake_get[CNF_URL] = _release_zip(FIXTURE_DIR, Path(CNF_URL).stem)

    cnf.main()

    assert "CNF imported: 3 foods, 18 portions, 24 nutrients" in capsys.readouterr().out
    assert session.scalar(select(func.count()).select_from(RefFood)) == 3
