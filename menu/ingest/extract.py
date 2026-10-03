"""Extract recipe data from fetched pages.

JSON-LD first: standard `application/ld+json` Recipe objects, with a
per-site fallback (test-id script tags) for sites that don't publish
standard JSON-LD.
"""

import json
from typing import Any, TypedDict

from bs4 import BeautifulSoup
from pydash import get

from menu.ingest.registry import SiteConfig
from menu.ingest.sites.bbc_good_food import contains_recipe

JSON_LD_TYPE = "application/ld+json"


class Nutrition(TypedDict):
    calories: str | None
    fat: str | None
    saturated_fat: str | None
    carbohydrate: str | None
    sugar: str | None
    fiber: str | None
    protein: str | None
    sodium: str | None


class ParsedRecipe(TypedDict):
    name: str
    description: str
    image: str
    keywords: list[str]
    cook_time: str
    prep_time: str
    total_time: str
    category: list[str]
    ingredients: list[str]
    instructions: list[str]
    portions: int | None
    cuisine: str | None
    calories: str | None
    fat: str | None
    saturated_fat: str | None
    carbohydrate: str | None
    sugar: str | None
    fiber: str | None
    protein: str | None
    sodium: str | None


def _is_recipe(node: dict[str, Any]) -> bool:
    types = node.get("@type", [])
    types = types if isinstance(types, list) else [types]
    return "Recipe" in types


def extract_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Return all JSON-LD objects embedded in the page.

    Follows @graph lists so sites that nest everything in one graph
    document are handled too.
    """
    nodes: list[dict[str, Any]] = []
    for script in soup.find_all("script", {"type": JSON_LD_TYPE}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        queue = data if isinstance(data, list) else [data]
        while queue:
            node = queue.pop(0)
            if not isinstance(node, dict):
                continue
            if isinstance(node.get("@graph"), list):
                queue.extend(node["@graph"])
            nodes.append(node)
    return nodes


def extract_json_ld_recipe(soup: BeautifulSoup) -> dict[str, Any] | None:
    """Return the first standard JSON-LD Recipe object on the page."""
    for node in extract_json_ld(soup):
        if _is_recipe(node):
            return node
    return None


def extract_recipe_data(site: SiteConfig, soup: BeautifulSoup) -> dict[str, Any] | None:
    """Recipe JSON for a page: the site's test-id script if it declares
    one, then standard JSON-LD, then None."""
    if site.json_ld_test_id:
        script = soup.find("script", {"data-testid": site.json_ld_test_id})
        if script and script.contents:
            try:
                data = json.loads(str(script.contents[0]))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                return data

    return extract_json_ld_recipe(soup)


def looks_like_recipe(site: SiteConfig, soup: BeautifulSoup) -> bool:
    """Site-specific page check; falls back to JSON-LD presence."""
    if site.name == "bbc_good_food":
        return contains_recipe(soup)
    return extract_json_ld_recipe(soup) is not None


def parse_keywords(keywords: str) -> list[str]:
    return keywords.replace(" ", "").split(",")


def parse_image(img: dict[str, Any]) -> str:
    return str(img["url"])


def strip_and_cast(original: str | None, strip: str) -> str | None:
    if original is not None:
        return original.replace(f"{strip}", "")
    return None


def parse_nutrition(nutrition: dict[str, Any]) -> Nutrition:
    calories = get(nutrition, "nutrition.calories")
    fat = get(nutrition, "nutrition.fatContent")
    saturated_fat = get(nutrition, "nutrition.saturatedFatContent")
    carbohydrate = get(nutrition, "nutrition.carbohydrateContent")
    sugar = get(nutrition, "nutrition.sugarContent")
    fiber = get(nutrition, "nutrition.fiberContent")
    protein = get(nutrition, "nutrition.proteinContent")
    sodium = get(nutrition, "nutrition.sodiumContent")
    return Nutrition(
        calories=strip_and_cast(calories, " calories"),
        fat=strip_and_cast(fat, " grams fat"),
        saturated_fat=strip_and_cast(saturated_fat, " grams saturated fat"),
        carbohydrate=strip_and_cast(carbohydrate, " grams carbohydrates"),
        sugar=strip_and_cast(sugar, " grams sugar"),
        fiber=strip_and_cast(fiber, " grams fiber"),
        protein=strip_and_cast(protein, " grams protein"),
        sodium=strip_and_cast(sodium, " milligram of sodium"),
    )


def parse_recipe(recipe: dict[str, Any]) -> ParsedRecipe:
    return ParsedRecipe(
        name=recipe["name"],
        description=recipe["description"],
        image=parse_image(recipe["image"]),
        keywords=(parse_keywords(recipe["keywords"]) if "keywords" in recipe else []),
        cook_time=get(recipe, "cookTime"),
        prep_time=get(recipe, "prepTime"),
        total_time=get(recipe, "totalTime"),
        category=get(recipe, "recipeCategory"),
        ingredients=recipe["recipeIngredient"],
        instructions=[
            step["text"] for step in recipe["recipeInstructions"] if "text" in step
        ],
        portions=get(recipe, "recipeYield"),
        cuisine=get(recipe, "recipeCuisine"),
        **parse_nutrition(recipe),
    )
