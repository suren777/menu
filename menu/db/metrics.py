"""The metrics snapshot: the lab's state as one JSON document, so a
phase of the data-model plan diffs against the last snapshot instead
of trusting prose."""

import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from menu.db.connection import get_session
from menu.db.database import (
    Base,
    Ingredient,
    RecipeIngredient,
    RecipeNutrition,
)
from menu.nutrition.reports import COMPARED, validation_comparisons


def collect_metrics() -> dict[str, Any]:
    """The numbers every phase must hold steady unless it says otherwise."""
    with get_session() as session:
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
        unresolved_lines = session.scalar(
            select(func.count())
            .select_from(RecipeIngredient)
            .where(RecipeIngredient.ingredient_id.is_(None))
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
    for _recipe_id, site, sources, _name, errors in validation_comparisons():
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
        "distinct_ingredients": distinct_ingredients,
        "mean_coverage": statistics.fmean(coverages) if coverages else None,
        "recipes_with_coverage_at_least_0_9": sum(c >= 0.9 for c in coverages),
        "median_validation_error": medians,
    }
