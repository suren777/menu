"""Reports: hand-seeding candidates from the reference imports —
a report, never an auto-merge."""

from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    RecipeIngredient,
    RefFood,
)
from menu.db.ingredients import repository
from menu.ingest import review_inputs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from menu.ingest.embeddings import Embedder


def fdc_id_conflicts(session: Session) -> list[tuple[str, list[str]]]:
    """Canonical ingredients sharing an unconfirmed candidate
    reference food with no seed term linking them, as candidates to
    hand-seed into seed.FOOD_REFS — a report, never an auto-merge.
    Shared USDA ids catch real variants (milk / warm milk) but also
    lump distinct ingredients (almond / vanilla / rosewater)."""
    names = {record.id: record.name for record in session.scalars(select(Ingredient))}
    links = {
        (term.term, term.ingredient_id)
        for term in session.scalars(select(IngredientTerm))
        if term.resolution == "seed" and term.ingredient_id is not None
    }
    candidates = session.execute(
        select(
            IngredientFoodRef.ref_food_id,
            IngredientFoodRef.ingredient_id,
        ).where(IngredientFoodRef.status == "candidate")
    ).all()
    descriptions = dict(session.execute(select(RefFood.id, RefFood.description)).all())

    by_food: dict[int, list[int]] = {}
    for ref_food_id, ingredient_id in candidates:
        by_food.setdefault(ref_food_id, []).append(ingredient_id)

    conflicts = []
    for ref_food_id, group in sorted(by_food.items()):
        if len(group) < 2:
            continue
        # An alias row pointing one member's name at another member
        # means the pair is already linked by a curated seed.
        if any(
            (names[other], member) in links
            for member in group
            for other in group
            if member != other
        ):
            continue
        label = descriptions.get(ref_food_id) or f"ref_food {ref_food_id}"
        conflicts.append((label, sorted(names[member] for member in group)))
    return conflicts


def ref_review(
    session: Session, limit: int = 100, *, embedder: Embedder | None = None
) -> list[tuple[int, str, str | None, tuple[str, ...], tuple[str, ...]]]:
    """Top ingredients without a confirmed reference by line count,
    with the parser's candidate and the best text matches from each
    imported source, for hand-seeding into seed.FOOD_REFS. Candidates
    a rejected food_ref suggestion turned down — and their foods as
    text matches — stay out. Needs the reference import; a report, never an
    auto-merge. Lines count through their terms' effective ingredient
    (repository.effective_ingredient_predicates). With an embedder, a
    fifth element carries the embedding neighbours per row — the top 3
    reference foods per source (review_inputs.ref_neighbours), whose
    similarity fills the dialect gaps the all-words text match can't."""
    rows = session.execute(
        select(func.count(RecipeIngredient.id), Ingredient.id, Ingredient.name)
        .join(IngredientTerm, IngredientTerm.ingredient_id == Ingredient.id)
        .join(RecipeIngredient, RecipeIngredient.term_id == IngredientTerm.id)
        .outerjoin(
            IngredientFoodRef,
            and_(
                IngredientFoodRef.ingredient_id == Ingredient.id,
                IngredientFoodRef.status == "confirmed",
            ),
        )
        .where(
            IngredientFoodRef.id.is_(None),
            *repository.effective_ingredient_predicates(),
        )
        .group_by(Ingredient.id)
        .order_by(func.count(RecipeIngredient.id).desc())
        .limit(limit)
    ).all()
    rejected: dict[int, set[int]] = {}
    for owner_id, food_id in session.execute(
        select(IngredientFoodRef.ingredient_id, IngredientFoodRef.ref_food_id).where(
            IngredientFoodRef.status == "rejected"
        )
    ).all():
        rejected.setdefault(owner_id, set()).add(food_id)
    sources = list(session.scalars(select(FoodSource)))
    descriptions = {
        source.id: session.execute(
            select(RefFood.id, RefFood.source_food_id, RefFood.description).where(
                RefFood.source_id == source.id
            )
        ).all()
        for source in sources
    }
    neighbours = (
        review_inputs.ref_neighbours(
            session,
            [name for _count, _ingredient_id, name in rows],
            embedder=embedder,
            per_source=3,
        )
        if embedder is not None
        else {}
    )
    results = []
    for count, ingredient_id, name in rows:
        candidate_food = session.scalar(
            select(IngredientFoodRef.ref_food_id).where(
                IngredientFoodRef.ingredient_id == ingredient_id,
                IngredientFoodRef.status == "candidate",
            )
        )
        candidate = (
            session.scalar(
                select(RefFood.description).where(RefFood.id == candidate_food)
            )
            if candidate_food is not None
            else None
        )
        # Text match: descriptions containing every word of the
        # name, one candidate per source. A food this ingredient's
        # rejected suggestion turned down is not offered again.
        words = name.lower().split()
        rejected_foods = rejected.get(ingredient_id, set())
        matches = []
        for source in sources:
            for ref_food_id, source_food_id, description in descriptions[source.id]:
                if ref_food_id in rejected_foods:
                    continue
                text = description.lower()
                if all(word in text for word in words):
                    matches.append(f"{source.name} {source_food_id}: {description}")
                    break
        embedding = tuple(
            f"{source} {source_food_id}: {description} ({score:.2f})"
            for source, source_food_id, description, score in neighbours.get(name, [])
        )
        results.append((count, name, candidate, tuple(matches), embedding))
    return results
