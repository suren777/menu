"""CNF reference data: one-off bulk import into the common ref tables.

Downloads Health Canada's Canadian Nutrient File 2015 CSV release once
into .cache/sources/cnf/ and loads the foods, household-measure
portions and the fixed nutrient slice into ref_food / ref_portion /
ref_nutrient, tagged with a food_source row and a nutrient_map. Bulk
files, not a crawl, so no politeness rules apply — and nothing here
runs at ingest time: run `uv run menu-cnf-import` to (re)build the
tables.
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
from menu.ingest.units import singular

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CACHE_DIR = Path(".cache/sources/cnf")
SOURCE_NAME = "cnf"
SOURCE_VERSION = "CNF 2015"

CNF_URL = (
    "https://www.canada.ca/content/dam/hc-sc/migration/hc-sc/fn-an/"
    "alt_formats/zip/nutrition/fiche-nutri-data/cnf-fcen-csv.zip"
)

# The fixed nutrient vocabulary, by CNF's nutrient id (the old SR
# numbers), with the definition each code means.
NUTRIENT_MAP: list[tuple[str, str, str]] = [
    # (source code, our nutrient, definition)
    ("208", "energy_kcal", "kcal"),
    ("203", "protein_g", "by_weight"),
    ("204", "fat_g", "by_weight"),
    ("606", "saturated_fat_g", "by_weight"),
    ("205", "carbohydrate_g", "by_difference"),
    ("269", "sugars_g", "total"),
    ("291", "fibre_g", "total_dietary"),
    ("307", "sodium_mg", "by_weight"),
]

CHUNK = 10_000

# Measure descriptions lead with an amount — "100ml", "1/2 back",
# "1 fish (500 g)" — glued to the unit or not, then a unit word and
# free-text modifier.
_AMOUNT = re.compile(r"^\s*(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?\s*")

# The household-unit aliases conversion applies to ingredient lines
# (menu.db.ingredients.actions); portions must spell units the same
# way to be found.
_UNIT_ALIASES = {
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
}


def _parse_measure(description: str) -> tuple[float, str, str | None] | None:
    """(amount, unit, modifier) from a measure description like
    "100ml chopped" or "1/2 back"; None when no amount leads it.

    The unit is the first word after the amount, aliased and
    singularised so conversion matches it ("tablespoons" -> "tbsp",
    "slices" -> "slice"); the modifier is the remaining text, or None.
    """
    match = _AMOUNT.match(description)
    if match is None:
        return None
    numerator, denominator = match.groups()
    amount = float(numerator)
    if denominator is not None:
        amount = round(amount / float(denominator), 4)
    rest = description[match.end() :]
    if not rest:
        return None  # a bare number leaves conversion no unit to match
    word, _, modifier = rest.partition(" ")
    unit = _UNIT_ALIASES.get(word.lower(), singular(word.lower()))
    return amount, unit, modifier or None


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="latin-1") as handle:
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


def _categories(dataset_dir: Path) -> dict[str, str]:
    """Food group id -> English group name."""
    return {
        row["FoodGroupID"]: row["FoodGroupName"]
        for row in _rows(dataset_dir / "FOOD GROUP.csv")
    }


def _measure_names(dataset_dir: Path) -> dict[int, str]:
    return {
        int(row["MeasureID"]): row["MeasureDescription"]
        for row in _rows(dataset_dir / "MEASURE NAME.csv")
    }


def _refuse(dataset_dir: Path) -> dict[str, float]:
    """Food id -> refuse percentage (already a share, not a weight);
    the first row per food wins."""
    refuse: dict[str, float] = {}
    for row in _rows(dataset_dir / "REFUSE AMOUNT.csv"):
        refuse.setdefault(row["FoodID"], float(row["RefuseAmount"] or 0))
    return refuse


def _portions(
    dataset_dir: Path, measures: dict[int, str]
) -> Iterator[dict[str, Any]]:
    """Portion rows from the conversion factors. A factor multiplies
    the per-100 g values into per-measure values, so the measure's
    gram weight is factor x 100. Measures without a leading amount
    ("dough for 1", brand names) are skipped."""
    seq: dict[str, int] = {}
    for row in _rows(dataset_dir / "CONVERSION FACTOR.csv"):
        factor = float(row["ConversionFactorValue"] or 0)
        parsed = _parse_measure(measures.get(int(row["MeasureID"]), ""))
        if not factor or parsed is None:
            continue
        amount, unit, modifier = parsed
        food_id = row["FoodID"]
        seq[food_id] = seq.get(food_id, 0) + 1
        yield {
            "food_id": food_id,
            "seq_num": seq[food_id],
            "amount": amount,
            "unit": unit,
            "modifier": modifier,
            "gram_weight": factor * 100,
        }


def _food_source(session: Session) -> FoodSource:
    source = session.scalar(select(FoodSource).where(FoodSource.name == SOURCE_NAME))
    if source is None:
        source = FoodSource(
            name=SOURCE_NAME,
            version=SOURCE_VERSION,
            licence="Health Canada: free use with attribution; "
            "values must not be modified",
            citation="Canadian Nutrient File (CNF), 2015. Health Canada. "
            "Values are reproduced unmodified.",
        )
        session.add(source)
        session.flush()
    return source


def import_cnf(dataset_dir: Path, session: Session) -> dict[str, int]:
    """Replace the CNF rows in the common ref tables with the slices
    of the extracted release directory. Returns the row counts."""
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

    categories = _categories(dataset_dir)
    refuse = _refuse(dataset_dir)
    measures = _measure_names(dataset_dir)

    foods: list[dict[str, Any]] = []
    for row in _rows(dataset_dir / "FOOD NAME.csv"):
        foods.append(
            {
                "source_id": source.id,
                "source_food_id": row["FoodID"],
                "description": row["FoodDescription"],
                "category": categories.get(row["FoodGroupID"]),
                "refuse_pct": refuse.get(row["FoodID"]),
            }
        )
    portions = list(_portions(dataset_dir, measures))
    nutrients: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in _rows(dataset_dir / "NUTRIENT AMOUNT.csv"):
        code = row["NutrientID"]
        lookup = by_code.get(code)
        if lookup is None or not row["NutrientValue"] or (row["FoodID"], code) in seen:
            continue
        seen.add((row["FoodID"], code))
        nutrients.append(
            {
                "food_id": row["FoodID"],
                "nutrient": lookup[0],
                "amount_per_100g": float(row["NutrientValue"]),
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
        {"ref_food_id": ref_ids[row.pop("food_id")], **row} for row in portions
    ]
    nutrient_rows: list[dict[str, Any]] = [
        {"ref_food_id": ref_ids[row.pop("food_id")], **row} for row in nutrients
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
    dataset_dir = _extract(_download(CNF_URL))
    with get_session() as session:
        counts = import_cnf(dataset_dir, session)
    print(
        f"CNF imported: {counts['foods']} foods, "
        f"{counts['portions']} portions, {counts['nutrients']} nutrients"
    )


if __name__ == "__main__":
    main()
