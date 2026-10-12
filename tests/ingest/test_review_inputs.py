"""Review input sets built from a hand-made DB with fixed vectors:
no model download, no network."""

import hashlib
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from sqlalchemy import select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RefFood,
)
from menu.domain.review import FoodCandidate
from menu.ingest import review_inputs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def add_recipe_row(session: Session, url: str, name: str) -> Recipe:
    """A bare recipe row, in the caller's transaction (inlined from the
    ingredient-test helpers: tests/ingest is not a package)."""
    page = Page(url=url, site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id, name=name, data={}, data_sha256=url, extractor="json_ld"
    )
    session.add(recipe)
    session.flush()
    return recipe


DIM = 8


def _axis(index: int, weight: float = 1.0, spill: int | None = None) -> list[float]:
    vector = [0.0] * DIM
    vector[index] = weight
    if spill is not None:
        vector[spill] = (1 - weight**2) ** 0.5
    return vector


VECTORS: dict[str, list[float]] = {
    "milk": _axis(0),
    "whole milk": _axis(0, 0.95, spill=1),
    "aubergine": _axis(2),
    "eggplant": _axis(2, 0.9, spill=3),
    "cream": _axis(4),
    "Eggplant, raw": _axis(2, 0.98, spill=3),
    "Milk, whole": _axis(0, 0.97, spill=1),
    "Cream, heavy": _axis(4, 0.9, spill=5),
}


class KeyedEmbedder:
    """The vector for the text's name part (before " ("); other texts
    get a deterministic hash vector, far from everything above."""

    model_id = "fake/keyed"

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = []
        for text in texts:
            key = text.split(" (")[0]
            if key in VECTORS:
                rows.append(VECTORS[key])
            else:
                seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4])
                rows.append(list(np.random.default_rng(seed).standard_normal(DIM)))
        return np.array(rows, dtype=np.float32)


def _concept(session: Session, name: str, origin: str) -> Ingredient:
    concept = Ingredient(name=name, origin=origin)
    session.add(concept)
    session.flush()
    session.add(
        IngredientTerm(
            term=name,
            ingredient_id=concept.id,
            resolution="seed" if origin == "seed" else "auto",
        )
    )
    session.flush()
    return concept


def _lines(session: Session, term: str, *raws: str) -> None:
    recipe = add_recipe_row(session, f"https://x/{term}/{len(raws)}", term)
    term_id = session.scalar(
        select(IngredientTerm.id).where(IngredientTerm.term == term)
    )
    for position, raw in enumerate(raws):
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id, position=position, raw_text=raw, term_id=term_id
            )
        )
    session.flush()


@pytest.fixture
def kitchen(session: Session) -> Session:
    _concept(session, "milk", "seed")
    _concept(session, "whole milk", "auto")
    _concept(session, "aubergine", "auto")
    _concept(session, "eggplant", "seed")
    _concept(session, "cream", "seed")
    session.add(IngredientTerm(term="pink and yellow gel", resolution="review"))
    session.flush()
    _lines(session, "milk", "200ml milk", "200ml milk")
    _lines(session, "whole milk", "100ml whole milk")
    _lines(session, "aubergine", "1 aubergine", "2 aubergines")
    _lines(session, "cream", "cream")
    _lines(
        session,
        "pink and yellow gel",
        "1 tsp pink and yellow gel",
        "1 tsp pink and yellow gel",
        "pink and yellow gel",
    )
    return session


def test_concept_text_carries_the_line_when_there_is_one() -> None:
    assert review_inputs.concept_text("milk", "200ml milk") == "milk (200ml milk)"
    assert review_inputs.concept_text("milk", None) == "milk"


def test_queue_items_carry_lines_most_frequent_first(kitchen: Session) -> None:
    (item,) = review_inputs.queue_items(kitchen, embedder=KeyedEmbedder())
    assert item.input == "queue"
    assert item.subject == "pink and yellow gel"
    assert item.lines == ("1 tsp pink and yellow gel", "pink and yellow gel")
    assert set(item.known_concepts) == {
        "aubergine",
        "cream",
        "eggplant",
        "milk",
        "whole milk",
    }


def test_audit_items_are_single_use_auto_concepts(kitchen: Session) -> None:
    (item,) = review_inputs.audit_items(kitchen, embedder=KeyedEmbedder())
    assert (item.input, item.subject, item.lines) == (
        "audit",
        "whole milk",
        ("100ml whole milk",),
    )
    assert item.known_concepts[0] == "milk"
    assert "whole milk" not in item.known_concepts


def test_pair_items_put_the_auto_concept_first_by_line_count(
    kitchen: Session,
) -> None:
    pairs = review_inputs.pair_items(kitchen, embedder=KeyedEmbedder())
    assert [item.subject for item in pairs.items] == [
        "aubergine | eggplant",
        "whole milk | milk",
    ]
    assert pairs.items[0].known_concepts == ("eggplant",)
    assert pairs.similarity["whole milk | milk"] == pytest.approx(0.95, abs=1e-3)


def _subjects(session: Session) -> list[str]:
    pairs = review_inputs.pair_items(session, embedder=KeyedEmbedder())
    return [item.subject for item in pairs.items]


def test_pair_items_skip_pairs_a_term_already_joins(kitchen: Session) -> None:
    term = _one(kitchen, IngredientTerm, IngredientTerm.term == "aubergine")
    term.ingredient_id = _one(kitchen, Ingredient, Ingredient.name == "eggplant").id
    kitchen.flush()
    assert "aubergine | eggplant" not in _subjects(kitchen)


def test_pair_items_skip_two_seed_concepts(
    kitchen: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(VECTORS, "cream", _axis(0, 0.9, spill=6))
    subjects = _subjects(kitchen)
    assert "whole milk | cream" in subjects
    assert not {"milk | cream", "cream | milk"} & set(subjects)


@pytest.fixture
def refs(kitchen: Session) -> Session:
    fdc = FoodSource(name="fdc", version="1", licence="public domain", citation="FDC")
    kitchen.add(fdc)
    kitchen.flush()
    for food_id, description in (
        ("1", "Eggplant, raw"),
        ("2", "Milk, whole"),
        ("3", "Cream, heavy"),
    ):
        kitchen.add(
            RefFood(source_id=fdc.id, source_food_id=food_id, description=description)
        )
    kitchen.flush()
    return kitchen


def _one[T](session: Session, model: type[T], criterion: Any) -> T:
    return session.scalars(select(model).where(criterion)).one()


def _food(session: Session, source_food_id: str) -> RefFood:
    return _one(session, RefFood, RefFood.source_food_id == source_food_id)


def test_food_ref_items_rank_candidates_by_similarity(refs: Session) -> None:
    items = review_inputs.food_ref_items(refs, embedder=KeyedEmbedder(), per_source=1)
    by_subject = {item.subject: item for item in items}
    assert [item.subject for item in items] == [
        "aubergine",
        "milk",
        "cream",
        "whole milk",
    ]
    assert by_subject["aubergine"].candidates == (
        FoodCandidate("fdc", "1", "Eggplant, raw"),
    )
    assert by_subject["whole milk"].candidates[0].source_food_id == "2"


def test_food_ref_items_skip_confirmed_and_rejected(refs: Session) -> None:
    milk = _one(refs, Ingredient, Ingredient.name == "milk")
    aubergine = _one(refs, Ingredient, Ingredient.name == "aubergine")
    for owner, food, status, role in (
        (milk, _food(refs, "2"), "confirmed", "nutrition"),
        (aubergine, _food(refs, "1"), "rejected", None),
    ):
        refs.add(
            IngredientFoodRef(
                ingredient_id=owner.id,
                ref_food_id=food.id,
                source_id=food.source_id,
                role=role,
                status=status,
                origin="seed",
            )
        )
    refs.flush()
    items = review_inputs.food_ref_items(refs, embedder=KeyedEmbedder(), per_source=1)
    by_subject = {item.subject: item for item in items}
    assert "milk" not in by_subject
    assert by_subject["aubergine"].candidates[0].source_food_id != "1"


def test_builders_are_deterministic(refs: Session) -> None:
    embedder = KeyedEmbedder()
    first = [
        review_inputs.queue_items(refs, embedder=embedder),
        review_inputs.audit_items(refs, embedder=embedder),
        review_inputs.pair_items(refs, embedder=embedder).items,
        review_inputs.food_ref_items(refs, embedder=embedder),
    ]
    second = [
        review_inputs.queue_items(refs, embedder=embedder),
        review_inputs.audit_items(refs, embedder=embedder),
        review_inputs.pair_items(refs, embedder=embedder).items,
        review_inputs.food_ref_items(refs, embedder=embedder),
    ]
    assert first == second
