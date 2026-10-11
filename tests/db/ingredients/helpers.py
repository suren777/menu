"""Shared helpers for the ingredient-module tests: bare recipes to
attach lines to, and the store_lines entry the pipeline supplies a
session to."""

from typing import TYPE_CHECKING, Any

from menu.db.connection import get_session
from menu.db.database import Ingredient, IngredientTerm, Page, Recipe
from menu.db.ingredients.lines import store_recipe_ingredients
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def add_recipe(url: str) -> int:
    """Insert a bare recipe row, reached through its page (the
    ledger); the action opens its own session, so the seed must be
    committed before it runs."""
    with get_session() as session:
        page = Page(url=url, site="bbc_good_food")
        session.add(page)
        session.flush()
        recipe = Recipe(
            page_id=page.id,
            name="Test",
            data={},
            data_sha256=url,
            extractor="json_ld",
        )
        session.add(recipe)
        session.flush()
        return recipe.id


def add_recipe_row(session: Session, url: str, name: str) -> Recipe:
    """The same bare recipe, in the caller's transaction."""
    page = Page(url=url, site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id, name=name, data={}, data_sha256=url, extractor="json_ld"
    )
    session.add(recipe)
    session.flush()
    return recipe


def store_lines(recipe_id: int, data: dict[str, Any]) -> None:
    """Store a recipe's lines through its recipe row: the action runs
    in the caller's session (the pipeline supplies it)."""
    with get_session() as session:
        store_recipe_ingredients(recipe_id, data, BBC_GOOD_FOOD, session)


def add_term(
    session: Session,
    term: str,
    ingredient: Ingredient,
    *,
    variant: str | None = None,
    resolution: str = "seed",
) -> IngredientTerm:
    """A term pointing at a concept, for hand-built lines."""
    row = IngredientTerm(
        term=term, ingredient_id=ingredient.id, variant=variant, resolution=resolution
    )
    session.add(row)
    session.flush()
    return row
