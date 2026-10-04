"""FDC reference data: one-off bulk import into the common ref tables.

Downloads the public-domain USDA FoodData Central SR Legacy and FNDDS
CSV releases once into .cache/sources/fdc/ and loads the slices the
nutrition work needs into ref_food / ref_portion / ref_nutrient,
tagged with a food_source row and a nutrient_map. Bulk files, not a
crawl, so no politeness rules apply — and nothing here runs at ingest
time: run `uv run menu-fdc-import` to (re)build the tables.
"""

import csv
import logging
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from sqlalchemy import delete, insert, select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    NutrientMap,
    RefFood,
    RefNutrient,
    RefPortion,
    initialise,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CACHE_DIR = Path(".cache/sources/fdc")
SOURCE_NAME = "fdc"
SOURCE_VERSION = "SR Legacy 2018-04; FNDDS 2024-10-31"

SR_URL = (
    "https://fdc.nal.usda.gov/fdc-datasets/"
    "FoodData_Central_sr_legacy_food_csv_2018-04.zip"
)
FNDDS_URL = (
    "https://fdc.nal.usda.gov/fdc-datasets/"
    "FoodData_Central_survey_food_csv_2024-10-31.zip"
)

# Fixed nutrient set, matching what BBC and King Arthur publish so
# derived values validate against published ones like for like. The
# two releases key food_nutrient rows differently: SR Legacy by the
# FDC nutrient ids (1003...), FNDDS by the old SR nutrient numbers
# (203...); nutrient.csv's nutrient_nbr column pairs them up. Both
# collapse onto our fixed vocabulary through one explicit map, with
# the definition each code means.
NUTRIENT_MAP: list[tuple[str, str, str]] = [
    # (source code, our nutrient, definition)
    ("1008", "energy_kcal", "kcal"),
    ("208", "energy_kcal", "kcal"),
    ("1003", "protein_g", "by_weight"),
    ("203", "protein_g", "by_weight"),
    ("1004", "fat_g", "by_weight"),
    ("204", "fat_g", "by_weight"),
    ("1258", "saturated_fat_g", "by_weight"),
    ("606", "saturated_fat_g", "by_weight"),
    ("1005", "carbohydrate_g", "by_difference"),
    ("205", "carbohydrate_g", "by_difference"),
    ("2000", "sugars_g", "total"),
    ("269", "sugars_g", "total"),
    ("1079", "fibre_g", "total_dietary"),
    ("291", "fibre_g", "total_dietary"),
    ("1093", "sodium_mg", "by_weight"),
    ("307", "sodium_mg", "by_weight"),
]

# SR portion rows all carry measure_unit_id 9999 ("undetermined") with
# the real unit text in `modifier` ("large", "cup (4.86 large eggs)");
# FNDDS rows carry "1 cup"-style text in portion_description and an
# internal numeric modifier id.
_LEADING_AMOUNT = re.compile(r"^\s*\d+(?:\.\d+)?\s*")

CHUNK = 10_000


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def _download(url: str) -> Path:
    target = CACHE_DIR / url.rsplit("/", 1)[-1]
    if not target.exists():
        logger.info("Downloading %s", url)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=300) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    handle.write(chunk)
    return target


def _extract(archive_path: Path) -> Path:
    dataset_dir = archive_path.with_suffix("")
    if not dataset_dir.is_dir():
        logger.info("Extracting %s", archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(CACHE_DIR)
    return dataset_dir


def _measure_units(dataset_dir: Path) -> dict[int, str]:
    path = dataset_dir / "measure_unit.csv"
    if not path.exists():
        return {}
    return {int(row["id"]): row["name"] for row in _rows(path)}


def _categories(dataset_dir: Path) -> dict[str, str]:
    """Category id -> description; food.csv's food_category_id joins
    on id. SR ships food_category.csv, FNDDS wweia_food_category.csv."""
    wweia = dataset_dir / "wweia_food_category.csv"
    if wweia.exists():
        return {
            row["wweia_food_category"]: row["wweia_food_category_description"]
            for row in _rows(wweia)
        }
    standard = dataset_dir / "food_category.csv"
    if standard.exists():
        return {row["id"]: row["description"] for row in _rows(standard)}
    return {}


def _portions(
    dataset_dir: Path, measure_units: dict[int, str]
) -> Iterator[dict[str, Any]]:
    """Portion rows with a usable unit: the SR `modifier`, or for
    FNDDS the portion description's unit ("1 cup" -> "cup"). Rows with
    no weight ("Quantity not specified", 0.0 g) are skipped."""
    for row in _rows(dataset_dir / "food_portion.csv"):
        if not float(row["gram_weight"] or 0):
            continue
        unit = measure_units.get(int(row["measure_unit_id"] or 0), "")
        if not unit or unit == "undetermined":
            unit = row["modifier"]
        if not unit or unit.isdigit():
            unit = _LEADING_AMOUNT.sub("", row["portion_description"])
        if not unit:
            continue
        description = row["portion_description"]
        match = _LEADING_AMOUNT.match(description)
        yield {
            "fdc_id": int(row["fdc_id"]),
            "seq_num": int(row["seq_num"]),
            "amount": float(row["amount"])
            if row["amount"]
            else (float(match.group()) if match else None),
            "unit": unit,
            "modifier": row["modifier"] or None,
            "gram_weight": float(row["gram_weight"]),
        }


def _food_source(session: Session) -> FoodSource:
    source = session.scalar(select(FoodSource).where(FoodSource.name == SOURCE_NAME))
    if source is None:
        source = FoodSource(
            name=SOURCE_NAME,
            version=SOURCE_VERSION,
            licence="public domain",
            citation="USDA FoodData Central, fdc.nal.usda.gov",
        )
        session.add(source)
        session.flush()
    return source


def import_fdc(dataset_dirs: list[Path], session: Session) -> dict[str, int]:
    """Replace the FDC rows in the common ref tables with the slices
    of the extracted release directories. Returns the row counts."""
    source = _food_source(session)
    session.execute(delete(NutrientMap).where(NutrientMap.source_id == source.id))
    session.execute(
        insert(NutrientMap),
        [
            {
                "source_id": source.id,
                "source_nutrient_code": code,
                "nutrient": nutrient,
                "definition": definition,
            }
            for code, nutrient, definition in NUTRIENT_MAP
        ],
    )
    by_code = {
        code: (nutrient, definition)
        for code, nutrient, definition in NUTRIENT_MAP
    }

    ref_food_ids = select(RefFood.id).where(RefFood.source_id == source.id)
    session.execute(
        delete(RefNutrient).where(RefNutrient.ref_food_id.in_(ref_food_ids))
    )
    session.execute(delete(RefPortion).where(RefPortion.ref_food_id.in_(ref_food_ids)))
    session.execute(delete(RefFood).where(RefFood.source_id == source.id))

    foods: list[dict[str, Any]] = []
    portions: list[dict[str, Any]] = []
    nutrients: list[dict[str, Any]] = []
    for dataset_dir in dataset_dirs:
        measure_units = _measure_units(dataset_dir)
        categories = _categories(dataset_dir)
        for row in _rows(dataset_dir / "food.csv"):
            foods.append(
                {
                    "source_id": source.id,
                    "source_food_id": row["fdc_id"],
                    "description": row["description"],
                    "category": categories.get(row["food_category_id"]),
                }
            )
        portions.extend(_portions(dataset_dir, measure_units))
        # Both releases key food_nutrient rows by their own nutrient
        # codes (see NUTRIENT_MAP); map them onto our fixed vocabulary.
        seen: set[tuple[str, str]] = set()
        for row in _rows(dataset_dir / "food_nutrient.csv"):
            code = str(int(row["nutrient_id"] or 0))
            lookup = by_code.get(code)
            source_food_id = row["fdc_id"]
            if (
                lookup is None
                or not row["amount"]
                or (source_food_id, code) in seen
            ):
                continue
            seen.add((source_food_id, code))
            nutrients.append(
                {
                    "source_food_id": source_food_id,
                    "nutrient": lookup[0],
                    "amount_per_100g": float(row["amount"]),
                    "definition": lookup[1],
                }
            )

    session.execute(insert(RefFood), foods)
    ref_ids = dict(
        session.execute(
            select(RefFood.source_food_id, RefFood.id).where(
                RefFood.source_id == source.id
            )
        ).all()
    )
    # The bulk inserts need ref_food_id in place of the source's own
    # id; rebuild the rows rather than mutate the typed dicts in place.
    portion_rows: list[dict[str, Any]] = [
        {"ref_food_id": ref_ids[str(row.pop("fdc_id"))], **row}
        for row in portions
    ]
    nutrient_rows: list[dict[str, Any]] = [
        {"ref_food_id": ref_ids[row.pop("source_food_id")], **row}
        for row in nutrients
    ]

    for model, rows in ((RefPortion, portion_rows), (RefNutrient, nutrient_rows)):
        for start in range(0, len(rows), CHUNK):
            session.execute(insert(model), rows[start : start + CHUNK])
    return {
        "foods": len(foods),
        "portions": len(portions),
        "nutrients": len(nutrients),
    }


def main() -> None:
    logging.basicConfig()
    initialise()
    dataset_dirs = [_extract(_download(url)) for url in (SR_URL, FNDDS_URL)]
    with get_session() as session:
        counts = import_fdc(dataset_dirs, session)
    print(
        f"FDC imported: {counts['foods']} foods, "
        f"{counts['portions']} portions, {counts['nutrients']} nutrients"
    )


if __name__ == "__main__":
    main()
