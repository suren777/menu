import json
from typing import Any

from bs4 import BeautifulSoup

from menu.ingest.extract import (
    extract_json_ld,
    extract_json_ld_recipe,
    extract_recipe_data,
)
from menu.ingest.registry import SiteConfig
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

JSON_LD_RECIPE = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Test Recipe",
    "description": "A test recipe",
    "image": {"url": "http://test.com/image.jpg"},
    "keywords": "test, recipe",
    "recipeYield": "4",
    "recipeIngredient": ["ingredient1", "ingredient2"],
    "recipeInstructions": [{"text": "step 1"}, {"text": "step 2"}],
    "recipeCuisine": "Test Cuisine",
    "nutrition": {"calories": "100 calories"},
}


def _page_with_script(
    script_attrs: dict[str, str], payload: dict[str, Any]
) -> BeautifulSoup:
    soup = BeautifulSoup("<html><body></body></html>", "html.parser")
    script = soup.new_tag("script", attrs=script_attrs)
    script.string = json.dumps(payload)
    soup.body.append(script)  # type: ignore[union-attr]
    return soup


def test_extract_json_ld_finds_recipe() -> None:
    soup = _page_with_script({"type": "application/ld+json"}, JSON_LD_RECIPE)
    recipe = extract_json_ld_recipe(soup)
    assert recipe is not None and recipe["name"] == "Test Recipe"


def test_extract_json_ld_follows_graph() -> None:
    wrapped = {"@context": "https://schema.org", "@graph": [JSON_LD_RECIPE]}
    soup = _page_with_script({"type": "application/ld+json"}, wrapped)
    recipe = extract_json_ld_recipe(soup)
    assert recipe is not None and recipe["name"] == "Test Recipe"


def test_extract_json_ld_ignores_non_recipe() -> None:
    soup = _page_with_script(
        {"type": "application/ld+json"}, {"@type": "WebSite", "name": "x"}
    )
    assert extract_json_ld_recipe(soup) is None
    assert extract_json_ld(soup) != []


def test_extract_recipe_data_prefers_test_id_script() -> None:
    payload = {"@type": "Recipe", "name": "From test id"}
    soup = _page_with_script({"data-testid": "page-schema"}, payload)
    assert extract_recipe_data(BBC_GOOD_FOOD, soup) == payload


def test_extract_recipe_data_ignores_non_recipe_test_id_script() -> None:
    payload = {"@type": "BreadcrumbList", "name": "Not a recipe"}
    soup = _page_with_script({"data-testid": "page-schema"}, payload)
    assert extract_recipe_data(BBC_GOOD_FOOD, soup) is None


def test_extract_recipe_data_falls_back_to_json_ld() -> None:
    soup = _page_with_script({"type": "application/ld+json"}, JSON_LD_RECIPE)
    data = extract_recipe_data(BBC_GOOD_FOOD, soup)
    assert data is not None and data["name"] == "Test Recipe"


def test_extract_recipe_data_none_when_absent() -> None:
    site = SiteConfig(name="x", base_url="u", sitemap_url="s")
    soup = BeautifulSoup("<html><body>no data</body></html>", "html.parser")
    assert extract_recipe_data(site, soup) is None
