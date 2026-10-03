"""Ingredient-line parsing: one free-text line -> a ParsedLine.

Wraps ingredient-parser-nlp behind our own frozen dataclass so nothing
else depends on the library: swap or replace the parser later without
touching call sites. site.unit_system decides how ambiguous units
(cup, pint) are read — BBC is imperial — and foundation_foods links
names to USDA FoodData Central ids for a canonical identity.

The parser returns ranked name alternatives ("rosewater or vanilla
extract" -> rosewater extract, then vanilla extract). The first option
is the one a shopping list counts; the rest are kept as alternatives.
"""

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

import inflect
from ingredient_parser import parse_ingredient
from ingredient_parser.dataclasses import (
    CompositeIngredientAmount,
    IngredientAmount,
    ParsedIngredient,
)

from menu.ingest.units import to_base

if TYPE_CHECKING:
    from menu.ingest.registry import SiteConfig

# The parser has no "metric" volumetric system. Metric sites rarely use
# cups, and where they do, the US convention is the common rendering.
_VOLUMETRIC_SYSTEMS = {
    "us": "us_customary",
    "imperial": "imperial",
    "metric": "us_customary",
}


@dataclass(frozen=True)
class ParsedLine:
    """One ingredient line, parsed and normalised to base units."""

    raw_text: str
    name: str | None
    """Canonical, singular, lowercase: "milk". NULL when unresolvable."""
    alternatives: tuple[str, ...]
    """Other names the parser ranked below the first: "vanilla extract"."""
    size: str | None
    """Does not change identity: "medium", "large"."""
    preparation: str | None
    """Kept separate from the name: "softened", "beaten"."""
    note: str | None
    """Comment/purpose: "plus extra for the tin", "to serve"."""
    optional: bool
    quantity: float | None
    """Base units (g / ml / piece)."""
    quantity_max: float | None
    """Upper end of a range ("2-3"), in base units."""
    dimension: str | None
    """'mass' | 'volume' | 'count' | NULL."""
    base_unit: str | None
    original_quantity_text: str | None
    original_unit: str | None
    parse_confidence: float
    fdc_id: int | None
    """USDA FoodData Central id when the parser matched one."""


_INFLECT = inflect.engine()

# inflect gets these wrong ("molass", "cassi"); they are already
# singular or uncountable, so leave them exactly as parsed.
_LEAVE_ALONE = frozenset({"molasses", "cassis"})


def canonical_name(text: str) -> str:
    """Lowercase and properly singularise a parsed name: "cherry
    tomatoes" -> "cherry tomato".

    Real canonicalisation is the alias table's job (warm milk ->
    milk); this only fixes case and the plural so the table always
    has one form to map. Words ending in "us" or "ss" are never
    touched: they are already singular or uncountable (asparagus,
    couscous, hummus, molasses) and inflect strips them to junk
    ("asparagu", "molass").
    """
    name = " ".join(text.lower().split())
    if not name:
        return name
    *lead, last = name.split()
    if last.endswith(("us", "ss")) or last in _LEAVE_ALONE:
        return name
    singular = _INFLECT.singular_noun(last)
    return " ".join([*lead, singular if isinstance(singular, str) else last])


def name_needs_review(name: str) -> bool:
    """Heuristic for names the parser likely mangled, which should go
    to the review queue (ingredient_id NULL) instead of becoming
    canonical ingredients: long modifier chains ("whole blanched
    almonds roughly chopped") and unsplit conjunctions ("candied
    orange and lemon peel")."""
    words = name.split()
    return len(words) >= 5 or " or " in name or " and " in name


_BRACKETS = re.compile(r"\([^)]*\)")
_AND = re.compile(r"\band\b")
_OR = re.compile(r"\bor\b")


def line_needs_review(
    raw_text: str, parsed: ParsedLine, or_alternative: ParsedLine | None
) -> bool:
    """Full-line review heuristic: the name check above, plus
    conjunctions the parse dropped. A conjunction is accounted for by
    the name, the note/preparation text, a bracketed parenthetical
    ("(or 3 small)") or a stored alternative; anything else was lost.
    "and" is flagged even when the parser captured it as an
    alternative: "pink and yellow food colouring gels" needs both
    gels, so alternative semantics would drop one from the shopping
    list. "or" is only flagged when nothing captured the second
    option."""
    if parsed.name is None:
        return False
    if name_needs_review(parsed.name):
        return True
    # "(or 3 small)" is a parenthetical, not line content.
    text = _BRACKETS.sub(" ", raw_text.lower())
    sidecars = " ".join(
        part.lower() for part in (parsed.note, parsed.preparation) if part
    )
    if _AND.search(text) and not _AND.search(sidecars):
        return True
    if _OR.search(text) and not _OR.search(sidecars):
        return not (parsed.alternatives or or_alternative is not None)
    return False


def _primary_amount(parsed: ParsedIngredient) -> IngredientAmount | None:
    """The first amount, counting only the first option of an
    alternative ("2 tsp vanilla or 1 tsp essential oil")."""
    for amount in parsed.amount:
        if isinstance(amount, CompositeIngredientAmount):
            if amount.amounts:
                return amount.amounts[0]
            continue
        return amount
    return None


def parse_line(text: str, site: SiteConfig) -> ParsedLine:
    """Parse one ingredient line under the site's unit system."""
    parsed = parse_ingredient(
        text,
        volumetric_units_system=_VOLUMETRIC_SYSTEMS[site.unit_system],
        foundation_foods=True,
    )

    names = parsed.name
    amount = _primary_amount(parsed)

    dimension: str | None = None
    quantity: float | None = None
    quantity_max: float | None = None
    base_unit: str | None = None
    if amount is not None:
        dimension, quantity, base_unit = to_base(amount.quantity, amount.unit)
        if amount.RANGE and isinstance(amount.quantity_max, Fraction):
            _, qmax, _ = to_base(amount.quantity_max, amount.unit)
            if qmax != quantity:
                quantity_max = qmax

    note = "; ".join(
        part.text for part in (parsed.comment, parsed.purpose) if part is not None
    ) or None
    confidence = (
        amount.confidence
        if amount is not None
        else names[0].confidence
        if names
        else 0.0
    )

    return ParsedLine(
        raw_text=text,
        name=canonical_name(names[0].text) if names else None,
        alternatives=tuple(canonical_name(n.text) for n in names[1:]),
        size=parsed.size.text if parsed.size else None,
        preparation=parsed.preparation.text if parsed.preparation else None,
        note=note,
        optional="optional" in text.lower(),
        quantity=quantity,
        quantity_max=quantity_max,
        dimension=dimension,
        base_unit=base_unit,
        original_quantity_text=amount.text if amount is not None else None,
        original_unit=(str(amount.unit) or None) if amount is not None else None,
        parse_confidence=confidence,
        fdc_id=(
            parsed.foundation_foods[0].fdc_id if parsed.foundation_foods else None
        ),
    )
