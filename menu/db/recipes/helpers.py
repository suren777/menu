import ast
from typing import TypedDict
from sqlalchemy import Column, ColumnExpressionArgument, func
from menu.db.connection import get_ro_session
from menu.db.database import RecipeTable


def parse_categories(dirty_categories: list[str]) -> list[str]:
    combined = (",".join(dirty_categories)).lower()
    return sorted(list(set([c.strip() for c in combined.split(",")])))


def get_categories() -> list[str]:
    with get_ro_session() as session:
        result = [
            c[0]
            for c in session.query(RecipeTable.category).distinct().all()
            if c[0] is not None
        ]
        return parse_categories(result)


def get_cuisines(category: str | None = None) -> list[str]:
    filters: list[ColumnExpressionArgument[bool]] = []
    if category is not None:
        filters.append(func.lower(RecipeTable.category).like(f"%{category.lower()}%"))

    with get_ro_session() as session:
        result = [
            c[0]
            for c in session.query(RecipeTable.cuisine)
            .filter(*filters)
            .distinct()
            .all()
            if c[0] is not None
        ]
        return result


def parse_literal_array(
    literal_array: Column[str] | None, enumerate_list: bool = False
) -> str:

    if literal_array is None:
        return ""
    ingredients_list: list[str] = ast.literal_eval(str(literal_array))
    if enumerate_list:
        ingredients_list = [
            f"{i+1}. {ingredient}" for i, ingredient in enumerate(ingredients_list)
        ]

    return "\n".join(ingredients_list)


def recipe_to_text(recipe: RecipeTable) -> str:
    result = f"<b>{str(recipe.name)}</b>\n\n"
    result += f"<i>{str(recipe.description)}</i>\n\n"
    result += "<b>Ingredients:</b>\n"
    result += parse_literal_array(recipe.ingredients) + "\n\n"
    result += "<b>Instructions:</b>\n"
    result += parse_literal_array(recipe.instructions, enumerate_list=True)
    return result


def create_recipe_filters(
    cuisine: str | None,
    category: str | None,
    ingredient: str | None = None,
) -> list[ColumnExpressionArgument[bool]]:
    filters: list[ColumnExpressionArgument[bool]] = []
    if cuisine is not None:
        filters.append(RecipeTable.cuisine == cuisine)
    if category is not None:
        filters.append(func.lower(RecipeTable.category).like(f"%{category.lower()}%"))
    if ingredient is not None:
        filters.append(
            func.lower(RecipeTable.ingredients).like(f"% {ingredient.lower()}%")
        )
    return filters


class RecipeNames(TypedDict):
    name: str
    id: int


def get_recipe_names(
    cuisine: str | None, category: str | None, ingredient: str | None = None
) -> list[RecipeNames]:
    filters = create_recipe_filters(cuisine, category, ingredient)
    with get_ro_session() as session:
        result = [
            RecipeNames(name=str(recipe.name), id=int(recipe.id))
            for recipe in session.query(RecipeTable.name, RecipeTable.id).filter(
                *filters
            )
        ]
    return result


def get_recipe_by_id(recipe_id: int | None) -> str | None:
    if recipe_id is None:
        return None
    with get_ro_session() as session:
        result = session.query(RecipeTable).filter(RecipeTable.id == recipe_id).first()
        if result is None:
            return None
        return recipe_to_text(result)


def search_recipe_by_name(recipe_name: str) -> list[RecipeNames]:
    with get_ro_session() as session:
        result = [
            RecipeNames(name=str(recipe.name), id=int(recipe.id))
            for recipe in session.query(RecipeTable.name, RecipeTable.id).filter(
                func.lower(RecipeTable.name).like(f"%{recipe_name.lower()}%")
            )
        ]
    return result


def get_recipes(
    cuisine: str | None,
    category: str | None,
    ingredient: str | None = None,
    max_recipes: int = 3,
) -> list[str]:
    filters: list[ColumnExpressionArgument[bool]] = []
    if cuisine is not None:
        filters.append(RecipeTable.cuisine == cuisine)
    if category is not None:
        filters.append(func.lower(RecipeTable.category).like(f"%{category.lower()}%"))
    if ingredient is not None:
        filters.append(
            func.lower(RecipeTable.ingredients).like(f"% {ingredient.lower()}%")
        )

    with get_ro_session() as session:
        result = [
            recipe_to_text(recipe)
            for recipe in session.query(RecipeTable)
            .filter(*filters)
            .limit(max_recipes)
            .all()
        ]
    return result
