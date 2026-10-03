"""Extract raw recipe JSON-LD from fetched pages.

JSON-LD first: standard `application/ld+json` Recipe objects, with a
per-site fallback (test-id script tags) for sites that don't publish
standard JSON-LD. Parsing into structured recipes happens in food-guru;
menu only stores the raw data.
"""

import json
from collections import deque
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
            data = json.loads(script.string or "")
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


def extract_recipe_data(site: SiteConfig, soup: BeautifulSoup) -> dict[str, Any] | None:
    """Recipe JSON for a page: the site's test-id script if it declares
    one, then standard JSON-LD, then None. Doubles as the recipe check:
    a page is a recipe iff it yields a Recipe object."""
    if site.json_ld_test_id:
        script = soup.find("script", {"data-testid": site.json_ld_test_id})
        if script and script.contents:
            try:
                data = json.loads(str(script.contents[0]))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and _is_recipe(data):
                return data

    return extract_json_ld_recipe(soup)
