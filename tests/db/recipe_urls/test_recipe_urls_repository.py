from typing import TYPE_CHECKING, Any

from menu.db.database import RecipeUrls
from menu.db.recipe_urls import repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

RECIPE_DATA: dict[str, Any] = {"key": "value"}


def test_to_model() -> None:
    record = RecipeUrls(
        id=1, url="http://test.com", name="Test Recipe", data=RECIPE_DATA
    )

    model = repository.to_model(record)

    assert model.id == 1
    assert model.url == "http://test.com"
    assert model.name == "Test Recipe"
    assert model.data == RECIPE_DATA


def test_find_by_url(session: Session) -> None:
    session.add(RecipeUrls(url="http://test.com", name="Test Recipe", data=RECIPE_DATA))
    session.flush()

    model = repository.find_by_url("http://test.com", session)

    assert model is not None
    assert model.id == 1
    assert model.name == "Test Recipe"
    assert model.data == RECIPE_DATA


def test_find_by_url_missing(session: Session) -> None:
    assert repository.find_by_url("http://missing.com", session) is None


def test_get_all(session: Session) -> None:
    repository.add_recipe("http://test.com", "Test Recipe", RECIPE_DATA, session)
    repository.add_recipe("http://other.com", "Other Recipe", {}, session)

    models = repository.get_all(session)

    assert {model.url for model in models} == {"http://test.com", "http://other.com"}


def test_get_all_by_id(session: Session) -> None:
    repository.add_recipe("http://test.com", "Test Recipe", RECIPE_DATA, session)
    repository.add_recipe("http://other.com", "Other Recipe", {}, session)
    first = repository.find_by_url("http://test.com", session)
    assert first is not None

    models = repository.get_all_by_id([first.id], session)

    assert [model.url for model in models] == ["http://test.com"]


def test_add_recipe_ignores_duplicates(session: Session) -> None:
    """RecipeUrls.url is unique, so re-storing must not duplicate the row."""
    repository.add_recipe("http://test.com", "Test Recipe", RECIPE_DATA, session)
    repository.add_recipe("http://test.com", "Renamed Recipe", {}, session)

    models = repository.get_all(session)

    assert len(models) == 1
    assert models[0].name == "Test Recipe"
