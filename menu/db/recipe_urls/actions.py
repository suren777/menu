from typing import Any

from menu.db.connection import get_session
from menu.db.recipe_urls import repository


def add_recipe(url: str, name: str, recipe_data: dict[str, Any]) -> None:
    with get_session() as session:
        repository.add_recipe(
            url=url, name=name, recipe_data=recipe_data, session=session
        )
