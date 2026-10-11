"""Review input sets for the LLM wave (plan phases 4b/4c).

Builds the four ReviewItem sets --suggest sends through
llm_review.suggest: queue (review-queue terms with their raw lines),
audit (single-use auto concepts), pairs (embedding neighbours above a
similarity floor) and food_ref candidates (reference foods ranked by
embedding similarity). Embeddings only order candidates for the model,
never decide (plan 4b: a recall tool, not a classifier), and every
builder is deterministic: same database, same vectors, same order.
Line counts are plain lines through terms — the parse vocabulary under
review, not the effective-ingredient aggregate.
"""

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    RecipeIngredient,
    RefFood,
)
from menu.ingest.embeddings import EMBEDDINGS_DIR, embed, nearest
from menu.ingest.llm_review import FoodCandidate, ReviewItem

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.orm import Session

    from menu.ingest.embeddings import Embedder

QUEUE_LINES = 3
"""Distinct raw lines a queue item carries as context."""
KNOWN_CONCEPTS = 10
"""Nearest concept names offered to the model as alias targets."""
PAIR_K = 3
"""Neighbours per concept for the pair set."""
PAIR_FLOOR = 0.8
"""Similarity floor for the pair set."""
FOOD_REFS_PER_SOURCE = 5
"""Reference-food candidates per source."""


def concept_text(name: str, raw_line: str | None) -> str:
    """Embedding text for a concept: the name with its most frequent
    raw line as context — short names alone embed noisily (plan 4b)."""
    return f"{name} ({raw_line})" if raw_line else name


def _top_texts(texts: Iterable[str], limit: int) -> list[str]:
    """Distinct texts, most frequent first, ties alphabetical."""
    counts = Counter(texts)
    ranked = sorted(counts, key=lambda text: (-counts[text], text))
    return ranked[:limit]


def _top_text(texts: Iterable[str]) -> str | None:
    ranked = _top_texts(texts, 1)
    return ranked[0] if ranked else None


def _lines_by_term(session: Session) -> dict[int, Counter[str]]:
    """term id -> raw-line counts of the lines using it."""
    counts: dict[int, Counter[str]] = {}
    for term_id, raw_text in session.execute(
        select(RecipeIngredient.term_id, RecipeIngredient.raw_text)
        .where(RecipeIngredient.term_id.is_not(None))
        .order_by(RecipeIngredient.id)
    ).all():
        if term_id is None:  # excluded above; keeps the types honest
            continue
        counts.setdefault(term_id, Counter())[raw_text] += 1
    return counts


def _lines_by_concept(session: Session) -> dict[int, list[tuple[str, str]]]:
    """concept id -> (term text, raw line) per line through its terms,
    in line id order — the one join every concept-based builder reads."""
    grouped: dict[int, list[tuple[str, str]]] = {}
    for ingredient_id, term_text, raw_text in session.execute(
        select(
            IngredientTerm.ingredient_id,
            IngredientTerm.term,
            RecipeIngredient.raw_text,
        )
        .join(RecipeIngredient, RecipeIngredient.term_id == IngredientTerm.id)
        .where(IngredientTerm.ingredient_id.is_not(None))
        .order_by(RecipeIngredient.id)
    ).all():
        if ingredient_id is None:  # excluded above
            continue
        grouped.setdefault(ingredient_id, []).append((term_text, raw_text))
    return grouped


def _concepts(session: Session) -> list[tuple[int, str, str, str]]:
    """(id, name, origin, embedding text) per concept, name order —
    the corpus the concept-based sets embed."""
    lines = _lines_by_concept(session)
    return [
        (
            record.id,
            record.name,
            record.origin,
            concept_text(
                record.name, _top_text([raw for _, raw in lines.get(record.id, [])])
            ),
        )
        for record in session.scalars(select(Ingredient).order_by(Ingredient.name))
    ]


def _nearest_names(
    query_texts: list[str],
    corpus_texts: list[str],
    corpus_names: list[str],
    *,
    k: int,
    embedder: Embedder | None,
) -> list[list[str]]:
    """Nearest corpus names per query, best first; nearest's stable
    sort keeps ties in corpus order."""
    if not query_texts or not corpus_texts:
        return [[] for _ in query_texts]
    hits = nearest(
        embed(query_texts, embedder=embedder, cache_dir=EMBEDDINGS_DIR),
        embed(corpus_texts, embedder=embedder, cache_dir=EMBEDDINGS_DIR),
        k=k,
    )
    return [[corpus_names[index] for index, _score in row] for row in hits]


def queue_items(
    session: Session, *, embedder: Embedder | None = None, limit: int | None = None
) -> list[ReviewItem]:
    """Review-queue terms with up to three distinct raw lines each:
    what is the ingredient, and is there an alternative or equipment
    in the line? Known concepts are the nearest concepts by embedding.
    Ordered by line count desc, then term text."""
    lines = _lines_by_term(session)
    terms = session.scalars(
        select(IngredientTerm).where(IngredientTerm.resolution == "review")
    ).all()
    ordered = sorted(
        terms,
        key=lambda term: (-sum(lines.get(term.id, Counter()).values()), term.term),
    )
    if limit is not None:
        ordered = ordered[:limit]
    if not ordered:
        return []
    concepts = _concepts(session)
    queries = [
        concept_text(term.term, _top_text(lines.get(term.id, Counter())))
        for term in ordered
    ]
    known = _nearest_names(
        queries,
        [text for _id, _name, _origin, text in concepts],
        [name for _id, name, _origin, _text in concepts],
        k=KNOWN_CONCEPTS,
        embedder=embedder,
    )
    return [
        ReviewItem(
            input="queue",
            subject=term.term,
            lines=tuple(_top_texts(lines.get(term.id, []), QUEUE_LINES)),
            known_concepts=tuple(neighbours),
        )
        for term, neighbours in zip(ordered, known, strict=True)
    ]


def audit_items(
    session: Session, *, embedder: Embedder | None = None, limit: int | None = None
) -> list[ReviewItem]:
    """Auto concepts reached by exactly one recipe line: is the parsed
    name right, was the right alternative kept, is it food? The subject
    is the line's term text; known concepts are the nearest other
    concepts. Ordered by term text (every line count is 1)."""
    concepts = _concepts(session)
    lines = _lines_by_concept(session)
    singles = []
    for cid, name, origin, text in concepts:
        rows = lines.get(cid, [])
        if origin == "auto" and len(rows) == 1:
            singles.append((name, rows[0][0], rows[0][1], text))
    singles.sort(key=lambda single: single[1])  # subject = term text
    if limit is not None:
        singles = singles[:limit]
    if not singles:
        return []
    known = _nearest_names(
        [text for _name, _term, _raw, text in singles],
        [text for _id, _name, _origin, text in concepts],
        [name for _id, name, _origin, _text in concepts],
        k=KNOWN_CONCEPTS + 1,  # the concept itself is dropped below
        embedder=embedder,
    )
    return [
        ReviewItem(
            input="audit",
            subject=term_text,
            lines=(raw_line,),
            known_concepts=tuple(
                neighbour for neighbour in neighbours if neighbour != name
            )[:KNOWN_CONCEPTS],
        )
        for (name, term_text, raw_line, _text), neighbours in zip(
            singles, known, strict=True
        )
    ]


@dataclass(frozen=True)
class PairItems:
    """The pair input set plus each pair's embedding similarity, for
    the caller to carry into the suggestion's evidence."""

    items: list[ReviewItem]
    similarity: dict[str, float]
    """subject ("a | b") -> cosine similarity of the two concepts."""


def pair_items(
    session: Session,
    *,
    embedder: Embedder | None = None,
    k: int = PAIR_K,
    floor: float = PAIR_FLOOR,
    limit: int | None = None,
) -> PairItems:
    """Neighbouring concept pairs above the floor: same food, variant
    or different? Pairs the terms already join (a term of one pointing
    at the other) and pairs where both are seed are skipped; unordered
    pairs are deduped. Ordered by the first-named concept's line count
    desc, then subject."""
    concepts = _concepts(session)
    if len(concepts) < 2:
        return PairItems([], {})
    lines = _lines_by_concept(session)
    vectors = embed(
        [text for _id, _name, _origin, text in concepts],
        embedder=embedder,
        cache_dir=EMBEDDINGS_DIR,
    )
    hits = nearest(vectors, vectors, k=k + 1)  # +1 for self, dropped below
    joined = {
        (term, ingredient_id)
        for term, ingredient_id in session.execute(
            select(IngredientTerm.term, IngredientTerm.ingredient_id).where(
                IngredientTerm.ingredient_id.is_not(None)
            )
        ).all()
        if ingredient_id is not None
    }
    id_by_name = {name: cid for cid, name, _origin, _text in concepts}
    candidates: list[tuple[int, str, float, str]] = []
    seen: set[tuple[str, str]] = set()
    for position, row in enumerate(hits):
        cid, name, origin, _text = concepts[position]
        for index, score in row:
            other_id, other_name, other_origin, _other = concepts[index]
            if other_id == cid or score < floor:
                continue
            if origin == "seed" and other_origin == "seed":
                continue  # both curated: nothing for the model to decide
            if (name, other_id) in joined or (other_name, cid) in joined:
                continue  # a term already joins the pair
            if origin == "auto" and other_origin == "auto":
                first, second = sorted((name, other_name))
            elif origin == "auto":
                first, second = name, other_name
            else:
                first, second = other_name, name
            key = (first, second)
            if key in seen:
                continue
            seen.add(key)
            count = len(lines.get(id_by_name[first], []))
            candidates.append((count, f"{first} | {second}", score, second))
    candidates.sort(key=lambda entry: (-entry[0], entry[1]))
    if limit is not None:
        candidates = candidates[:limit]
    return PairItems(
        [
            ReviewItem(input="pair", subject=subject, known_concepts=(other,))
            for _count, subject, _score, other in candidates
        ],
        {subject: score for _count, subject, score, _other in candidates},
    )


def ref_neighbours(
    session: Session,
    names: Sequence[str],
    *,
    embedder: Embedder | None = None,
    per_source: int = FOOD_REFS_PER_SOURCE,
) -> dict[str, list[tuple[str, str, str, float]]]:
    """name -> [(source, source_food_id, description, score)] — the top
    `per_source` reference foods per source by embedding similarity,
    rejected foods skipped, best first. The query is the concept text;
    the ref-food corpus is embedded once per call."""
    unique = list(dict.fromkeys(names))
    neighbours: dict[str, list[tuple[str, str, str, float]]] = {
        name: [] for name in unique
    }
    sources = list(session.scalars(select(FoodSource).order_by(FoodSource.id)))
    if not unique or not sources:
        return neighbours
    lines = _lines_by_concept(session)
    ids = {
        record.name: record.id
        for record in session.scalars(
            select(Ingredient).where(Ingredient.name.in_(unique))
        )
    }
    rejected: dict[int, set[int]] = {}
    for owner_id, ref_food_id in session.execute(
        select(IngredientFoodRef.ingredient_id, IngredientFoodRef.ref_food_id).where(
            IngredientFoodRef.status == "rejected"
        )
    ).all():
        rejected.setdefault(owner_id, set()).add(ref_food_id)
    foods = list(
        session.scalars(
            select(RefFood)
            .where(RefFood.source_id.in_([source.id for source in sources]))
            .order_by(RefFood.source_id, RefFood.id)
        )
    )
    if not foods:
        return neighbours
    queries = []
    for name in unique:
        ingredient_id = ids.get(name)
        raws = (
            [raw for _, raw in lines.get(ingredient_id, [])]
            if ingredient_id is not None
            else []
        )
        queries.append(concept_text(name, _top_text(raws)))
    hits = nearest(
        embed(queries, embedder=embedder, cache_dir=EMBEDDINGS_DIR),
        embed(
            [
                (
                    f"{food.description} ({food.category})"
                    if food.category is not None
                    else food.description
                )
                for food in foods
            ],
            embedder=embedder,
            cache_dir=EMBEDDINGS_DIR,
        ),
        k=len(foods),
    )
    source_names = {source.id: source.name for source in sources}
    for name, row in zip(unique, hits, strict=True):
        ingredient_id = ids.get(name)
        skip = (
            rejected.get(ingredient_id, set()) if ingredient_id is not None else set()
        )
        by_source: dict[int, list[tuple[float, str, str]]] = {}
        for index, score in row:
            food = foods[index]
            if food.id in skip:
                continue
            by_source.setdefault(food.source_id, []).append(
                (score, food.source_food_id, food.description)
            )
        neighbours[name] = [
            (source_names[source_id], source_food_id, description, score)
            for source_id, hits_for_source in sorted(by_source.items())
            for score, source_food_id, description in hits_for_source[:per_source]
        ]
    return neighbours


def food_ref_items(
    session: Session,
    *,
    embedder: Embedder | None = None,
    per_source: int = FOOD_REFS_PER_SOURCE,
    limit: int | None = None,
) -> list[ReviewItem]:
    """Ingredients with lines and no confirmed nutrition reference,
    each with the top reference foods per source as candidates: which
    of them, if any, is this ingredient? Ordered by line count desc,
    then name."""
    lines = _lines_by_concept(session)
    confirmed = set(
        session.scalars(
            select(IngredientFoodRef.ingredient_id).where(
                IngredientFoodRef.status == "confirmed",
                IngredientFoodRef.role == "nutrition",
            )
        )
    )
    rows = [
        (len(lines[cid]), name)
        for cid, name, _origin, _text in _concepts(session)
        if cid in lines and cid not in confirmed
    ]
    rows.sort(key=lambda row: (-row[0], row[1]))
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        return []
    neighbours = ref_neighbours(
        session,
        [name for _count, name in rows],
        embedder=embedder,
        per_source=per_source,
    )
    return [
        ReviewItem(
            input="food_ref",
            subject=name,
            candidates=tuple(
                FoodCandidate(
                    source=source,
                    source_food_id=source_food_id,
                    description=description,
                )
                for source, source_food_id, description, _score in neighbours[name]
            ),
        )
        for _count, name in rows
    ]
