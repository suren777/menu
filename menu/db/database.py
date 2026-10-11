"""Models for the scratch SQLite database.

Typed 2.0-style columns (Mapped/mapped_column) so mypy sees the column
types and call sites need no casts.
"""

from datetime import datetime  # noqa: TC003 - Mapped annotations resolve at runtime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from menu.db.engine import engine as default_engine

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class Base(DeclarativeBase):
    pass


class Sitemap(Base):
    """One sitemap document of one site, with its fetch outcome.

    A sitemap index stores itself (kind "index", status "done") and
    its children as pending rows; the crawl fetches every sitemap
    still pending or blocked and queues the page URLs it lists. The
    status is the recorded outcome of the last fetch attempt, not a
    boolean: a failed or blocked sitemap is data, not a crash.
    """

    __tablename__ = "sitemaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str] = mapped_column(nullable=False)
    """Registry name of the site this sitemap belongs to."""
    url: Mapped[str] = mapped_column(nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sitemaps.id"))
    """The index sitemap that listed this one, if any."""
    kind: Mapped[str | None]
    """'index' | 'urlset'; NULL until the document has been fetched."""
    status: Mapped[str] = mapped_column(nullable=False, default="pending")
    """'pending' | 'done' | 'failed' | 'blocked'."""
    fetched_at: Mapped[datetime | None]
    error: Mapped[str | None]

    __table_args__ = (
        CheckConstraint(
            "kind IS NULL OR kind IN ('index', 'urlset')",
            "ck_sitemaps_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'done', 'failed', 'blocked')",
            "ck_sitemaps_status",
        ),
    )


class Page(Base):
    """One crawled URL and its outcome: the crawl ledger.

    Every URL a sitemap lists gets a row, whatever happens to it —
    fetched, not a recipe, failed, blocked or disallowed — so a re-run
    continues from the queued pages instead of re-walking sitemaps,
    and "what was crawled and what failed" is a query.
    """

    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str] = mapped_column(nullable=False)
    """Registry name of the site this page belongs to."""
    url: Mapped[str] = mapped_column(nullable=False, unique=True)
    sitemap_id: Mapped[int | None] = mapped_column(ForeignKey("sitemaps.id"))
    lastmod: Mapped[str | None]
    """The sitemap's lastmod for the URL, as the sitemap spells it."""
    status: Mapped[str] = mapped_column(nullable=False, default="queued")
    """'queued' | 'fetched' | 'no_recipe' | 'failed' | 'blocked' |
    'disallowed' — the last fetch attempt's outcome."""
    http_status: Mapped[int | None]
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    fetched_at: Mapped[datetime | None]
    content_sha256: Mapped[str | None]
    """sha256 of the fetched bytes: detects upstream changes."""
    error: Mapped[str | None]

    __table_args__ = (
        Index("ix_pages_site_status", "site", "status"),
        CheckConstraint(
            "status IN ('queued', 'fetched', 'no_recipe', 'failed', "
            "'blocked', 'disallowed')",
            "ck_pages_status",
        ),
    )


class Recipe(Base):
    """The raw recipe data captured from one page.

    Split from the page: the page is the fetch, the recipe is what was
    extracted from it. data_sha256 — the fingerprint of the canonical
    JSON — says whether upstream changed the recipe since the last
    capture; the site is the page's site (join through page_id).
    """

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(nullable=False)
    """The JSON-LD name; the page <title> when the data names nothing."""
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    data_sha256: Mapped[str] = mapped_column(nullable=False)
    extractor: Mapped[str] = mapped_column(nullable=False)
    """'json_ld' | 'test_id' | 'microdata' — what found the recipe."""
    servings: Mapped[int | None]
    """Parsed from recipeYield ("Serves 12", "Makes 16")."""
    yield_text: Mapped[str | None]
    """The raw recipeYield as text."""

    __table_args__ = (
        CheckConstraint(
            "extractor IN ('json_ld', 'test_id', 'microdata')",
            "ck_recipes_extractor",
        ),
    )


class RecipeNutrition(Base):
    """Nutrition totals for one recipe, derived or as published.

    Published rows are parsed from the JSON-LD nutrition block;
    derived rows are summed from ingredient lines and carry coverage
    metadata. A NULL nutrient means not reported, never zero.
    """

    __tablename__ = "recipe_nutrition"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    source: Mapped[str]
    """\"derived\" | \"published\" — never mixed in one row."""
    energy_kcal: Mapped[float | None]
    protein_g: Mapped[float | None]
    fat_g: Mapped[float | None]
    saturated_fat_g: Mapped[float | None]
    carbohydrate_g: Mapped[float | None]
    sugars_g: Mapped[float | None]
    fibre_g: Mapped[float | None]
    sodium_mg: Mapped[float | None]
    coverage: Mapped[float | None]
    """Share of the recipe's grams that came from mapped lines
    (derived rows). Low coverage is marked, not trusted."""
    unconverted_lines: Mapped[int | None]
    """Lines with no quantity or no conversion (derived rows)."""
    sources: Mapped[str | None]
    """Reference sources used, comma-separated (derived rows)."""
    definitions_mixed: Mapped[bool | None]
    """True when the summed values mix nutrient definitions."""

    __table_args__ = (
        Index(
            "ix_recipe_nutrition_recipe_source",
            "recipe_id",
            "source",
            unique=True,
        ),
        CheckConstraint(
            "source IN ('derived', 'published')",
            "ck_recipe_nutrition_source",
        ),
    )


class Ingredient(Base):
    """Canonical ingredient concept, shared by terms and recipe lines."""

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False, unique=True)
    """Canonical, singular, lowercase: "milk"."""
    origin: Mapped[str] = mapped_column(nullable=False, default="auto")
    """'seed' (curated in seed.py) | 'auto' (a parser name the
    heuristics let through). Seeding a name that exists as auto flips
    it to seed; auto concepts are what pruning cleans up."""
    density_g_per_ml: Mapped[float | None]
    """Enables volume<->mass conversion. Hand-seeded; derived from the
    reference data when no seed exists."""
    unit_weight_g: Mapped[float | None]
    """Per-item weight for count->mass conversion (egg ~= 50 g)."""

    __table_args__ = (
        CheckConstraint("origin IN ('seed', 'auto')", "ck_ingredients_origin"),
    )


class IngredientFoodRef(Base):
    """A canonical ingredient mapped to a reference food.

    `status` carries the review state instead of a role-NULL +
    boolean pair. 'confirmed' rows are the hand-seeded mappings
    (seed.FOOD_REFS) and carry the role the reference serves:
    conversion (portion weights) or nutrition (per-100g values) — one
    confirmed reference per role per source per ingredient, which the
    partial unique index makes the database enforce. 'candidate' rows
    are the parser's own suggestion (role NULL): they feed the
    --ref-review report, never conversion or nutrition. 'rejected'
    rows were reviewed and turned down (seed.REJECTED_REFS), so they
    leave the report too and a re-crawl cannot resurrect them.
    `origin` records where the row came from.
    """

    __tablename__ = "ingredient_food_refs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), nullable=False
    )
    ref_food_id: Mapped[int] = mapped_column(nullable=False)
    source_id: Mapped[int] = mapped_column(nullable=False)
    """The food's source, denormalised from ref_food: the composite
    FK below keeps the pair consistent, and the partial unique index
    scopes "one confirmed per role" to a source."""
    role: Mapped[str | None]
    """'conversion' | 'nutrition'; NULL for candidates and rejected
    rows, which are suggestions for either use."""
    status: Mapped[str] = mapped_column(nullable=False, default="candidate")
    """'candidate' | 'confirmed' | 'rejected'."""
    origin: Mapped[str] = mapped_column(nullable=False)
    """'seed' (curated in seed.py) | 'parser' (the fdc_id
    suggestion)."""

    __table_args__ = (
        # The composite FK's parent key: (id, source_id) must be
        # unique for SQLite to accept the reference to it.
        UniqueConstraint("id", "source_id"),
        ForeignKeyConstraint(
            ["ref_food_id", "source_id"],
            ["ref_food.id", "ref_food.source_id"],
        ),
        # The same ingredient -> food mapping must not be stored
        # twice, whatever the statuses; candidates have role NULL, so
        # the coalesce folds that into one key.
        Index(
            "ix_ingredient_food_refs_ingredient_food_role",
            "ingredient_id",
            "ref_food_id",
            text("COALESCE(role, '')"),
            unique=True,
        ),
        # One confirmed reference per role per source per ingredient.
        Index(
            "ix_ingredient_food_refs_confirmed_role_source",
            "ingredient_id",
            "role",
            "source_id",
            unique=True,
            sqlite_where=text("status = 'confirmed'"),
        ),
        Index("ix_ingredient_food_refs_ingredient", "ingredient_id"),
        CheckConstraint(
            "role IS NULL OR role IN ('conversion', 'nutrition')",
            "ck_ingredient_food_refs_role",
        ),
        CheckConstraint(
            "status IN ('candidate', 'confirmed', 'rejected')",
            "ck_ingredient_food_refs_status",
        ),
        CheckConstraint(
            "origin IN ('seed', 'parser')",
            "ck_ingredient_food_refs_origin",
        ),
        CheckConstraint(
            "status <> 'confirmed' OR role IS NOT NULL",
            "ck_ingredient_food_refs_confirmed_has_role",
        ),
    )


class IngredientTerm(Base):
    """One parsed name (a term) and the concept it resolves to.

    Every parsed name is a term; recipe lines point at terms, not
    concepts, so re-pointing a term re-resolves every line that uses
    it with no reparse. `resolution` says how the term was decided:
    'seed' (curated in seed.py), 'auto' (the parser name the
    heuristics let through), 'review' (the review queue — a name the
    parser likely mangled) or 'not_food' (equipment and other
    non-ingredients; their lines stay stored but are excluded from
    aggregation and nutrition). A term resolves exactly when its
    resolution is seed/auto, which the CHECK enforces.
    """

    __tablename__ = "ingredient_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(nullable=False, unique=True)
    """The canonical_name of a parsed name: "warm milk"."""
    ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredients.id")
    )
    """The concept the term resolves to; NULL exactly when the term is
    unresolved (review/not_food)."""
    variant: Mapped[str | None]
    """Which variant the term carries ("whole", "warm"); lets a
    shopping list keep variants apart."""
    resolution: Mapped[str] = mapped_column(nullable=False)
    """'seed' | 'auto' | 'review' | 'not_food'."""

    __table_args__ = (
        Index("ix_ingredient_terms_ingredient_id", "ingredient_id"),
        CheckConstraint(
            "resolution IN ('seed', 'auto', 'review', 'not_food')",
            "ck_ingredient_terms_resolution",
        ),
        CheckConstraint(
            "(resolution IN ('review', 'not_food') AND ingredient_id IS NULL) "
            "OR (resolution NOT IN ('review', 'not_food') "
            "AND ingredient_id IS NOT NULL)",
            "ck_ingredient_terms_resolution_ingredient",
        ),
    )


class RecipeIngredient(Base):
    """One parsed ingredient line of one recipe.

    The line points at its term; the effective ingredient is reached
    through the term (see repository.effective_ingredient_*). The raw
    text is always kept, so an unresolved or failed line can be
    reviewed and matched later.
    """

    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    position: Mapped[int]
    section: Mapped[str | None]
    """BBC publishes a flat ingredient list, so NULL today; populated
    for sites with sectioned ingredient groups."""
    raw_text: Mapped[str] = mapped_column(nullable=False)
    term_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient_terms.id"))
    """The term the line's parsed name resolved to; NULL when the
    parse failed outright."""
    needs_review: Mapped[bool] = mapped_column(nullable=False, default=False)
    """The line-level review flag (line_needs_review): it is about the
    line's raw text, not the term, so it must not taint the term for
    other lines. A seeded term clears it."""
    parse_status: Mapped[str] = mapped_column(nullable=False, default="ok")
    """'ok' | 'failed' — a parse that raised is stored with 'failed'
    and no term instead of being dropped."""
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
        ForeignKey("recipe_ingredients.id", ondelete="CASCADE")
    )
    """Set on the non-counted options of "rosewater or vanilla"."""
    parse_confidence: Mapped[float | None]
    grams: Mapped[float | None]
    """The line on the common gram scale, derived from quantity and
    the reference data. NULL when no honest conversion exists —
    never guessed."""
    grams_max: Mapped[float | None]
    """Upper end of a range ("2-3"), in grams."""
    grams_source: Mapped[str | None]
    """Provenance of the conversion: "seed", the reference source's
    name ("fdc"), NULL for mass lines (no lookup happened) and for
    unconverted lines."""

    __table_args__ = (
        Index("ix_recipe_ingredients_term_id", "term_id"),
        Index("ix_recipe_ingredients_recipe_id_position", "recipe_id", "position"),
        CheckConstraint(
            "dimension IS NULL OR dimension IN ('mass', 'volume', 'count')",
            "ck_recipe_ingredients_dimension",
        ),
        CheckConstraint(
            "parse_status IN ('ok', 'failed')",
            "ck_recipe_ingredients_parse_status",
        ),
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

    __table_args__ = (
        Index("ix_ref_food_source", "source_id", "source_food_id", unique=True),
        # Parent key for ingredient_food_refs' composite FK
        # (ref_food_id, source_id).
        UniqueConstraint("id", "source_id"),
    )


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
