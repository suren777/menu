"""Hand-seeded ingredient data: aliases, densities, unit weights.

Two things cannot come from the parser and are curated here by hand:

- Variant canonicalisation ("warm milk" -> "milk") is alias-driven;
  the parser keeps modifiers fused into the name.
- USDA FoodData Central has no directly usable density, so
  volume->mass aggregation needs these measured values.

Rerunning is idempotent: missing rows are created, and an alias that
already exists but points elsewhere (typically a parse-time self-alias
of a variant name) is re-pointed to the canonical ingredient.
"""

from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import Ingredient, IngredientAlias

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# alias -> (canonical ingredient, variant)
ALIASES: list[tuple[str, str, str | None]] = [
    ("warm milk", "milk", "warm"),
    ("whole milk", "milk", "whole"),
    ("salted butter", "butter", "salted"),
    ("unsalted butter", "butter", "unsalted"),
    ("melted butter", "butter", "melted"),
    ("plain flour", "flour", "plain"),
    ("bread flour", "flour", "bread"),
    ("strong white bread flour", "flour", "bread"),
    ("egg white", "egg", "white"),
    ("egg yolk", "egg", "yolk"),
    ("instant dried yeast", "fast-action dried yeast", None),
    # The parser turns "rosewater or vanilla extract" into
    # "rosewater extract"; map it back to rosewater.
    ("rosewater extract", "rosewater", None),
    (
        "whole blanched almonds roughly chopped",
        "blanched almond",
        "whole, roughly chopped",
    ),
    # King Arthur recipes carry the brand in the ingredient name;
    # the 5-word names would otherwise all land in review. Bread
    # flour folds into the existing "flour" canonical rather than
    # fragmenting a second one.
    (
        "king arthur unbleached all-purpose flour",
        "all-purpose flour",
        None,
    ),
    ("king arthur unbleached bread flour", "flour", "bread"),
    ("king arthur semolina flour", "semolina flour", None),
    ("king arthur baker's special dry milk", "nonfat dry milk", None),
    ("king arthur artisan bread topping", "bread topping", "artisan"),
    ("king arthur the works bread topping", "bread topping", "the works"),
]

# Whitespace-normalised raw line -> canonical ingredient, for lines the
# parser merges wrongly. The real cached line "70g  milk or dark
# chocolate roughly chopped (optional)" (double space) parses as name
# "milk" with confidence 1.0 — no confidence threshold catches it, only
# curated data does. Lookup squashes the line's whitespace, so keys
# hold the single-spaced form. Checked before the parser's name is
# trusted.
LINE_OVERRIDES: dict[str, str] = {
    "70g milk or dark chocolate roughly chopped (optional)": "milk chocolate",
}

# g/ml, cooking-standard measured values
DENSITIES_G_PER_ML: dict[str, float] = {
    "milk": 1.03,
    "butter": 0.96,
    "flour": 0.55,
    "caster sugar": 0.85,
}

# g per piece
UNIT_WEIGHTS_G: dict[str, float] = {
    "egg": 50.0,
}


def _canonical_ingredient(name: str, session: Session) -> Ingredient:
    ingredient = session.scalar(select(Ingredient).where(Ingredient.name == name))
    if ingredient is None:
        ingredient = Ingredient(name=name)
        session.add(ingredient)
        session.flush()
    return ingredient


def seed_ingredient_data(session: Session) -> None:
    """Create the seed rows; safe to rerun."""
    for alias, canonical, variant in ALIASES:
        target = _canonical_ingredient(canonical, session)
        existing = session.scalar(
            select(IngredientAlias).where(IngredientAlias.alias == alias)
        )
        if existing is None:
            session.add(
                IngredientAlias(
                    alias=alias, ingredient_id=target.id, variant=variant
                )
            )
        elif existing.ingredient_id != target.id:
            existing.ingredient_id = target.id
            existing.variant = variant

    # Density and unit-weight ingredients are created even when
    # nothing points at them yet: caster sugar is no alias target, so
    # a fresh crawl would otherwise meet it before the next startup
    # and miss its density for a run.
    for name, density in DENSITIES_G_PER_ML.items():
        ingredient = _canonical_ingredient(name, session)
        ingredient.density_g_per_ml = density

    for name, weight in UNIT_WEIGHTS_G.items():
        ingredient = _canonical_ingredient(name, session)
        ingredient.unit_weight_g = weight
