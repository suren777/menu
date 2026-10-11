"""Grams: converting a stored line's quantity to grams through the
ingredient's conversion rows — a conversion is never guessed."""

from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import IngredientConversion
from menu.ingest.units import COUNT, MASS, VOLUME, singular, unit_ml

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Household-unit spellings the parser emits that FDC portion rows
# spell differently.
_UNIT_ALIASES = {
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
}


def _portion_row(
    conversions: list[IngredientConversion],
    unit: str | None,
    prefer: str | None = None,
) -> IngredientConversion | None:
    """The `portion` row spoken as `unit` ("tbsp", "medium", "stick").

    Egg's cup row is "cup (4.86 large eggs)", so a unit followed by a
    parenthetical counts as the unit too. When `prefer` is given (the
    line's preparation or size), rows whose modifier says so come
    first.
    """
    if unit is None:
        return None
    matches = [
        row
        for row in conversions
        if row.kind == "portion"
        and row.unit is not None
        and (row.unit == unit or row.unit.startswith(f"{unit} ("))
    ]
    if prefer is not None:
        for candidate in matches:
            if candidate.modifier == prefer:
                return candidate
    return matches[0] if matches else None


def _factor_row(
    conversions: list[IngredientConversion], kind: str
) -> IngredientConversion | None:
    """The ingredient's density or unit-weight row, seed rows before
    derived ones."""
    for row in sorted(conversions, key=lambda r: r.origin != "seed"):
        if row.kind == kind:
            return row
    return None


def _portion_density(
    conversions: list[IngredientConversion], prefer: str | None
) -> tuple[float, IngredientConversion] | None:
    """(g/ml, portion row) implied by a volumetric portion: salt's
    "tsp = 6 g" row is 1.22 g/ml, so "a pinch" converts.
    Preparation-matched rows come first."""
    for row in sorted(conversions, key=lambda r: r.modifier != prefer):
        if row.kind != "portion" or row.unit is None:
            continue
        ml = unit_ml(singular(row.unit))
        if ml is None:
            continue
        return row.grams / ml, row
    return None


def to_grams(
    quantity: float | None,
    quantity_max: float | None,
    dimension: str | None,
    base_unit: str | None,
    original_unit: str | None,
    preparation: str | None,
    size: str | None,
    conversions: list[IngredientConversion],
) -> tuple[float | None, float | None, str | None, int | None]:
    """(grams, grams_max, provenance, conversion row id) for one line.

    Precedence over the ingredient's conversion rows, seed rows
    before derived ones — a conversion is never guessed:

    - mass is already grams (no lookup: provenance and row NULL).
    - volume converts through the portion row for the line's unit
      ("1 cup egg" -> its 243 g row), preferring a modifier that
      matches the line's preparation; else the seeded density; else a
      derived one; else the density a volumetric portion row implies
      (salt's "tsp = 6 g"), recorded against that row.
    - count converts through the portion row matching the line's size
      ("large" egg -> the large row) or named count unit ("2 sticks
      butter" -> the stick row); else the seeded unit weight; else a
      derived one; else the "medium" portion row.
    """
    if quantity is None:
        return None, None, None, None
    if dimension == MASS:
        return quantity, quantity_max, None, None
    if dimension == VOLUME:
        name = singular(_UNIT_ALIASES.get(original_unit or "", original_unit or ""))
        portion = _portion_row(conversions, name, preparation)
        if portion is not None:
            factor, conversion_id, origin = (
                portion.grams,
                portion.id,
                portion.origin,
            )
        else:
            density = _factor_row(conversions, "density")
            if density is not None:
                factor, conversion_id, origin = (
                    density.grams,
                    density.id,
                    density.origin,
                )
            else:
                implied = _portion_density(conversions, preparation)
                if implied is None:
                    return None, None, None, None
                factor, row = implied
                conversion_id, origin = row.id, row.origin
        grams_max = quantity_max * factor if quantity_max is not None else None
        return quantity * factor, grams_max, origin, conversion_id
    if dimension == COUNT:
        # Named count units convert through a portion with that name
        # ("stick", "slice"); bare pieces through the size's row.
        if base_unit == "piece":
            portion = _portion_row(conversions, size) if size is not None else None
        else:
            portion = _portion_row(conversions, base_unit)
        if portion is not None:
            weight, conversion_id, origin = (
                portion.grams,
                portion.id,
                portion.origin,
            )
        else:
            unit_weight = _factor_row(conversions, "unit_weight")
            if unit_weight is not None:
                weight, conversion_id, origin = (
                    unit_weight.grams,
                    unit_weight.id,
                    unit_weight.origin,
                )
            elif base_unit == "piece":
                medium = _portion_row(conversions, "medium")
                if medium is None:
                    return None, None, None, None
                weight, conversion_id, origin = (
                    medium.grams,
                    medium.id,
                    medium.origin,
                )
            else:
                return None, None, None, None
        grams_max = quantity_max * weight if quantity_max is not None else None
        return quantity * weight, grams_max, origin, conversion_id
    return None, None, None, None


def ingredient_conversions(
    ingredient_id: int, session: Session
) -> list[IngredientConversion]:
    """All of the ingredient's conversion rows, created by seeding:
    the hand seeds and the confirmed conversion reference's portions
    side by side. Only confirmed references feed the derived rows —
    candidates and rejected ones never do."""
    return list(
        session.scalars(
            select(IngredientConversion).where(
                IngredientConversion.ingredient_id == ingredient_id
            )
        )
    )


def _line_grams(
    quantity: float | None,
    quantity_max: float | None,
    dimension: str | None,
    base_unit: str | None,
    original_unit: str | None,
    preparation: str | None,
    size: str | None,
    ingredient_id: int,
    session: Session,
) -> tuple[float | None, float | None, str | None, int | None]:
    """Grams for a line, from the ingredient's conversion rows — so a
    re-resolve can re-run the conversion without the parser."""
    return to_grams(
        quantity,
        quantity_max,
        dimension,
        base_unit,
        original_unit,
        preparation,
        size,
        ingredient_conversions(ingredient_id, session),
    )
