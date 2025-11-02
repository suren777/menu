from unittest.mock import patch, MagicMock
from menu.db.recipes.helpers import (
    parse_categories,
    get_categories,
    get_cuisines,
    parse_literal_array,
    recipe_to_text,
    create_recipe_filters,
    get_recipe_names,
    get_recipe_by_id,
    search_recipe_by_name,
    search_recipes_by_ingredients,
    get_recipes,
)
from menu.db.database import RecipeTable

def test_parse_categories():
    assert parse_categories(["dessert, main", "breakfast"]) == ["breakfast", "dessert", "main"]

@patch('menu.db.recipes.helpers.get_ro_session')
def test_get_categories(mock_get_ro_session):
    mock_session = MagicMock()
    mock_session.query.return_value.distinct.return_value.all.return_value = [("dessert",), ("main",)]
    mock_get_ro_session.return_value.__enter__.return_value = mock_session
    assert get_categories() == ["dessert", "main"]

@patch('menu.db.recipes.helpers.get_ro_session')
def test_get_cuisines(mock_get_ro_session):
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.distinct.return_value.all.return_value = [("italian",), ("mexican",)]
    mock_get_ro_session.return_value.__enter__.return_value = mock_session
    assert get_cuisines() == ["italian", "mexican"]

def test_parse_literal_array():
    assert parse_literal_array("['one', 'two']") == "one\ntwo"
    assert parse_literal_array("['one', 'two']", enumerate_list=True) == "1. one\n2. two"

def test_recipe_to_text():
    recipe = RecipeTable(name="test recipe", portions=2, description="test description", ingredients="['ingredient1', 'ingredient2']", instructions="['step1', 'step2']")
    assert "<b>test recipe</b>" in recipe_to_text(recipe)
    assert "Portions: 2" in recipe_to_text(recipe)
    assert "<i>test description</i>" in recipe_to_text(recipe)
    assert "<b>Ingredients:</b>" in recipe_to_text(recipe)
    assert "ingredient1\ningredient2" in recipe_to_text(recipe)
    assert "<b>Instructions:</b>" in recipe_to_text(recipe)
    assert "1. step1\n2. step2" in recipe_to_text(recipe)

def test_create_recipe_filters():
    filters = create_recipe_filters(cuisine="italian", category="dessert", ingredient="chocolate")
    assert len(filters) == 3

@patch('menu.db.recipes.helpers.get_ro_session')
def test_get_recipe_names(mock_get_ro_session):
    mock_session = MagicMock()
    mock_recipe = RecipeTable(name="test recipe", id=1)
    mock_session.query.return_value.filter.return_value = [mock_recipe]
    mock_get_ro_session.return_value.__enter__.return_value = mock_session
    assert get_recipe_names(cuisine="italian", category=None) == [{'name': 'test recipe', 'id': 1}]

@patch('menu.db.recipes.helpers.get_ro_session')
def test_get_recipe_by_id(mock_get_ro_session):
    mock_session = MagicMock()
    mock_recipe = RecipeTable(name="test recipe", id=1)
    mock_session.query.return_value.filter.return_value.first.return_value = mock_recipe
    mock_get_ro_session.return_value.__enter__.return_value = mock_session
    assert get_recipe_by_id(1) is not None

@patch('menu.db.recipes.helpers.get_ro_session')
def test_search_recipe_by_name(mock_get_ro_session):
    mock_session = MagicMock()
    mock_recipe = RecipeTable(name="test recipe", id=1)
    mock_session.query.return_value.filter.return_value = [mock_recipe]
    mock_get_ro_session.return_value.__enter__.return_value = mock_session
    assert search_recipe_by_name("test") == [{'name': 'test recipe', 'id': 1}]

@patch('menu.db.recipes.helpers.get_ro_session')
def test_search_recipes_by_ingredients(mock_get_ro_session):
    mock_session = MagicMock()
    mock_recipe = RecipeTable(name="test recipe", id=1)
    mock_session.query.return_value.filter.return_value = [mock_recipe]
    mock_get_ro_session.return_value.__enter__.return_value = mock_session
    assert search_recipes_by_ingredients(["ingredient1"]) == [{'name': 'test recipe', 'id': 1}]

@patch('menu.db.recipes.helpers.get_ro_session')
def test_get_recipes(mock_get_ro_session):
    mock_session = MagicMock()
    mock_recipe = RecipeTable(name="test recipe", id=1)
    mock_session.query.return_value.filter.return_value.limit.return_value.all.return_value = [mock_recipe]
    mock_get_ro_session.return_value.__enter__.return_value = mock_session
    assert len(get_recipes(cuisine="italian", category=None)) == 1
