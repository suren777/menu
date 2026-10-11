"""Ingredient queries: plain functions over a caller-supplied session."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Select, or_, select

from menu.db.database import Ingredient, IngredientTerm, RecipeIngredient

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.expression import ColumnElement

RESOLVED_RESOLUTIONS = ("seed", "auto")
"""Term resolutions that point at a concept. review/not_food do not."""


@dataclass
class IngredientModel:
    id: int
    name: str
    density_g_per_ml: float | None
    unit_weight_g: float | None


def to_model(record: Ingredient) -> IngredientModel:
    return IngredientModel(
        id=record.id,
        name=record.name,
        density_g_per_ml=record.density_g_per_ml,
        unit_weight_g=record.unit_weight_g,
    )


def find_ingredient_by_name(name: str, session: Session) -> IngredientModel | None:
    record = session.scalars(select(Ingredient).where(Ingredient.name == name)).first()
    return to_model(record) if record is not None else None


def lines_with_terms_select(
    *criteria: ColumnElement[bool],
) -> Select[RecipeIngredient, IngredientTerm | None]:
    """`recipe_ingredients` outer-joined to `ingredient_terms`: the one
    join every effective-ingredient reader goes through (aggregation,
    derivation, reports, metrics, review lists)."""
    stmt: Select[RecipeIngredient, IngredientTerm | None] = select(
        RecipeIngredient, IngredientTerm
    ).outerjoin(IngredientTerm, IngredientTerm.id == RecipeIngredient.term_id)
    if criteria:
        return stmt.where(*criteria)
    return stmt


def effective_ingredient_id(
    needs_review: bool, term: IngredientTerm | None
) -> int | None:
    """A line's effective ingredient: its term's concept, or NULL when
    the line is flagged for review, the term is unresolved
    (review/not_food) or the line has no term. ONE definition of the
    rule — row readers go through this, SQL readers through
    effective_ingredient_predicates()."""
    if term is None or term.resolution not in RESOLVED_RESOLUTIONS:
        return None
    # A seeded term wins over the line heuristic: curated data has
    # decided where the line belongs, whenever the seed was added.
    if needs_review and term.resolution != "seed":
        return None
    return term.ingredient_id


def effective_ingredient_predicates() -> tuple[ColumnElement[bool], ...]:
    """The same rule as SQL, for readers that aggregate in a query."""
    return (
        or_(
            RecipeIngredient.needs_review.is_(False),
            IngredientTerm.resolution == "seed",
        ),
        IngredientTerm.resolution.in_(RESOLVED_RESOLUTIONS),
    )


@dataclass
class RecipeIngredientModel:
    id: int
    recipe_id: int
    position: int
    section: str | None
    raw_text: str
    term_id: int | None
    ingredient_id: int | None
    """The EFFECTIVE ingredient (effective_ingredient_id), not a
    column: reached through the term, NULL when unresolved."""
    variant: str | None
    """The term's variant; reached through the term."""
    term_resolution: str | None
    needs_review: bool
    parse_status: str
    quantity: float | None
    quantity_max: float | None
    dimension: str | None
    base_unit: str | None
    original_quantity_text: str | None
    original_unit: str | None
    preparation: str | None
    size: str | None
    note: str | None
    optional: bool
    alternative_of: int | None
    parse_confidence: float | None
    grams: float | None
    grams_max: float | None
    grams_source: str | None


def to_recipe_ingredient_model(
    record: RecipeIngredient, term: IngredientTerm | None
) -> RecipeIngredientModel:
    return RecipeIngredientModel(
        id=record.id,
        recipe_id=record.recipe_id,
        position=record.position,
        section=record.section,
        raw_text=record.raw_text,
        term_id=record.term_id,
        ingredient_id=effective_ingredient_id(record.needs_review, term),
        variant=term.variant if term is not None else None,
        term_resolution=term.resolution if term is not None else None,
        needs_review=record.needs_review,
        parse_status=record.parse_status,
        quantity=record.quantity,
        quantity_max=record.quantity_max,
        dimension=record.dimension,
        base_unit=record.base_unit,
        original_quantity_text=record.original_quantity_text,
        original_unit=record.original_unit,
        preparation=record.preparation,
        size=record.size,
        note=record.note,
        optional=record.optional,
        alternative_of=record.alternative_of,
        parse_confidence=record.parse_confidence,
        grams=record.grams,
        grams_max=record.grams_max,
        grams_source=record.grams_source,
    )


def get_recipe_ingredients(
    recipe_id: int, session: Session
) -> list[RecipeIngredientModel]:
    """A recipe's ingredient lines, in their original order."""
    return [
        to_recipe_ingredient_model(line, term)
        for line, term in session.execute(
            lines_with_terms_select(RecipeIngredient.recipe_id == recipe_id).order_by(
                RecipeIngredient.position
            )
        ).all()
    ]


def get_unresolved(session: Session) -> list[RecipeIngredientModel]:
    """Lines with no effective ingredient — no term, a review/not_food
    term, or a line-level review flag — for review."""
    return [
        model
        for line, term in session.execute(
            lines_with_terms_select().order_by(RecipeIngredient.id)
        ).all()
        if (model := to_recipe_ingredient_model(line, term)).ingredient_id is None
    ]
