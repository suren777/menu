"""The metrics snapshot counts what the data-model plan's phases diff
against."""

from typing import TYPE_CHECKING

from menu.db.database import (
    Ingredient,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrition,
)
from menu.db.metrics import collect_metrics

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_collect_metrics_snapshot(session: Session) -> None:
    page = Page(url="https://x/1", site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id,
        name="Test",
        data={},
        data_sha256="x",
        extractor="json_ld",
        servings=2,
    )
    ingredient = Ingredient(name="milk")
    session.add_all([recipe, ingredient])
    session.flush()
    resolved_term = IngredientTerm(
        term="milk", ingredient_id=ingredient.id, resolution="seed"
    )
    review_term = IngredientTerm(term="mystery powder", resolution="review")
    session.add_all([resolved_term, review_term])
    session.flush()
    session.add_all(
        [
            RecipeIngredient(
                recipe_id=recipe.id,
                position=0,
                raw_text="100 ml milk",
                term_id=resolved_term.id,
                grams=103.0,
                grams_source="seed",
            ),
            # Unresolved twice over: a review term, and a failed parse.
            RecipeIngredient(
                recipe_id=recipe.id,
                position=1,
                raw_text="mystery powder",
                term_id=review_term.id,
            ),
            RecipeIngredient(
                recipe_id=recipe.id,
                position=2,
                raw_text="unparseable line",
                parse_status="failed",
            ),
            RecipeNutrition(
                recipe_id=recipe.id,
                source="derived",
                energy_kcal=250.0,
                coverage=0.95,
                sources="fdc",
            ),
            RecipeNutrition(
                recipe_id=recipe.id, source="published", energy_kcal=100.0
            ),
        ]
    )
    # collect_metrics opens its own session, so the fixture rows must
    # be committed first.
    session.commit()

    metrics = collect_metrics()

    assert metrics["row_counts"]["pages"] == 1
    assert metrics["row_counts"]["recipes"] == 1
    assert metrics["lines_with_grams_by_source"] == {"seed": 1}
    assert metrics["lines_with_ingredient_id_null"] == 2
    assert metrics["lines_parse_failed"] == 1
    assert metrics["review_queue_lines"] == 1
    assert metrics["review_queue_terms"] == 1
    assert metrics["not_food_lines"] == 0
    assert metrics["distinct_ingredients"] == 1
    assert metrics["mean_coverage"] == 0.95
    assert metrics["recipes_with_coverage_at_least_0_9"] == 1
    assert metrics["median_validation_error"] == {
        "bbc_good_food via fdc": {"energy_kcal": 0.25}
    }
