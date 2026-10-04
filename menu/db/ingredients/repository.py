"""Ingredient queries: plain functions over a caller-supplied session."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import Ingredient, IngredientAlias, RecipeIngredient

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class IngredientModel:
    id: int
    name: str
    fdc_id: int | None
    fdc_id_confirmed: bool
    density_g_per_ml: float | None
    unit_weight_g: float | None


def to_model(record: Ingredient) -> IngredientModel:
    return IngredientModel(
        id=record.id,
        name=record.name,
        fdc_id=record.fdc_id,
        fdc_id_confirmed=record.fdc_id_confirmed,
        density_g_per_ml=record.density_g_per_ml,
        unit_weight_g=record.unit_weight_g,
    )


def find_ingredient_by_name(name: str, session: Session) -> IngredientModel | None:
    record = session.scalars(select(Ingredient).where(Ingredient.name == name)).first()
    return to_model(record) if record is not None else None


def alias_target(
    alias: str, session: Session
) -> tuple[IngredientModel, str | None] | None:
    """The canonical ingredient an alias resolves to, with the variant
    the alias carries ("whole" for whole milk), if any."""
    row = session.execute(
        select(Ingredient, IngredientAlias.variant).join(
            IngredientAlias, IngredientAlias.ingredient_id == Ingredient.id
        ).where(IngredientAlias.alias == alias)
    ).first()
    if row is None:
        return None
    record, variant = row
    return to_model(record), variant


@dataclass
class RecipeIngredientModel:
    id: int
    recipe_id: int
    position: int
    section: str | None
    raw_text: str
    ingredient_id: int | None
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
    variant: str | None
    alternative_of: int | None
    parse_confidence: float | None


def to_recipe_ingredient_model(record: RecipeIngredient) -> RecipeIngredientModel:
    return RecipeIngredientModel(
        id=record.id,
        recipe_id=record.recipe_id,
        position=record.position,
        section=record.section,
        raw_text=record.raw_text,
        ingredient_id=record.ingredient_id,
        variant=record.variant,
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
    )


def get_recipe_ingredients(
    recipe_id: int, session: Session
) -> list[RecipeIngredientModel]:
    """A recipe's ingredient lines, in their original order."""
    return [
        to_recipe_ingredient_model(record)
        for record in session.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.position)
        )
    ]


def get_unresolved(session: Session) -> list[RecipeIngredientModel]:
    """Lines whose ingredient_id is NULL, for review."""
    return [
        to_recipe_ingredient_model(record)
        for record in session.scalars(
            select(RecipeIngredient)
            .where(RecipeIngredient.ingredient_id.is_(None))
            .order_by(RecipeIngredient.id)
        )
    ]
