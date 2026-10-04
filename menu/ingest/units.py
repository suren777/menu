"""Quantity normalisation into one canonical base-unit system.

Base units: mass -> gram, volume -> millilitre, count -> piece (plus
named count units such as slice and clove, which stay their own unit).

pint supplies every conversion factor — never write factors by hand —
and the registry is the ingredient parser's, so units parsed out of
ingredient lines convert natively. Volume<->mass conversion needs a
per-ingredient density and count->mass a per-ingredient unit weight;
without them, lines are kept separate and shown side by side.

Quantities are stored in base units. Converting to metric or imperial
for display is a caller concern and never happens here.
"""

from contextlib import suppress
from typing import TYPE_CHECKING

from ingredient_parser import UREG
from pint.errors import UndefinedUnitError

if TYPE_CHECKING:
    from fractions import Fraction

    from pint import Unit

MASS = "mass"
VOLUME = "volume"
COUNT = "count"

# Qualifier words that loosen a household measure ("heaping tbsp"):
# modifiers on the real unit, not units themselves. The quantity is
# kept as-is — a heaping spoonful is more, but how much more is the
# recipe's guess, not a factor to invent.
_QUALIFIERS = frozenset({"heaping", "scant"})

# Fuzzy count units that are really fixed amounts, whatever the
# ingredient (a pinch is a pinch). Checked before the pint lookup:
# pint parses "pinch" as picoinch.
_FIXED_AMOUNTS: dict[str, tuple[str, float, str]] = {
    "pinch": (VOLUME, 0.36, "ml"),  # ~1/16 tsp
    "dash": (VOLUME, 0.6, "ml"),  # ~1/8 tsp
    "knob": (MASS, 15.0, "g"),  # knob of butter ~= 15 g
}


def dimension_of(unit: Unit | str) -> str | None:
    """Classify a parsed unit as mass, volume or count.

    Named count units ("slices", "clove", "stick") and the empty unit
    of a bare count ("2 eggs") are both 'count'. A pint unit with some
    other dimensionality (rare) has no base unit here, so None.
    """
    if isinstance(unit, str):
        return COUNT
    if unit.dimensionality == UREG.gram.dimensionality:
        return MASS
    if unit.dimensionality == UREG.milliliter.dimensionality:
        return VOLUME
    return None


def singular(unit_name: str) -> str:
    """Naive singular of a count-unit name: "slices" -> "slice".

    Real singulars live in the alias table; this only normalises the
    common plural so storage has one form per unit.
    """
    if unit_name.endswith("s") and not unit_name.endswith("ss"):
        return unit_name[:-1]
    return unit_name


def to_base(
    quantity: Fraction | str, unit: Unit | str
) -> tuple[str | None, float | None, str | None]:
    """Convert one parsed amount to (dimension, base quantity, base unit).

    A quantity the parser could not make sense of (it returns a str,
    e.g. "to taste") converts to (None, None, None).
    """
    if isinstance(quantity, str):
        return None, None, None
    if isinstance(unit, str):
        # "heaping tbsp" is a tbsp with a qualifier, not a unit.
        qualifier, _, rest = unit.partition(" ")
        if rest and qualifier in _QUALIFIERS:
            unit = rest
        fixed = _FIXED_AMOUNTS.get(unit)
        if fixed is not None:
            return fixed
        # A string unit is usually a named count unit ("clove"), but
        # after a qualifier is stripped it can be a pint unit the
        # parser passed through as text ("tbsp").
        if unit:
            with suppress(UndefinedUnitError):
                unit = UREG.parse_units(unit)
    dimension = dimension_of(unit)
    if dimension is None:
        return None, None, None
    if dimension == COUNT:
        base = "piece" if unit == "" else singular(str(unit))
        return COUNT, float(quantity), base
    amount = UREG.Quantity(float(quantity), unit)
    if dimension == MASS:
        return MASS, float(amount.to(UREG.gram).magnitude), "g"
    return VOLUME, float(amount.to(UREG.milliliter).magnitude), "ml"


def volume_to_mass(volume_ml: float, density_g_per_ml: float) -> float:
    """Millilitres -> grams via the ingredient's density.

    The density is ingredient data, not a conversion factor: without
    it this conversion must not be attempted.
    """
    return volume_ml * density_g_per_ml


def count_to_mass(count: float, unit_weight_g: float) -> float:
    """Count -> grams via the ingredient's per-item weight (egg ~= 50 g).

    The unit weight is ingredient data, not a conversion factor.
    """
    return count * unit_weight_g
