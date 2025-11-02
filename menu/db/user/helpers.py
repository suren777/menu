"""This module contains helpers for interacting with the user table."""
from sqlalchemy.orm import Session
from menu.db.database import User
from menu.db.engine import engine

from menu.db.database import UserRecipe


def get_or_create_user(telegram_id: int) -> User:
    """Gets or creates a user in the database."""
    with Session(engine) as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if user is None:
            user = User(telegram_id=telegram_id)
            session.add(user)
            session.commit()
            session.refresh(user)
        return user


from menu.db.database import RecipeTable


def add_recipe_to_favorites(user_id: int, recipe_id: int):
    """Adds a recipe to a user's favorites."""
    with Session(engine) as session:
        user_recipe = UserRecipe(user_id=user_id, recipe_id=recipe_id)
        session.add(user_recipe)
        session.commit()


def get_favorite_recipes(user_id: int) -> list[RecipeTable]:
    """Gets a user's favorite recipes."""
    with Session(engine) as session:
        recipe_ids = (
            session.query(UserRecipe.recipe_id)
            .filter_by(user_id=user_id)
            .all()
        )
        if not recipe_ids:
            return []
        return (
            session.query(RecipeTable)
            .filter(RecipeTable.id.in_([r[0] for r in recipe_ids]))
            .all()
        )
