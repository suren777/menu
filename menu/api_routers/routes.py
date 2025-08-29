from fastapi import APIRouter

from menu.api_routers.data_types import RecipeSearchRequest
from menu.db.recipes.helpers import get_categories, get_cuisines, get_recipes

router = APIRouter()


@router.get("/healthcheck", tags=["healthcheck"])
async def healthcheck() -> dict[str, str]:
    return {
        "service": "menu",
        "environment": "dev",
    }


@router.get("/categories", tags=["recipe_categories"])
async def recipe_categories() -> list[str]:
    return get_categories()


@router.get("/cuisines", tags=["recipe_cuisines"])
async def recipe_cuisines() -> list[str]:
    return get_cuisines()


@router.post("/get-recipe", tags=["get_recipe"])
async def get_recipe(request: RecipeSearchRequest) -> list[str]:
    return get_recipes(
        cuisine=request.cuisine,
        category=request.category,
        ingredient=request.ingredient,
    )
