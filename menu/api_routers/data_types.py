"""Data types for the API routers."""
from pydantic import BaseModel


class RecipeSearchRequest(BaseModel):  # pylint: disable=too-few-public-methods
    """Request model for searching recipes."""

    cuisine: str | None = None
    category: str | None = None
    ingredient: str | None = None
