from unittest.mock import MagicMock, patch

from menu.db.database import RecipeUrls
from menu.db.recipie_urls.repository import RecipeUrlsRepository


def test_from_record():
    record = RecipeUrls(
        id=1, url="http://test.com", name="Test Recipe", data={"key": "value"}
    )
    model = RecipeUrlsRepository.from_record(record)
    assert model.id == 1
    assert model.url == "http://test.com"
    assert model.name == "Test Recipe"
    assert model.data == {"key": "value"}


def test_to_record():
    model = MagicMock()
    model.id = 1
    model.url = "http://test.com"
    model.name = "Test Recipe"
    model.data = {"key": "value"}
    record = RecipeUrlsRepository.to_record(model)
    assert record.id == 1
    assert record.url == "http://test.com"
    assert record.name == "Test Recipe"
    assert record.data == {"key": "value"}


def test_find_by_url():
    session = MagicMock()
    repo = RecipeUrlsRepository()
    repo.find_by_url("http://test.com", session)
    session.query.assert_called_once()


def test_url_exists():
    session = MagicMock()
    RecipeUrlsRepository.url_exists("http://test.com", session)
    session.query.assert_called_once()


def test_get_all():
    session = MagicMock()
    RecipeUrlsRepository.get_all(session)
    session.query.assert_called_once()


def test_get_all_by_id():
    session = MagicMock()
    RecipeUrlsRepository.get_all_by_id([1, 2], session)
    session.query.assert_called_once()


def test_add_recipe():
    session = MagicMock()
    with patch.object(RecipeUrlsRepository, "url_exists", return_value=False):
        RecipeUrlsRepository.add_recipe("http://test.com", "Test Recipe", {}, session)
        session.add.assert_called_once()


def test_add_recipe_exists():
    session = MagicMock()
    with patch.object(RecipeUrlsRepository, "url_exists", return_value=True):
        RecipeUrlsRepository.add_recipe("http://test.com", "Test Recipe", {}, session)
        session.add.assert_not_called()
