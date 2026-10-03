from sqlalchemy import JSON, Boolean, Column, Integer, String
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


def initialise(db_engine: Engine = engine) -> None:
    Base.metadata.create_all(db_engine)
