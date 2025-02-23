from dataclasses import dataclass
from menu.crawlers.bbc_good_food.common import Nutrition
from menu.db.database import RecipeTable
from sqlalchemy.orm import Session
import json


@dataclass
class RecipeModel:
    id: int
    name: str
    description: str
    image: str
    keywords: list[str]
    cook_time: str
    prep_time: str
    total_time: str
    category: str
    calories: str | None
    fat: str | None
    saturated_fat: str | None
    carbohydrate: str | None
    sugar: str | None
    fiber: str | None
    protein: str | None
    sodium: str | None
    ingredients: list[str]
    instructions: list[str]
    portions: int
    cuisine: str


class RecipesRepository:
    @staticmethod
    def from_record(record: RecipeTable) -> RecipeModel:
        return RecipeModel(
            id=record.id,
            name=record.name,
            description=record.description,
            image=record.image,
            keywords=json.loads(record.keywords),
            cook_time=record.cook_time,
            prep_time=record.prep_time,
            total_time=record.total_time,
            category=record.category,
            nutrition=record.nutrition,
            ingredients=json.loads(record.ingredients),
            instructions=json.loads(record.instructions),
            portions=record.portions,
            cuisine=record.cuisine,
            calories=record.calories,
            fat=record.fat,
            saturated_fat=record.saturated_fat,
            carbohydrate=record.carbohydrate,
            sugar=record.sugar,
            fiber=record.fiber,
            protein=record.protein,
            sodium=record.sodium,
        )

    @staticmethod
    def to_record(entity: RecipeModel) -> RecipeTable:
        return RecipeTable(
            id=entity.id,
            name=entity.name,
            description=entity.description,
            image=entity.image,
            keywords=json.dumps(entity.keywords),
            cook_time=entity.cook_time,
            prep_time=entity.prep_time,
            total_time=entity.total_time,
            category=entity.category,
            ingredients=json.dumps(entity.ingredients),
            instructions=json.dumps(entity.instructions),
            portions=entity.portions,
            cuisine=entity.cuisine,
            calories=entity.calories,
            fat=entity.fat,
            saturated_fat=entity.saturated_fat,
            carbohydrate=entity.carbohydrate,
            sugar=entity.sugar,
            fiber=entity.fiber,
            protein=entity.protein,
            sodium=entity.sodium,
        )

    @staticmethod
    def add_recipe(recipe: RecipeModel, session: Session):
        session.add(RecipesRepository.to_record(recipe))
