from unittest.mock import patch, MagicMock
import pytest
from bs4 import BeautifulSoup
from menu.crawlers.bbc_good_food.utils import (
    request_xml,
    get_sitemap,
    contains_recipe,
    fetch_recipe,
    parse_keywords,
    parse_image,
    strip_and_cast,
    parce_nutrition,
    parce_recipe,
    FetchError,
)

@patch('requests.get')
def test_request_xml_ok(mock_get):
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.content = b'<urlset><url><loc>http://test.com</loc></url></urlset>'
    mock_get.return_value = mock_response

    result = request_xml("http://test.com/sitemap.xml")
    assert result == ["http://test.com"]

@patch('requests.get')
def test_request_xml_fail(mock_get):
    mock_response = MagicMock()
    mock_response.ok = False
    mock_get.return_value = mock_response

    with pytest.raises(FetchError):
        request_xml("http://test.com/sitemap.xml")

def test_get_sitemap():
    with patch('menu.crawlers.bbc_good_food.utils.request_xml', return_value=["http://test.com"]) as mock_request_xml:
        result = get_sitemap("http://test.com/sitemap.xml")
        assert result == ["http://test.com"]
        mock_request_xml.assert_called_once_with("http://test.com/sitemap.xml")

def test_contains_recipe():
    html_with_recipe = '<html><body><ul class="breadcrumb__list body-copy-extra-small oflow-x-auto list"><li>Home</li><li>Recipes</li><li>Dessert</li></ul></body></html>'
    soup_with_recipe = BeautifulSoup(html_with_recipe, 'html.parser')
    assert contains_recipe(soup_with_recipe)

    html_without_recipe = '<html><body><ul class="breadcrumb__list body-copy-extra-small oflow-x-auto list"><li>Home</li><li>Recipes</li><li>Collection</li></ul></body></html>'
    soup_without_recipe = BeautifulSoup(html_without_recipe, 'html.parser')
    assert not contains_recipe(soup_without_recipe)

@patch('requests.get')
def test_fetch_recipe(mock_get):
    mock_response = MagicMock()
    mock_response.content = b'<html><body><h1>Test Recipe</h1></body></html>'
    mock_get.return_value = mock_response

    result = fetch_recipe("http://test.com/recipe")
    assert "Test Recipe" in str(result)

def test_parse_keywords():
    assert parse_keywords("one, two, three") == ["one", "two", "three"]

def test_parse_image():
    assert parse_image({"url": "http://test.com/image.jpg"}) == "http://test.com/image.jpg"

def test_strip_and_cast():
    assert strip_and_cast("10 grams", " grams") == 10.0
    assert strip_and_cast(None, " grams") is None

def test_parce_nutrition():
    nutrition_data = {
        "nutrition": {
            "calories": "100 calories",
            "fatContent": "10 grams fat",
            "saturatedFatContent": "5 grams saturated fat",
            "carbohydrateContent": "20 grams carbohydrates",
            "sugarContent": "15 grams sugar",
            "fiberContent": "5 grams fiber",
            "proteinContent": "10 grams protein",
            "sodiumContent": "500 milligram of sodium",
        }
    }
    nutrition = parce_nutrition(nutrition_data)
    assert nutrition["calories"] == 100.0

def test_parce_recipe():
    recipe_data = {
        "name": "Test Recipe",
        "description": "A test recipe",
        "image": {"url": "http://test.com/image.jpg"},
        "keywords": "test, recipe",
        "recipeYield": "4",
        "recipeIngredient": ["ingredient1", "ingredient2"],
        "recipeInstructions": [{"text": "step 1"}, {"text": "step 2"}],
        "recipeCuisine": "Test Cuisine",
        "nutrition": {
            "calories": "100 calories",
        },
    }
    parsed_recipe = parce_recipe(recipe_data)
    assert parsed_recipe["name"] == "Test Recipe"
    assert parsed_recipe["cuisine"] == "Test Cuisine"
