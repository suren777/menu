from sqlalchemy import JSON, Boolean, Column, Float, Integer, String, Text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

from menu.db.engine import engine


class Base(DeclarativeBase):
    pass


class MyMixin:
    id = Column(Integer, primary_key=True)


class Sitemap(MyMixin, Base):
    __tablename__ = "sitemap"
    url = Column(String, nullable=False)
    completed = Column(Boolean, default=False)


class RecipeUrls(MyMixin, Base):
    __tablename__ = "recipe_urls"
    url = Column(String, nullable=False)
    name = Column(String, nullable=False)
    data = Column(JSON, nullable=False)


class User(MyMixin, Base):
    __tablename__ = "users"
    telegram_id = Column(Integer, unique=True, nullable=False)
    premium = Column(Boolean, default=False)


class UserRecipe(MyMixin, Base):
    __tablename__ = "user_recipes"
    user_id = Column(Integer, nullable=False)
    recipe_id = Column(Integer, nullable=False)


class RecipeTable(MyMixin, Base):
    __tablename__ = "recipes"
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    image = Column(String)
    keywords = Column(Text)
    cook_time = Column(String)
    prep_time = Column(String)
    total_time = Column(String)
    category = Column(String)
    cuisine = Column(String)
    ingredients = Column(Text)
    instructions = Column(Text)
    portions = Column(String, nullable=True)
    calories = Column(Float())
    fat = Column(Float())
    saturated_fat = Column(Float())
    carbohydrate = Column(Float())
    sugar = Column(Float())
    fiber = Column(Float())
    protein = Column(Float())
    sodium = Column(Float())


def initialise(db_engine: Engine = engine):
    Base.metadata.create_all(db_engine)
