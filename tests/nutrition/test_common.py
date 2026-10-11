"""The shared ref_food upsert, at a release's real size."""

from typing import TYPE_CHECKING

from menu.db.database import FoodSource
from menu.nutrition.sources.common import upsert_foods

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_upsert_handles_a_full_release(session: Session) -> None:
    source = FoodSource(name="test", version="1", licence="CC0", citation="test")
    session.add(source)
    session.flush()
    foods = [
        {
            "source_id": source.id,
            "source_food_id": str(n),
            "description": f"food {n}",
            "category": None,
            "refuse_pct": None,
        }
        for n in range(12_000)
    ]

    ids, kept = upsert_foods(session, source.id, foods)
    again, _ = upsert_foods(session, source.id, foods)

    assert len(ids) == 12_000
    assert again == ids
    assert kept == 0
