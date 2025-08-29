from pydantic import BaseModel


class RecipeSearchRequest(BaseModel):
    cuisine: str | None = None
    category: str | None = None
    ingredient: str | None = None
