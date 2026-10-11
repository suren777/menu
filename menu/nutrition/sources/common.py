"""Shared machinery for the reference-data importers.

fdc, cofid and cnf all slice their release into the same ref_* tables.
upsert_foods is the food half every importer needs: ref_food rows are
upserted on (source_id, source_food_id) so a re-import keeps ref_food.id
stable — a confirmed ingredient_food_ref mapping never silently points
at a different food — while foods the release dropped are removed
unless a mapping still references them.
"""

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from menu.db.database import IngredientFoodRef, RefFood

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CHUNK = 10_000


def upsert_foods(
    session: Session, source_id: int, foods: list[dict[str, Any]]
) -> tuple[dict[str, int], int]:
    """Upsert the release's food rows on (source_id, source_food_id),
    keeping existing ids and refreshing description/category/refuse_pct,
    then drop the source's foods missing from the release — except
    those an ingredient_food_ref row references: those are kept and
    warned about. Returns the source's ref_food ids by source_food_id
    (after the upsert) and the count of kept stale foods."""
    stmt = sqlite_insert(RefFood)
    upsert = stmt.on_conflict_do_update(
        index_elements=[RefFood.source_id, RefFood.source_food_id],
        set_={
            "description": stmt.excluded.description,
            "category": stmt.excluded.category,
            "refuse_pct": stmt.excluded.refuse_pct,
        },
    )
    # executemany, not one multi-row VALUES: a release's foods x columns
    # would overflow SQLite's bound-parameter limit.
    for start in range(0, len(foods), CHUNK):
        session.execute(upsert, foods[start : start + CHUNK])
    ref_ids = dict(
        session.execute(
            select(RefFood.source_food_id, RefFood.id).where(
                RefFood.source_id == source_id
            )
        ).all()
    )
    released = {row["source_food_id"] for row in foods}
    stale = [
        (food_id, source_food_id)
        for source_food_id, food_id in ref_ids.items()
        if source_food_id not in released
    ]
    referenced = set(
        session.scalars(
            select(IngredientFoodRef.ref_food_id).where(
                IngredientFoodRef.ref_food_id.in_([food_id for food_id, _ in stale])
            )
        ).all()
    )
    unreferenced: list[int] = []
    for food_id, source_food_id in stale:
        if food_id in referenced:
            logger.warning(
                "ref_food %s is missing from the release but still "
                "referenced by ingredient_food_ref; keeping it",
                source_food_id,
            )
        else:
            unreferenced.append(food_id)
    if unreferenced:
        session.execute(delete(RefFood).where(RefFood.id.in_(unreferenced)))
    return ref_ids, len(stale) - len(unreferenced)
