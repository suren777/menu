"""Base-unit conversion tests: pint supplies the factors."""

from fractions import Fraction
from typing import TYPE_CHECKING

import pytest
from ingredient_parser import UREG

from menu.ingest.units import (
    COUNT,
    MASS,
    VOLUME,
    count_to_mass,
    dimension_of,
    singular,
    to_base,
    volume_to_mass,
)

if TYPE_CHECKING:
    from pint import Unit


def _to_base(quantity: Fraction, unit: Unit | str) -> tuple[str, float, str]:
    """to_base with the unparseable case excluded, for readable tests."""
    dimension, base_quantity, base_unit = to_base(quantity, unit)
    assert dimension is not None
    assert base_quantity is not None
    assert base_unit is not None
    return dimension, base_quantity, base_unit


def test_tablespoon_to_ml() -> None:
    dimension, quantity, base = _to_base(Fraction(1), UREG.tablespoon)
    assert dimension == VOLUME
    assert quantity == pytest.approx(14.78676478125)
    assert base == "ml"


def test_imperial_tablespoon_to_ml() -> None:
    """The parser's imperial system must reach the conversion."""
    dimension, quantity, base = _to_base(Fraction(1), UREG.imperial_tablespoon)
    assert dimension == VOLUME
    assert quantity == pytest.approx(17.7581640625)
    assert base == "ml"


def test_lb_to_g() -> None:
    dimension, quantity, base = _to_base(Fraction(1), UREG.pound)
    assert dimension == MASS
    assert quantity == pytest.approx(453.59237)
    assert base == "g"


def test_oz_to_g() -> None:
    dimension, quantity, base = _to_base(Fraction(1), UREG.ounce)
    assert dimension == MASS
    assert quantity == pytest.approx(28.349523125)
    assert base == "g"


def test_us_pint_to_ml() -> None:
    dimension, quantity, base = _to_base(Fraction(1), UREG.pint)
    assert dimension == VOLUME
    assert quantity == pytest.approx(473.176473)
    assert base == "ml"


def test_imperial_pint_to_ml() -> None:
    dimension, quantity, base = _to_base(Fraction(1), UREG.imperial_pint)
    assert dimension == VOLUME
    assert quantity == pytest.approx(568.26125)
    assert base == "ml"


def test_imperial_cup_to_ml() -> None:
    dimension, quantity, base = _to_base(Fraction(1), UREG.imperial_cup)
    assert dimension == VOLUME
    assert quantity == pytest.approx(284.130625)
    assert base == "ml"


def test_bare_count() -> None:
    """A count with no unit ("12 biscuits") becomes pieces."""
    assert _to_base(Fraction(12), "") == (COUNT, 12.0, "piece")


def test_named_count_unit() -> None:
    assert _to_base(Fraction(2), "slices") == (COUNT, 2.0, "slice")


def test_unparseable_quantity() -> None:
    """The parser hands back a str when there is no number to read."""
    assert to_base("to taste", UREG.gram) == (None, None, None)


def test_unknown_dimension() -> None:
    assert dimension_of(UREG.joule) is None
    assert to_base(Fraction(1), UREG.joule) == (None, None, None)


def test_dimension_of() -> None:
    assert dimension_of(UREG.gram) == MASS
    assert dimension_of(UREG.milliliter) == VOLUME
    assert dimension_of("clove") == COUNT


def test_singular() -> None:
    assert singular("slices") == "slice"
    assert singular("clove") == "clove"
    assert singular("glass") == "glass"


def test_volume_to_mass() -> None:
    """Milk's density is 1.03 g/ml."""
    assert volume_to_mass(250.0, 1.03) == pytest.approx(257.5)


def test_count_to_mass() -> None:
    """An egg weighs about 50 g."""
    assert count_to_mass(2, 50.0) == 100.0


def test_heaping_qualifier() -> None:
    """A qualifier loosens the measure but is not a unit: "heaping
    tbsp" converts as a tbsp, not as a junk count unit."""
    dimension, quantity, base = _to_base(Fraction(1), "heaping tbsp")
    assert (dimension, base) == (VOLUME, "ml")
    assert quantity == pytest.approx(14.78676478125)


def test_scant_qualifier() -> None:
    dimension, quantity, base = _to_base(Fraction(2), "scant tbsp")
    assert (dimension, base) == (VOLUME, "ml")
    assert quantity == pytest.approx(2 * 14.78676478125)


def test_fuzzy_units_are_fixed_amounts() -> None:
    """pinch and dash are small volumes, a knob a small mass —
    whatever the ingredient (a pinch is a pinch)."""
    assert _to_base(Fraction(1), "pinch") == (VOLUME, 0.36, "ml")
    assert _to_base(Fraction(1), "dash") == (VOLUME, 0.6, "ml")
    assert _to_base(Fraction(1), "knob") == (MASS, 15.0, "g")
