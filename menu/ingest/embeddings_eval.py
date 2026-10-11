"""Evaluate an embedding model: pair accuracy and seeded-ref recall.

Ground truth per data_model_plan.md phase 4b:

- Accuracy on the hand-labelled `tests/ingest/fixtures/
  synonym_pairs.json`: same+variant are positive, different negative;
  the similarity threshold is swept and the best reported, with each
  pair's score.
- Recall@5 of the seeded FOOD_REFS: for each source, every
  `ref_food` row is embedded as "description, category" and each
  seeded ingredient name must find its own food in its top 5.

The numbers feed the plan (the integrator records them), so this is
a script, not a test:

    uv run menu-embed-eval [--model MODEL_ID]
"""

import argparse
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from menu.db.connection import get_ro_session
from menu.db.database import FoodSource, RefFood
from menu.db.ingredients.seed import FOOD_REFS
from menu.ingest.embeddings import DEFAULT_MODEL, FastEmbedder, embed, nearest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests/ingest/fixtures/synonym_pairs.json"
)
K = 5
POSITIVE_LABELS = frozenset({"same", "variant"})

# (a, b, label) — the plan's eight ground-truth pairs.
PLAN_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("icing sugar", "confectioners' sugar", "same"),
    ("aubergine", "eggplant", "same"),
    ("rocket", "arugula", "same"),
    ("coriander leaf", "cilantro", "same"),
    ("whole milk", "milk", "variant"),
    ("coriander", "ground coriander", "different"),
    ("baking soda", "baking powder", "different"),
    ("caster sugar", "icing sugar", "different"),
)

ScoredPair = tuple[str, str, str, float]


def pair_scores(model_id: str) -> list[ScoredPair]:
    """Cosine score for every fixture pair, in fixture order."""
    with FIXTURE_PATH.open() as file:
        pairs: list[dict[str, str]] = json.load(file)
    texts = sorted({text for pair in pairs for text in (pair["a"], pair["b"])})
    vectors = embed(texts, embedder=FastEmbedder(model_id))
    by_text = dict(zip(texts, vectors, strict=True))
    return [
        (
            pair["a"],
            pair["b"],
            pair["label"],
            float(by_text[pair["a"]] @ by_text[pair["b"]]),
        )
        for pair in pairs
    ]


def accuracy_at(scored: Sequence[ScoredPair], threshold: float) -> float:
    """Share of pairs whose label matches `score >= threshold`."""
    correct = sum(
        (score >= threshold) == (label in POSITIVE_LABELS)
        for _, _, label, score in scored
    )
    return correct / len(scored)


def best_threshold(scored: Sequence[ScoredPair]) -> tuple[float, float]:
    """The threshold with the best accuracy (lowest wins a tie, so
    the operating point stays as inclusive as possible); +inf covers
    the all-negative end of the sweep."""
    thresholds = sorted({score for _, _, _, score in scored})
    threshold, accuracy = max(
        ((t, accuracy_at(scored, t)) for t in (*thresholds, math.inf)),
        key=lambda item: item[1],
    )
    return threshold, accuracy


def report_pairs(model_id: str) -> None:
    scored = pair_scores(model_id)
    threshold, accuracy = best_threshold(scored)
    print(f"Synonym pairs ({model_id}, {FIXTURE_PATH}):")
    print(
        f"  best threshold {threshold:.4f}, "
        f"accuracy {accuracy * 100:.1f}% "
        f"({sum(1 for p in scored if p[2] in POSITIVE_LABELS)} positive, "
        f"{sum(1 for p in scored if p[2] not in POSITIVE_LABELS)} negative)"
    )
    for a, b, label, score in scored:
        print(f"  {score:7.4f}  {label:<10}  {a} / {b}")


def _ref_rows() -> Sequence[Any]:
    """(source, source_food_id, description, category) rows; empty
    when the ref tables don't exist yet (no database, no imports)."""
    try:
        with get_ro_session() as session:
            return session.execute(
                select(
                    FoodSource.name,
                    RefFood.source_food_id,
                    RefFood.description,
                    RefFood.category,
                ).join_from(FoodSource, RefFood, FoodSource.id == RefFood.source_id)
            ).all()
    except OperationalError:
        return []


def report_recall(model_id: str) -> None:
    """Recall@5 of the seeded FOOD_REFS, per source and overall."""
    rows = _ref_rows()
    if not rows:
        print(
            "Recall half skipped: the ref tables are empty — "
            "run menu-fdc-import (and menu-cofid-import / "
            "menu-cnf-import) first."
        )
        return

    # source -> [(source_food_id, "description, category")]
    corpus: dict[str, list[tuple[str, str]]] = {}
    for source, source_food_id, description, category in rows:
        text = f"{description}, {category}" if category else description
        corpus.setdefault(source, []).append((source_food_id, text))

    # source -> [(ingredient name, seeded source_food_id)]; FOOD_REFS
    # lists both roles per food, so (source, food) pairs are deduped.
    targets: dict[str, list[tuple[str, str]]] = {}
    for name, refs in FOOD_REFS.items():
        for source, source_food_id, _role in refs:
            entry = (name, source_food_id)
            if entry not in targets.setdefault(source, []):
                targets[source].append(entry)

    embedder = FastEmbedder(model_id)
    hits: dict[str, list[bool]] = {}
    for source, wanted in sorted(targets.items()):
        foods = corpus.get(source, [])
        if not foods:
            hits[source] = [False] * len(wanted)
            continue
        index_of = {food_id: i for i, (food_id, _) in enumerate(foods)}
        query_vectors = embed([name for name, _ in wanted], embedder=embedder)
        results = nearest(
            query_vectors, embed([text for _, text in foods], embedder=embedder), k=K
        )
        for (name, source_food_id), top in zip(wanted, results, strict=True):
            target = index_of.get(source_food_id)
            hit = target is not None and any(i == target for i, _ in top)
            hits.setdefault(source, []).append(hit)
            if not hit:
                print(f"  miss: {name} -> {source} {source_food_id}")

    print(f"Recall@{K} on seeded FOOD_REFS ({model_id}):")
    for source, outcomes in sorted(hits.items()):
        print(
            f"  {source}: {sum(outcomes)}/{len(outcomes)} = "
            f"{sum(outcomes) / len(outcomes):.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate an embedding model on the synonym "
        "pairs and the seeded FOOD_REFS (phase 4b ground truth)."
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="embedding model id (default: %(default)s)",
    )
    args = parser.parse_args()
    report_pairs(args.model)
    report_recall(args.model)


if __name__ == "__main__":
    main()
