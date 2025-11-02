import json
from dataclasses import dataclass

from sqlalchemy import ColumnExpressionArgument
from sqlalchemy.orm import Session

from menu.db.connection import get_ro_session
from menu.db.database import RecipeTable


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
            id=int(record.id),
            name=str(record.name),
            description=str(record.description),
            image=str(record.image),
            keywords=json.loads(str(record.keywords)),
            cook_time=str(record.cook_time),
            prep_time=str(record.prep_time),
            total_time=str(record.total_time),
            category=str(record.category),
            ingredients=json.loads(str(record.ingredients)),
            instructions=json.loads(str(record.instructions)),
            portions=int(record.portions),
            cuisine=str(record.cuisine),
            calories=str(record.calories) if record.calories is not None else None,
            fat=str(record.fat) if record.fat is not None else None,
            saturated_fat=(
                str(record.saturated_fat) if record.saturated_fat is not None else None
            ),
            carbohydrate=(
                str(record.carbohydrate) if record.carbohydrate is not None else None
            ),
            sugar=str(record.sugar) if record.sugar is not None else None,
            fiber=str(record.fiber) if record.fiber is not None else None,
            protein=str(record.protein) if record.protein is not None else None,
            sodium=str(record.sodium) if record.sodium is not None else None,
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
    def add_recipe(recipe: RecipeModel, session: Session) -> None:
        session.add(RecipesRepository.to_record(recipe))
