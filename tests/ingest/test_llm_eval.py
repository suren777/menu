"""LLM eval tests: a fake client, never the network."""

import json
from typing import Any

from menu.domain.review import InputSet, ReviewItem
from menu.ingest import llm_eval, llm_review
from menu.ingest.llm_eval import EvalItem
from menu.ingest.llm_review import Usage


class KindClient:
    """One canned verdict per item in the chunk; records every call."""

    model_id = "fake/model"

    def __init__(self, verdict: dict[str, Any]) -> None:
        self.verdict = dict(verdict)
        self.calls: list[tuple[str, str, dict[str, Any], int]] = []

    def complete(
        self, system: str, user: str, schema: dict[str, Any], *, max_tokens: int
    ) -> tuple[str, Usage]:
        self.calls.append((system, user, schema, max_tokens))
        subjects = [
            line.split(": ", 1)[1] for line in user.splitlines() if line[:1].isdigit()
        ]
        verdicts = [{"index": index, **self.verdict} for index in range(len(subjects))]
        return json.dumps({"items": verdicts}), Usage(1, 1)


def _items() -> list[EvalItem]:
    return [
        EvalItem(ReviewItem(input="queue", subject="right kind"), "ok"),
        EvalItem(
            ReviewItem(input="queue", subject="wrong kind", known_concepts=("milk",)),
            "alias",
            "milk",
        ),
        EvalItem(ReviewItem(input="pair", subject="a | b"), "ok"),
        EvalItem(ReviewItem(input="food_ref", subject="icing sugar"), "ok"),
    ]


def test_evaluate_scores_each_input_set() -> None:
    results = llm_eval.evaluate(_items(), KindClient({"kind": "ok", "reason": "r"}))
    assert set(results) == {"queue", "pair", "food_ref"}
    queue = results["queue"]
    assert (queue.hits, queue.total) == (1, 2)
    [miss] = queue.misses
    assert miss.subject == "wrong kind"
    assert (miss.expected_kind, miss.got_kind) == ("alias", "ok")
    assert results["pair"].accuracy == 1.0
    assert results["food_ref"].accuracy == 1.0


def test_evaluate_checks_the_expected_target() -> None:
    items = [
        EvalItem(
            ReviewItem(
                input="pair", subject="whole milk | milk", known_concepts=("milk",)
            ),
            "variant",
            "milk",
        ),
        EvalItem(
            ReviewItem(
                input="pair", subject="dairy cream | cream", known_concepts=("cream",)
            ),
            "variant",
            "clotted",
        ),
    ]
    results = llm_eval.evaluate(
        items, KindClient({"kind": "variant", "variant": "whole", "reason": "r"})
    )
    pair = results["pair"]
    assert (pair.hits, pair.total) == (1, 2)
    [miss] = pair.misses
    assert (miss.got_kind, miss.got_target) == ("variant", "cream")


def test_dropped_verdicts_are_misses_with_no_got() -> None:
    # a variant without the variant word fails the mechanical checks
    items = [
        EvalItem(
            ReviewItem(
                input="pair", subject="whole milk | milk", known_concepts=("milk",)
            ),
            "variant",
            "milk",
        )
    ]
    results = llm_eval.evaluate(items, KindClient({"kind": "variant", "reason": "r"}))
    pair = results["pair"]
    assert (pair.hits, pair.total) == (0, 1)
    miss = pair.misses[0]
    assert (miss.got_kind, miss.got_target) == (None, None)


def test_fixture_loads_with_valid_items_and_kinds() -> None:
    items = llm_eval.load_fixture()
    assert len(items) >= 40
    per_set: dict[InputSet, int] = {}
    for entry in items:
        assert entry.expected_kind in llm_review._KINDS_BY_INPUT[entry.item.input]
        per_set[entry.item.input] = per_set.get(entry.item.input, 0) + 1
    assert set(per_set) == {"queue", "audit", "pair", "food_ref"}
    assert all(count >= 8 for count in per_set.values())
