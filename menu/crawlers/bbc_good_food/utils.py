from typing import Any
import requests
from xml.etree import ElementTree
from bs4 import BeautifulSoup
from menu.crawlers.bbc_good_food.common import Nutrition, ParsedRecipe, RecipeKeys
from pydash import get


class FetchError(BaseException):
    ...


def request_xml(url: str) -> list[str]:
    response = requests.get(url)
    if response.ok:
        tree = ElementTree.fromstring(response.content)  # %%
        return [leaf[0].text for leaf in tree]
    raise FetchError("Can`t fetch the data")


def get_sitemap(url: str) -> list[str]:
    return request_xml(url)


def contains_recipe(content: BeautifulSoup) -> bool:
    ul = content.find(
        "ul",
        {"class": "breadcrumb__list body-copy-extra-small oflow-x-auto list"},
    )
    if ul is not None:
        li = ul.find_all(
            "li",
        )
        return li[1].text == "Recipes" and li[2].text != "Collection"

    return False


def fetch_recipe(url: str) -> BeautifulSoup:
    result = requests.get(url)
    return BeautifulSoup(result.content, features="html.parser")


def parse_keywords(keywords: str) -> list[str]:
    return keywords.replace(" ", "").split(",")


def parse_image(img: dict[str, Any]) -> str:
    return img["url"]


def strip_and_cast(original: str | None, strip: str) -> float | None:
    if original is not None:
        return float(original.replace(f"{strip}", ""))
    return None


def parce_nutrition(nutrition: dict[str, str]) -> Nutrition:
    calories: str | None = get(nutrition, f"{RecipeKeys.NUTRITION}.calories")
    fat = get(nutrition, f"{RecipeKeys.NUTRITION}.fatContent")
    saturated_fat = get(nutrition, f"{RecipeKeys.NUTRITION}.saturatedFatContent")
    carbohydrate = get(nutrition, f"{RecipeKeys.NUTRITION}.carbohydrateContent")
    sugar = get(nutrition, f"{RecipeKeys.NUTRITION}.sugarContent")
    fiber = get(nutrition, f"{RecipeKeys.NUTRITION}.fiberContent")
    protein = get(nutrition, f"{RecipeKeys.NUTRITION}.proteinContent")
    sodium = get(nutrition, f"{RecipeKeys.NUTRITION}.sodiumContent")
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
        keywords=parse_keywords(recipe[RecipeKeys.KEYWORDS])
        if RecipeKeys.KEYWORDS in recipe
        else [],
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
