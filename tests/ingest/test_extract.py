import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from menu.ingest.extract import (
    extract_json_ld,
    extract_json_ld_recipe,
    extract_microdata_recipe,
    extract_recipe_data,
)
from menu.ingest.registry import SiteConfig
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

FIXTURES = Path(__file__).parent / "fixtures"
# Trimmed saved pages: the Smitten Kitchen Recipe itemscope div, and
# an Ottolenghi JSON-LD script whose strings hold raw newlines.
SMITTEN_PAGE = (FIXTURES / "smitten_microdata.html").read_text()
OTTOLENGHI_CONTROL_CHARS = (FIXTURES / "ottolenghi_control_chars.html").read_text()

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
    assert recipe is not None
    assert recipe["name"] == "Test Recipe"


def test_extract_json_ld_follows_graph() -> None:
    wrapped = {"@context": "https://schema.org", "@graph": [JSON_LD_RECIPE]}
    soup = _page_with_script({"type": "application/ld+json"}, wrapped)
    recipe = extract_json_ld_recipe(soup)
    assert recipe is not None
    assert recipe["name"] == "Test Recipe"


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
    assert data is not None
    assert data["name"] == "Test Recipe"


def test_extract_recipe_data_none_when_absent() -> None:
    site = SiteConfig(name="x", base_url="u", sitemap_urls=("s",))
    soup = BeautifulSoup("<html><body>no data</body></html>", "html.parser")
    assert extract_recipe_data(site, soup) is None


# The saved Smitten Kitchen page has no nested itemscope, so this
# minimal snippet covers the one the fallback must skip.
NESTED_PAGE = """
<div itemscope itemtype="https://schema.org/Recipe">
  <span itemprop="name">Cake</span>
  <span itemprop="recipeIngredient">1 egg</span>
  <div itemscope itemtype="https://schema.org/AggregateRating">
    <span itemprop="ratingValue">5</span>
  </div>
</div>
"""


def test_extract_microdata_recipe_shapes_like_json_ld() -> None:
    soup = BeautifulSoup(SMITTEN_PAGE, "html.parser")
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert recipe["@type"] == "Recipe"
    assert recipe["name"] == "Double Chocolate Banana Bread"
    assert recipe["recipeYield"] == "Servings: 8"
    assert recipe["totalTime"] == "P0DT1H30M0S"  # datetime attr beats the text
    assert recipe["recipeIngredient"][0] == "3 medium-to-large very ripe bananas"
    assert len(recipe["recipeIngredient"]) == 11


def test_extract_microdata_recipe_fills_instructions_from_e_instructions() -> None:
    soup = BeautifulSoup(SMITTEN_PAGE, "html.parser")
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert recipe["recipeInstructions"].startswith("Heat your oven to 350°F.")


def test_extract_microdata_recipe_skips_nested_itemscope() -> None:
    soup = BeautifulSoup(NESTED_PAGE, "html.parser")
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert "ratingValue" not in recipe


def test_extract_microdata_recipe_ignores_outer_itemscope() -> None:
    soup = BeautifulSoup(
        """
        <div itemscope itemtype="https://schema.org/BlogPosting">
          <div itemscope itemtype="https://schema.org/Recipe">
            <span itemprop="name">Wrapped Cake</span>
            <span itemprop="recipeIngredient">1 egg</span>
          </div>
        </div>
        """,
        "html.parser",
    )
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert recipe["name"] == "Wrapped Cake"


def test_extract_microdata_recipe_skips_itemprop_itemscope_blobs() -> None:
    soup = BeautifulSoup(
        """
        <div itemscope itemtype="https://schema.org/Recipe">
          <span itemprop="name">Cake</span>
          <span itemprop="recipeIngredient">1 egg</span>
          <div itemprop="nutrition" itemscope
               itemtype="https://schema.org/NutritionInformation">
            <span itemprop="calories">100 calories</span>
          </div>
        </div>
        """,
        "html.parser",
    )
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert "nutrition" not in recipe


def test_extract_microdata_recipe_none_without_name_or_ingredients() -> None:
    soup = BeautifulSoup(
        '<div itemscope itemtype="https://schema.org/Recipe">'
        '<span itemprop="recipeYield">Serves 8</span></div>',
        "html.parser",
    )
    assert extract_microdata_recipe(soup) is None


def test_extract_microdata_recipe_wraps_single_ingredient_in_a_list() -> None:
    soup = BeautifulSoup(
        """
        <div itemscope itemtype="https://schema.org/Recipe">
          <span itemprop="name">One-liner</span>
          <span itemprop="recipeIngredient">1 egg</span>
        </div>
        """,
        "html.parser",
    )
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert recipe["recipeIngredient"] == ["1 egg"]


def test_extract_recipe_data_falls_back_to_microdata() -> None:
    site = SiteConfig(name="x", base_url="u", sitemap_urls=("s",))
    soup = BeautifulSoup(SMITTEN_PAGE, "html.parser")
    data = extract_recipe_data(site, soup)
    assert data is not None
    assert data["name"] == "Double Chocolate Banana Bread"


def test_extract_recipe_data_prefers_json_ld_over_microdata() -> None:
    site = SiteConfig(name="x", base_url="u", sitemap_urls=("s",))
    soup = BeautifulSoup(
        SMITTEN_PAGE
        + '<script type="application/ld+json">'
        + json.dumps(JSON_LD_RECIPE)
        + "</script>",
        "html.parser",
    )
    data = extract_recipe_data(site, soup)
    assert data is not None
    assert data["name"] == "Test Recipe"


def test_extract_json_ld_allows_control_characters() -> None:
    # A saved Ottolenghi page: raw newlines inside JSON strings are
    # invalid strict JSON, but the real payload must still parse.
    soup = BeautifulSoup(OTTOLENGHI_CONTROL_CHARS, "html.parser")
    recipe = extract_json_ld_recipe(soup)
    assert recipe is not None
    assert recipe["name"] == "Pea and artichoke dip with pickled onions"
