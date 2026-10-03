"""Repository tests for the ingredient tables."""

from typing import TYPE_CHECKING

from menu.db.database import Ingredient, IngredientAlias, RecipeIngredient, RecipeUrls
from menu.db.ingredients import repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_find_ingredient_by_name(session: Session) -> None:
    session.add(Ingredient(name="milk", density_g_per_ml=1.03))
    session.flush()

    found = repository.find_ingredient_by_name("milk", session)
    missing = repository.find_ingredient_by_name("butter", session)

    assert found is not None
    assert (found.name, found.density_g_per_ml) == ("milk", 1.03)
    assert missing is None


def test_alias_target(session: Session) -> None:
    ingredient = Ingredient(name="milk")
    session.add(ingredient)
    session.flush()
    session.add(
        IngredientAlias(
            alias="warm milk", ingredient_id=ingredient.id, variant="warm"
        )
    )

    found = repository.alias_target("warm milk", session)
    assert found is not None
    assert (found[0].name, found[1]) == ("milk", "warm")
    assert repository.alias_target("cold milk", session) is None


def test_get_recipe_ingredients_in_position_order(session: Session) -> None:
    recipe = RecipeUrls(url="https://x/1", name="X", data={})
    session.add(recipe)
    session.flush()
    session.add(
        RecipeIngredient(recipe_id=recipe.id, position=1, raw_text="butter")
    )
    session.add(RecipeIngredient(recipe_id=recipe.id, position=0, raw_text="milk"))

    lines = repository.get_recipe_ingredients(recipe.id, session)

    assert [line.raw_text for line in lines] == ["milk", "butter"]


def test_get_unresolved(session: Session) -> None:
    ingredient = Ingredient(name="milk")
    recipe = RecipeUrls(url="https://x/1", name="X", data={})
    session.add_all([ingredient, recipe])
    session.flush()
    session.add(
        RecipeIngredient(recipe_id=recipe.id, position=0, raw_text="mystery")
    )
    session.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            position=1,
            raw_text="milk",
            ingredient_id=ingredient.id,
        )
    )

    unresolved = repository.get_unresolved(session)

    assert [line.raw_text for line in unresolved] == ["mystery"]
