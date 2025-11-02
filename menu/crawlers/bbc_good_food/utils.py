from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup, Tag
from pydash import get

from menu.crawlers.bbc_good_food.common import Nutrition, ParsedRecipe, RecipeKeys


class FetchError(BaseException): ...


def request_xml(url: str) -> list[str]:
    response = requests.get(url, timeout=10)
    if response.ok:
        tree = ElementTree.fromstring(response.content)
        namespace = {"sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [
            url.text
            for url in tree.findall("sitemap:url/sitemap:loc", namespaces=namespace)
            if url.text is not None
        ]
        return urls
    raise FetchError("Can`t fetch the data")


def get_sitemap(url: str) -> list[str]:
    return request_xml(url)


def contains_recipe(content: BeautifulSoup) -> bool:
    ul = content.find(
        "ul",
        {"class": "breadcrumb__list body-copy-extra-small oflow-x-auto list"},
    )
    if isinstance(ul, Tag):
        li = ul.find_all(
            "li",
        )
        return li[1].text == "Recipes" and li[2].text != "Collection"

    return False


def fetch_recipe(url: str) -> BeautifulSoup:
    result = requests.get(url, timeout=10)
    return BeautifulSoup(result.content, features="html.parser")


def parse_keywords(keywords: str) -> list[str]:
    return keywords.replace(" ", "").split(",")


def parse_image(img: dict[str, Any]) -> str:
    return img["url"]


def strip_and_cast(original: str | None, strip: str) -> str | None:
    if original is not None:
        return original.replace(f"{strip}", "")
    return None


def parce_nutrition(nutrition: dict[str, str]) -> Nutrition:
    calories: str | None = get(nutrition, f"{RecipeKeys.NUTRITION}.calories")
    fat: str | None = get(nutrition, f"{RecipeKeys.NUTRITION}.fatContent")
    saturated_fat: str | None = get(
        nutrition, f"{RecipeKeys.NUTRITION}.saturatedFatContent"
    )
    carbohydrate: str | None = get(
        nutrition, f"{RecipeKeys.NUTRITION}.carbohydrateContent"
    )
    sugar: str | None = get(nutrition, f"{RecipeKeys.NUTRITION}.sugarContent")
    fiber: str | None = get(nutrition, f"{RecipeKeys.NUTRITION}.fiberContent")
    protein: str | None = get(nutrition, f"{RecipeKeys.NUTRITION}.proteinContent")
    sodium: str | None = get(nutrition, f"{RecipeKeys.NUTRITION}.sodiumContent")
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


def parce_recipe(recipe: dict[str, Any]) -> ParsedRecipe:
    return ParsedRecipe(
        name=recipe[RecipeKeys.NAME],
        description=recipe[RecipeKeys.DESCRIPTION],
        image=parse_image(recipe[RecipeKeys.IMAGE]),
        keywords=(
            parse_keywords(recipe[RecipeKeys.KEYWORDS])
            if RecipeKeys.KEYWORDS in recipe
            else []
        ),
        cook_time=get(recipe, RecipeKeys.COOK_TIME),
        prep_time=get(recipe, RecipeKeys.PREP_TIME),
        total_time=get(recipe, RecipeKeys.TOTAL_TIME),
        category=get(recipe, RecipeKeys.CATEGORY),
        ingredients=recipe[RecipeKeys.INGREDIENTS],
        instructions=[
            step["text"] for step in recipe[RecipeKeys.INSTRUCTIONS] if "text" in step
        ],
        portions=get(recipe, RecipeKeys.PORTIONS),
        cuisine=get(recipe, RecipeKeys.CUISINE),
        **parce_nutrition(recipe),
    )
