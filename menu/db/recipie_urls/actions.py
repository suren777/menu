from typing import Any
from menu.db.connection import get_session
from menu.db.database import RecipeUrls
from menu.db.recipie_urls.repository import RecipeUrlsRepository


def add_recipe(url: str, name: str, recipe_data: dict[str, Any]):
    with get_session() as session:
        RecipeUrlsRepository.add_recipe(
            url=url, name=name, recipe_data=recipe_data, session=session
        )
