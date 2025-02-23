from enum import StrEnum
from typing import TypedDict


class RecipeKeys(StrEnum):
    DESCRIPTION = "description"
    IMAGE = "image"
    NAME = "name"
    KEYWORDS = "keywords"
    COOK_TIME = "cookTime"
    NUTRITION = "nutrition"
    PREP_TIME = "prepTime"
    CATEGORY = "recipeCategory"
    INGREDIENTS = "recipeIngredient"
    INSTRUCTIONS = "recipeInstructions"
    PORTIONS = "recipeYield"
    TOTAL_TIME = "totalTime"
    CUISINE = "recipeCuisine"


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


class Nutrition(TypedDict):
    calories: str | None
    fat: str | None
    saturated_fat: str | None
    carbohydrate: str | None
    sugar: str | None
    fiber: str | None
    protein: str | None
    sodium: str | None
