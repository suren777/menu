"""Repository tests for the ingredient tables."""

from typing import TYPE_CHECKING

from menu.db.database import (
    Ingredient,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
)
from menu.db.ingredients import repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_recipe(session: Session) -> Recipe:
    """A bare recipe row, reached through its page (the ledger)."""
    page = Page(url="https://x/1", site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id, name="X", data={}, data_sha256="x", extractor="json_ld"
    )
    session.add(recipe)
    session.flush()
    return recipe


def test_find_ingredient_by_name(session: Session) -> None:
    session.add(Ingredient(name="milk"))
    session.flush()

    found = repository.find_ingredient_by_name("milk", session)
    missing = repository.find_ingredient_by_name("butter", session)

    assert found is not None
    assert found.name == "milk"
    assert missing is None


def test_effective_ingredient_id_is_the_one_rule() -> None:
    """The term's concept — unless the term is unresolved
    (review/not_food) or missing, or the line is flagged and the term
    is not seeded."""
    seed_term = IngredientTerm(term="milk", ingredient_id=1, resolution="seed")
    auto_term = IngredientTerm(term="warm milk", ingredient_id=2, resolution="auto")
    review = IngredientTerm(term="junk food", resolution="review")
    not_food = IngredientTerm(term="bundt tin", resolution="not_food")

    assert repository.effective_ingredient_id(False, seed_term) == 1
    assert repository.effective_ingredient_id(False, auto_term) == 2
    # A seeded term wins over the line flag; an auto term does not.
    assert repository.effective_ingredient_id(True, seed_term) == 1
    assert repository.effective_ingredient_id(True, auto_term) is None
    assert repository.effective_ingredient_id(False, review) is None
    assert repository.effective_ingredient_id(False, not_food) is None
    assert repository.effective_ingredient_id(False, None) is None


def test_get_recipe_ingredients_in_position_order(session: Session) -> None:
    recipe = _add_recipe(session)
    session.add(
        RecipeIngredient(recipe_id=recipe.id, position=1, raw_text="butter")
    )
    session.add(RecipeIngredient(recipe_id=recipe.id, position=0, raw_text="milk"))

    lines = repository.get_recipe_ingredients(recipe.id, session)

    assert [line.raw_text for line in lines] == ["milk", "butter"]


def test_get_recipe_ingredients_carries_term_data(session: Session) -> None:
    """The model reaches the variant and the effective ingredient
    through the term."""
    milk = Ingredient(name="milk")
    session.add(milk)
    session.flush()
    term = IngredientTerm(
        term="warm milk", ingredient_id=milk.id, variant="warm", resolution="seed"
    )
    session.add(term)
    recipe = _add_recipe(session)
    session.add(
        RecipeIngredient(
            recipe_id=recipe.id, position=0, raw_text="warm milk", term_id=term.id
        )
    )

    (line,) = repository.get_recipe_ingredients(recipe.id, session)

    assert (line.ingredient_id, line.variant) == (milk.id, "warm")


def test_get_unresolved(session: Session) -> None:
    """A line is unresolved for review when it has no effective
    ingredient: no term, a review term, or the line-level flag."""
    milk = Ingredient(name="milk")
    session.add(milk)
    session.flush()
    resolved_term = IngredientTerm(
        term="milk", ingredient_id=milk.id, resolution="seed"
    )
    review_term = IngredientTerm(term="mystery powder", resolution="review")
    auto_term = IngredientTerm(
        term="milk or something", ingredient_id=milk.id, resolution="auto"
    )
    session.add_all([resolved_term, review_term, auto_term])
    recipe = _add_recipe(session)
    session.add(
        RecipeIngredient(recipe_id=recipe.id, position=0, raw_text="mystery")
    )
    session.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            position=1,
            raw_text="milk",
            term_id=resolved_term.id,
        )
    )
    session.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            position=2,
            raw_text="mangled name",
            term_id=review_term.id,
        )
    )
    session.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            position=3,
            raw_text="milk or something",
            term_id=auto_term.id,
            needs_review=True,
        )
    )

    unresolved = repository.get_unresolved(session)

    assert [line.raw_text for line in unresolved] == [
        "mystery",
        "mangled name",
        "milk or something",
    ]
