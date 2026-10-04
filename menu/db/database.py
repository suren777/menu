"""Models for the scratch SQLite database.

Typed 2.0-style columns (Mapped/mapped_column) so mypy sees the column
types and call sites need no casts.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from menu.db.engine import engine as default_engine

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class Base(DeclarativeBase):
    pass


class Sitemap(Base):
    __tablename__ = "sitemap"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(nullable=False, unique=True)
    site: Mapped[str] = mapped_column(nullable=False)
    """Registry name of the site this sitemap belongs to."""
    completed: Mapped[bool] = mapped_column(default=False)


class RecipeUrls(Base):
    __tablename__ = "recipe_urls"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(nullable=False, unique=True)
    """Unique: recipes are deduplicated on their URL."""
    name: Mapped[str] = mapped_column(nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    site: Mapped[str | None]
    """Registry name of the site the recipe came from; stamped at
    ingest time (rows stored before the column existed get it on
    reparse)."""
    servings: Mapped[int | None]
    """Parsed from recipeYield ("Serves 12", "Makes 16")."""


class Ingredient(Base):
    """Canonical ingredient, shared by aliases and recipe lines."""

    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False, unique=True)
    """Canonical, singular, lowercase: "milk"."""
    density_g_per_ml: Mapped[float | None]
    """Enables volume<->mass conversion. Hand-seeded; derived from the
    reference data when no seed exists."""
    unit_weight_g: Mapped[float | None]
    """Per-item weight for count->mass conversion (egg ~= 50 g)."""


class IngredientFoodRef(Base):
    """A canonical ingredient mapped to a reference food.

    Hand-seeded mappings (seed.FOOD_REFS) are confirmed, with the role
    the reference serves: conversion (portion weights) or nutrition
    (per-100g values). One ingredient can hold one confirmed reference
    per role. The parser's own suggestion seeds an unconfirmed
    candidate row (role NULL) — candidates feed the --ref-review
    report, never conversion or nutrition.
    """

    __tablename__ = "ingredient_food_ref"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredient.id"), nullable=False
    )
    ref_food_id: Mapped[int] = mapped_column(
        ForeignKey("ref_food.id"), nullable=False
    )
    role: Mapped[str | None]
    """'conversion' | 'nutrition'; NULL for unconfirmed parser
    candidates, which are suggestions for either use."""
    confirmed: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (Index("ix_ingredient_food_ref_ingredient", "ingredient_id"),)


class IngredientAlias(Base):
    """Maps a line's parsed name to its canonical ingredient.

    Variants ("whole", "unsalted") are kept so a shopping list can
    roll them up to the parent by default, or keep them apart.
    """

    __tablename__ = "ingredient_alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(nullable=False, unique=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredient.id"), nullable=False
    )
    variant: Mapped[str | None]


class RecipeIngredient(Base):
    """One parsed ingredient line of one recipe.

    ingredient_id NULL means unresolved: the raw text is always kept,
    so the row can be reviewed and matched later.
    """

    __tablename__ = "recipe_ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe_urls.id"))
    position: Mapped[int]
    section: Mapped[str | None]
    """BBC publishes a flat ingredient list, so NULL today; populated
    for sites with sectioned ingredient groups."""
    raw_text: Mapped[str] = mapped_column(nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    variant: Mapped[str | None]
    """Which alias variant the line resolved through ("whole",
    "unsalted"); lets a shopping list keep variants apart. Set only
    when an alias row carried a variant."""
    quantity: Mapped[float | None]
    """Base units (g / ml / piece)."""
    quantity_max: Mapped[float | None]
    """Upper end of a range ("2-3"), in base units."""
    dimension: Mapped[str | None]
    """'mass' | 'volume' | 'count' | NULL. Derivable from base_unit but
    stored so aggregation is a plain indexed column."""
    base_unit: Mapped[str | None]
    original_quantity_text: Mapped[str | None]
    original_unit: Mapped[str | None]
    preparation: Mapped[str | None]
    size: Mapped[str | None]
    note: Mapped[str | None]
    optional: Mapped[bool] = mapped_column(default=False)
    alternative_of: Mapped[int | None] = mapped_column(
        ForeignKey("recipe_ingredient.id")
    )
    """Set on the non-counted options of "rosewater or vanilla"."""
    parse_confidence: Mapped[float | None]

    __table_args__ = (
        Index("ix_recipe_ingredient_ingredient_id", "ingredient_id"),
        Index("ix_recipe_ingredient_recipe_id_position", "recipe_id", "position"),
    )


class FoodSource(Base):
    """A reference data source (FDC, CoFID, CNF, ...) with the licence
    provenance food-guru must carry. Import a source only when coverage
    needs it, and only when its licence allows commercial use without
    share-alike."""

    __tablename__ = "food_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False, unique=True)
    version: Mapped[str] = mapped_column(nullable=False)
    licence: Mapped[str] = mapped_column(nullable=False)
    citation: Mapped[str] = mapped_column(nullable=False)
    """Attribution is data: print this wherever source values show."""


class RefFood(Base):
    """One food of a reference source, keyed by the source's own id.
    `source_food_id` is the id as the source spells it (an FDC number,
    a CoFID code), so the rest of the code never cares which."""

    __tablename__ = "ref_food"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("food_source.id"), nullable=False
    )
    source_food_id: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(nullable=False)
    category: Mapped[str | None]
    refuse_pct: Mapped[float | None]
    """Share that is peel/stone/shell, where the source gives one."""

    __table_args__ = (Index("ix_ref_food_source", "source_id", "source_food_id"),)


class RefPortion(Base):
    """Gram weight of one household portion of a reference food.

    `unit` is the lookup text: the FDC SR `modifier` ("large", "cup
    (4.86 large eggs)") or the FNDDS portion description's unit ("1
    cup" -> "cup"). gram_weight is per `amount` of that unit.
    """

    __tablename__ = "ref_portion"

    ref_food_id: Mapped[int] = mapped_column(
        ForeignKey("ref_food.id"), primary_key=True
    )
    seq_num: Mapped[int] = mapped_column(primary_key=True)
    amount: Mapped[float | None]
    unit: Mapped[str | None]
    modifier: Mapped[str | None]
    gram_weight: Mapped[float] = mapped_column(nullable=False)


class RefNutrient(Base):
    """One nutrient of a reference food, per 100 g.

    `nutrient` is our fixed key (fdc's map), not the source's code, so
    the releases' differing vocabularies collapse onto one. definition
    records which definition the value uses (carbohydrate by difference
    vs available, fibre method) — never mix across definitions silently.
    """

    __tablename__ = "ref_nutrient"

    ref_food_id: Mapped[int] = mapped_column(
        ForeignKey("ref_food.id"), primary_key=True
    )
    nutrient: Mapped[str] = mapped_column(primary_key=True)
    amount_per_100g: Mapped[float] = mapped_column(nullable=False)
    definition: Mapped[str] = mapped_column(nullable=False)


class NutrientMap(Base):
    """Maps a source's nutrient code onto the fixed vocabulary, with
    the definition the code means. Per source, explicit — never
    compared or mixed across definitions silently."""

    __tablename__ = "nutrient_map"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("food_source.id"), primary_key=True
    )
    source_nutrient_code: Mapped[str] = mapped_column(primary_key=True)
    nutrient: Mapped[str] = mapped_column(nullable=False)
    definition: Mapped[str] = mapped_column(nullable=False)


def initialise(db_engine: Engine | None = None) -> None:
    """Create the tables. The default engine is resolved at call time so
    tests can patch it."""
    Base.metadata.create_all(db_engine or default_engine)
