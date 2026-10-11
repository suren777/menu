"""Re-resolution: re-applying the seeds and review heuristics to the
terms, in one transaction, with no parser."""

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from menu.db.connection import get_session
from menu.db.database import (
    Ingredient,
    IngredientConversion,
    IngredientFoodRef,
    IngredientTerm,
    RecipeIngredient,
)
from menu.db.ingredients import repository, seed
from menu.db.ingredients.grams import ingredient_conversions, to_grams
from menu.db.ingredients.seed import seed_ingredient_data
from menu.ingest.ingredients import name_needs_review

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def prune_orphan_ingredients(session: Session) -> int:
    """Delete `auto` terms no line uses, then `auto` concepts no term
    points at and no curated reference (confirmed or rejected — a
    decision in seed.py) keeps: a concept's parser candidate rows go
    with it. Seed concepts are never pruned. Used after a re-resolve
    or reparse, which re-resolves lines through the seeded terms and
    strands the junk auto concepts the first pass created. Returns the
    number of concepts pruned."""
    used = set(session.scalars(select(RecipeIngredient.term_id)))
    unused_terms = [
        term
        for term in session.scalars(
            select(IngredientTerm).where(IngredientTerm.resolution == "auto")
        )
        if term.id not in used
    ]
    for term in unused_terms:
        session.delete(term)
    session.flush()  # the concepts lose their pointers before the count

    curated = set(
        session.scalars(
            select(IngredientFoodRef.ingredient_id).where(
                IngredientFoodRef.status != "candidate"
            )
        )
    )
    pointed = set(
        session.scalars(
            select(IngredientTerm.ingredient_id).where(
                IngredientTerm.ingredient_id.is_not(None)
            )
        )
    )
    orphans = [
        ingredient
        for ingredient in session.scalars(
            select(Ingredient).where(Ingredient.origin == "auto")
        )
        if ingredient.id not in curated and ingredient.id not in pointed
    ]
    for ingredient in orphans:
        session.execute(
            delete(IngredientFoodRef).where(
                IngredientFoodRef.ingredient_id == ingredient.id
            )
        )
        session.delete(ingredient)
    return len(orphans)


def _recompute_terms(session: Session) -> int:
    """Re-apply the review heuristic to every non-seed term: a
    name_needs_review term goes back to 'review', everything else to
    'auto' pointing at the concept of the same name (created when
    missing). Seed and not_food terms still in seed.py are curated
    decisions and stand; one whose entry was removed from seed.py is
    recomputed like any other term. Run after seeding. Returns the
    number of terms changed."""
    curated = {alias for alias, _, _ in seed.ALIASES} | set(seed.NOT_FOOD)
    curated |= set(
        session.scalars(select(Ingredient.name).where(Ingredient.origin == "seed"))
    )
    changed = 0
    terms = [
        term
        for term in session.scalars(select(IngredientTerm))
        if term.resolution in ("auto", "review") or term.term not in curated
    ]
    for term in terms:
        if name_needs_review(term.term):
            resolution, ingredient_id = "review", None
        else:
            ingredient = session.scalar(
                select(Ingredient).where(Ingredient.name == term.term)
            )
            if ingredient is None:
                ingredient = Ingredient(name=term.term, origin="auto")
                session.add(ingredient)
                session.flush()
            resolution, ingredient_id = "auto", ingredient.id
        if (term.resolution, term.ingredient_id) != (resolution, ingredient_id):
            term.resolution = resolution
            term.ingredient_id = ingredient_id
            term.variant = None
            changed += 1
    return changed


def re_resolve() -> tuple[int, int, int]:
    """Re-apply the seeds and review heuristics to the terms, in one
    transaction, with no parser: a seed change reaches every line by
    re-pointing its term. Every line's grams are recomputed from the
    stored columns — a seed change can move grams without moving the
    ingredient (a conversion row, a confirmed conversion reference) —
    with the conversion rows read once per ingredient. Orphans are
    pruned last. Returns (terms changed, lines regrammed, concepts
    pruned)."""
    with get_session() as session:
        seed_ingredient_data(session)
        terms_changed = _recompute_terms(session)
        session.flush()
        inputs: dict[int, list[IngredientConversion]] = {}
        regrammed = 0
        for line, term in session.execute(repository.lines_with_terms_select()):
            effective = repository.effective_ingredient_id(line.needs_review, term)
            grams: tuple[float | None, float | None, str | None, int | None] = (
                None,
                None,
                None,
                None,
            )
            if effective is not None:
                if effective not in inputs:
                    inputs[effective] = ingredient_conversions(effective, session)
                grams = to_grams(
                    line.quantity,
                    line.quantity_max,
                    line.dimension,
                    line.base_unit,
                    line.original_unit,
                    line.preparation,
                    line.size,
                    inputs[effective],
                )
            if grams != (
                line.grams,
                line.grams_max,
                line.grams_source,
                line.conversion_id,
            ):
                (
                    line.grams,
                    line.grams_max,
                    line.grams_source,
                    line.conversion_id,
                ) = grams
                regrammed += 1
        pruned = prune_orphan_ingredients(session)
    return terms_changed, regrammed, pruned
