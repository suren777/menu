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
    fdc_id: Mapped[int | None]
    """USDA FoodData Central id when the parser matched one."""
    density_g_per_ml: Mapped[float | None]
    """Enables volume<->mass conversion. Hand-seeded: USDA FoodData
    Central has no directly usable density."""
    unit_weight_g: Mapped[float | None]
    """Per-item weight for count->mass conversion (egg ~= 50 g)."""


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


def initialise(db_engine: Engine | None = None) -> None:
    """Create the tables. The default engine is resolved at call time so
    tests can patch it."""
    Base.metadata.create_all(db_engine or default_engine)
