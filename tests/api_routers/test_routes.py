from fastapi.testclient import TestClient
from unittest.mock import patch
from menu.webapp import create_app

client = TestClient(create_app())

def test_healthcheck():
    response = client.get("/healthcheck")
    assert response.status_code == 200
    assert response.json() == {"service": "menu", "environment": "dev"}

@patch('menu.api_routers.routes.get_categories')
def test_recipe_categories(mock_get_categories):
    mock_get_categories.return_value = ["dessert", "main"]
    response = client.get("/categories")
    assert response.status_code == 200
    assert response.json() == ["dessert", "main"]

@patch('menu.api_routers.routes.get_cuisines')
def test_recipe_cuisines(mock_get_cuisines):
    mock_get_cuisines.return_value = ["italian", "mexican"]
    response = client.get("/cuisines")
    assert response.status_code == 200
    assert response.json() == ["italian", "mexican"]

@patch('menu.api_routers.routes.get_recipes')
def test_get_recipe(mock_get_recipes):
    mock_get_recipes.return_value = ["recipe1", "recipe2"]
    response = client.post("/get-recipe", json={"cuisine": "italian", "category": "dessert", "ingredient": "chocolate"})
    assert response.status_code == 200
    assert response.json() == ["recipe1", "recipe2"]
    mock_get_recipes.assert_called_once_with(cuisine="italian", category="dessert", ingredient="chocolate")
