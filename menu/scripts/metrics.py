"""The metrics snapshot: the lab's state as one JSON document, so a
phase of the data-model plan diffs against the last snapshot instead
of trusting prose."""

import statistics
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select

from menu.db.database import (
    Base,
    Ingredient,
    IngredientTerm,
    RecipeIngredient,
    RecipeNutrition,
)
from menu.db.ingredients import repository
from menu.nutrition.reports import COMPARED, validation_comparisons

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def collect_metrics(session: Session) -> dict[str, Any]:
    """The numbers every phase must hold steady unless it says otherwise."""
    row_counts = {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
    grams_by_source = {
        (source if source is not None else "none"): count
        for source, count in session.execute(
            select(RecipeIngredient.grams_source, func.count())
            .where(RecipeIngredient.grams.is_not(None))
            .group_by(RecipeIngredient.grams_source)
        ).all()
    }
    total_lines = session.scalar(select(func.count()).select_from(RecipeIngredient))
    resolved_lines = session.scalar(
        select(func.count())
        .select_from(RecipeIngredient)
        .join(IngredientTerm, IngredientTerm.id == RecipeIngredient.term_id)
        .where(*repository.effective_ingredient_predicates())
    )
    # Lines with no effective ingredient: no term (including the
    # failed parses), a review/not_food term, or a line-level
    # review flag.
    unresolved_lines = (total_lines or 0) - (resolved_lines or 0)
    lines_parse_failed = session.scalar(
        select(func.count())
        .select_from(RecipeIngredient)
        .where(RecipeIngredient.parse_status == "failed")
    )
    review_queue_terms = session.scalar(
        select(func.count())
        .select_from(IngredientTerm)
        .where(IngredientTerm.resolution == "review")
    )
    review_queue_lines = session.scalar(
        select(func.count())
        .select_from(RecipeIngredient)
        .outerjoin(IngredientTerm, IngredientTerm.id == RecipeIngredient.term_id)
        .where(
            or_(
                and_(
                    RecipeIngredient.needs_review.is_(True),
                    IngredientTerm.resolution.is_distinct_from("not_food"),
                ),
                IngredientTerm.resolution == "review",
            )
        )
    )
    not_food_lines = session.scalar(
        select(func.count())
        .select_from(RecipeIngredient)
        .join(IngredientTerm, IngredientTerm.id == RecipeIngredient.term_id)
        .where(IngredientTerm.resolution == "not_food")
    )
    distinct_ingredients = session.scalar(
        select(func.count(func.distinct(Ingredient.name)))
    )
    coverages = [
        coverage
        for coverage in session.scalars(
            select(RecipeNutrition.coverage).where(
                RecipeNutrition.source == "derived",
                RecipeNutrition.coverage.is_not(None),
            )
        )
        if coverage is not None
    ]

    by_group: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
    for _recipe_id, site, sources, _name, errors in validation_comparisons(session):
        by_group[(site or "?", sources or "?")].append(errors)
    medians = {
        f"{site} via {sources}": {
            nutrient: statistics.median(values)
            for nutrient in COMPARED
            if (values := [e[nutrient] for e in group if nutrient in e])
        }
        for (site, sources), group in sorted(by_group.items())
    }

    return {
        "row_counts": row_counts,
        "lines_with_grams_by_source": grams_by_source,
        "lines_with_ingredient_id_null": unresolved_lines,
        "lines_parse_failed": lines_parse_failed,
        "review_queue_lines": review_queue_lines,
        "review_queue_terms": review_queue_terms,
        "not_food_lines": not_food_lines,
        "distinct_ingredients": distinct_ingredients,
        "mean_coverage": statistics.fmean(coverages) if coverages else None,
        "recipes_with_coverage_at_least_0_9": sum(c >= 0.9 for c in coverages),
        "median_validation_error": medians,
    }
