"""Extract raw recipe data from fetched pages.

JSON-LD first: standard `application/ld+json` Recipe objects, with a
per-site fallback (test-id script tags) for sites that don't publish
standard JSON-LD, and schema.org microdata (Jetpack-style HTML
attributes) for sites without any JSON-LD at all. Parsing into
structured recipes happens in food-guru; menu only stores the raw
data.
"""

import json
from collections import deque
from itertools import takewhile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

    from menu.ingest.registry import SiteConfig

JSON_LD_TYPE = "application/ld+json"


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
            data = json.loads(script.string or "", strict=False)
        except json.JSONDecodeError, TypeError:
            continue
        queue = deque(data if isinstance(data, list) else [data])
        while queue:
            node = queue.popleft()
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


def extract_microdata_recipe(soup: BeautifulSoup) -> dict[str, Any] | None:
    """Return the schema.org Recipe microdata on the page (Smitten
    Kitchen's Jetpack markup), shaped like JSON-LD so the scratch
    store and ingredient pipeline don't change.

    Flat items only: an itemprop that is itself a nested itemscope
    (nutrition, rating) is skipped, and so is anything inside one —
    but the walk stops at the Recipe element, so an outer itemscope
    (a WordPress BlogPosting wrapper is common) doesn't blank the
    recipe. Returns None unless the markup yields a name and an
    ingredient list: an empty recipe is not a recipe. Jetpack
    publishes no recipeInstructions itemprop; the steps sit in the
    h-recipe's .e-instructions block, so those are filled in when
    missing.
    """
    scope = soup.select_one('[itemtype*="schema.org/Recipe"]')
    if scope is None:
        return None

    recipe: dict[str, Any] = {"@type": "Recipe"}
    for prop in scope.find_all(itemprop=True):
        if prop.has_attr("itemscope"):  # a nested scope, not a value
            continue
        if any(
            parent.has_attr("itemscope")
            for parent in takewhile(lambda p: p is not scope, prop.parents)
        ):
            continue
        value = (
            prop.get("content")
            or prop.get("datetime")
            or prop.get_text(" ", strip=True)
        )
        if not value:
            continue
        name = prop["itemprop"]
        if isinstance(name, list):  # bs4 types every attribute as str | list
            name = " ".join(name)
        existing = recipe.get(name)
        if existing is None:
            recipe[name] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            recipe[name] = [existing, value]
    if isinstance(recipe.get("recipeIngredient"), str):
        recipe["recipeIngredient"] = [recipe["recipeIngredient"]]
    if "recipeInstructions" not in recipe:
        steps = scope.select_one(".e-instructions")
        if steps is not None:
            recipe["recipeInstructions"] = steps.get_text(" ", strip=True)
    if "name" not in recipe or "recipeIngredient" not in recipe:
        return None
    return recipe


def extract_recipe_data(site: SiteConfig, soup: BeautifulSoup) -> dict[str, Any] | None:
    """Recipe JSON for a page: the site's test-id script if it declares
    one, then standard JSON-LD, then schema.org microdata, then None.
    Doubles as the recipe check: a page is a recipe iff it yields a
    Recipe object."""
    if site.json_ld_test_id:
        script = soup.find("script", {"data-testid": site.json_ld_test_id})
        if script and script.contents:
            try:
                data = json.loads(str(script.contents[0]), strict=False)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and _is_recipe(data):
                return data

    return extract_json_ld_recipe(soup) or extract_microdata_recipe(soup)
