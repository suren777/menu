import json
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


MICRODATA_PAGE = """
<html><body>
  <div itemscope itemtype="https://schema.org/Recipe">
    <span itemprop="name">Smitten Cake</span>
    <p itemprop="recipeYield">Serves 8</p>
    <time itemprop="totalTime" datetime="PT1H30M">1.5 hours</time>
    <span itemprop="recipeIngredient">200g butter</span>
    <span itemprop="recipeIngredient">100g caster sugar</span>
    <div class="e-instructions"><p>Step one.</p></div>
    <div itemscope itemtype="https://schema.org/AggregateRating">
      <span itemprop="ratingValue">5</span>
    </div>
  </div>
</body></html>
"""


def test_extract_microdata_recipe_shapes_like_json_ld() -> None:
    soup = BeautifulSoup(MICRODATA_PAGE, "html.parser")
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert recipe["@type"] == "Recipe"
    assert recipe["name"] == "Smitten Cake"
    assert recipe["recipeYield"] == "Serves 8"
    assert recipe["totalTime"] == "PT1H30M"  # datetime attr beats the text
    assert recipe["recipeIngredient"] == ["200g butter", "100g caster sugar"]


def test_extract_microdata_recipe_fills_instructions_from_e_instructions() -> None:
    soup = BeautifulSoup(MICRODATA_PAGE, "html.parser")
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert recipe["recipeInstructions"] == "Step one."


def test_extract_microdata_recipe_skips_nested_itemscope() -> None:
    soup = BeautifulSoup(MICRODATA_PAGE, "html.parser")
    recipe = extract_microdata_recipe(soup)
    assert recipe is not None
    assert "ratingValue" not in recipe


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
    soup = BeautifulSoup(MICRODATA_PAGE, "html.parser")
    data = extract_recipe_data(site, soup)
    assert data is not None
    assert data["name"] == "Smitten Cake"


def test_extract_recipe_data_prefers_json_ld_over_microdata() -> None:
    site = SiteConfig(name="x", base_url="u", sitemap_urls=("s",))
    soup = BeautifulSoup(
        MICRODATA_PAGE
        + '<script type="application/ld+json">'
        + json.dumps(JSON_LD_RECIPE)
        + "</script>",
        "html.parser",
    )
    data = extract_recipe_data(site, soup)
    assert data is not None
    assert data["name"] == "Test Recipe"


def test_extract_json_ld_allows_control_characters() -> None:
    soup = BeautifulSoup("<html><body></body></html>", "html.parser")
    script = soup.new_tag("script", attrs={"type": "application/ld+json"})
    # A raw newline inside a JSON string is invalid strict JSON.
    script.string = '{"@type": "Recipe", "name": "Cake\nwith a newline"}'
    soup.body.append(script)  # type: ignore[union-attr]
    recipe = extract_json_ld_recipe(soup)
    assert recipe is not None
    assert recipe["name"] == "Cake\nwith a newline"
