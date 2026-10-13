"""The metrics snapshot counts what the data-model plan's phases diff
against."""

from typing import TYPE_CHECKING

from menu.db.database import (
    DerivationRun,
    Ingredient,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrient,
    RecipeNutritionRun,
    RecipePublishedNutrient,
)
from menu.scripts.metrics import collect_metrics

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
        ]
    )
    # Coverages and validation medians come from the latest run's rows.
    run = DerivationRun(
        kind="nutrition", parser_version="test", seed_digest="d", sources={}
    )
    session.add(run)
    session.flush()
    session.add(
        RecipeNutritionRun(
            run_id=run.id,
            recipe_id=recipe.id,
            coverage=0.95,
            unconverted_lines=0,
            sources=["fdc"],
            definitions_mixed=False,
        )
    )
    session.flush()
    session.add(
        RecipeNutrient(
            run_id=run.id,
            recipe_id=recipe.id,
            nutrient="energy_kcal",
            amount=250.0,
            definitions=["kcal"],
            sources=["fdc"],
        )
    )
    session.add(
        RecipePublishedNutrient(
            recipe_id=recipe.id,
            nutrient="energy_kcal",
            amount=100.0,
            raw_text="100 calories",
        )
    )
    # collect_metrics reads through the caller's session: the fixture
    # rows are visible uncommitted.
    metrics = collect_metrics(session)

    assert metrics["row_counts"]["pages"] == 1
    assert metrics["row_counts"]["recipes"] == 1
    assert metrics["row_counts"]["derivation_runs"] == 1
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
