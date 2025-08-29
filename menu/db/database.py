from sqlalchemy import Boolean, Column, Integer, String, JSON, Float, Text
from menu.db.engine import engine
from sqlalchemy.orm import DeclarativeBase


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
    portions = Column(Integer)
    calories = Column(Float())
    fat = Column(Float())
    saturated_fat = Column(Float())
    carbohydrate = Column(Float())
    sugar = Column(Float())
    fiber = Column(Float())
    protein = Column(Float())
    sodium = Column(Float())


def initialise():
    Base.metadata.create_all(engine)
