"""The metrics snapshot counts what the data-model plan's phases diff
against."""

from typing import TYPE_CHECKING

from menu.db.database import (
    Ingredient,
    RecipeIngredient,
    RecipeNutrition,
    RecipeUrls,
)
from menu.db.metrics import collect_metrics

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_collect_metrics_snapshot(session: Session) -> None:
    recipe = RecipeUrls(
        url="https://x/1",
        name="Test",
        data={},
        site="bbc_good_food",
        servings=2,
    )
    ingredient = Ingredient(name="milk")
    session.add_all([recipe, ingredient])
    session.flush()
    session.add_all(
        [
            RecipeIngredient(
                recipe_id=recipe.id,
                position=0,
                raw_text="100 ml milk",
                ingredient_id=ingredient.id,
                grams=103.0,
                grams_source="seed",
            ),
            RecipeNutrition(recipe_id=recipe.id, source="derived", coverage=0.95),
            RecipeNutrition(
                recipe_id=recipe.id, source="published", energy_kcal=100.0
            ),
        ]
    )
    # collect_metrics opens its own session, so the fixture rows must
    # be committed first.
    session.commit()

    metrics = collect_metrics()

    assert metrics["row_counts"]["recipe_urls"] == 1
    assert metrics["lines_with_grams_by_source"] == {"seed": 1}
    assert metrics["lines_with_ingredient_id_null"] == 0
    assert metrics["distinct_ingredients"] == 1
    assert metrics["mean_coverage"] == 0.95
    assert metrics["recipes_with_coverage_at_least_0_9"] == 1
    assert metrics["median_validation_error"] == {}
