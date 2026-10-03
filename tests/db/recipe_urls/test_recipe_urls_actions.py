from typing import TYPE_CHECKING, Any

from menu.db.recipe_urls import actions, repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_add_recipe(session: Session) -> None:
    """add_recipe opens its own session on the (patched) default engine."""
    recipe_data: dict[str, Any] = {"key": "value"}

    actions.add_recipe("http://test.com", "Test Recipe", recipe_data)

    model = repository.find_by_url("http://test.com", session)
    assert model is not None
    assert model.name == "Test Recipe"
    assert model.data == recipe_data
