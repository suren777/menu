"""LLM eval tests: a fake client, never the network."""

import json
import sys
from typing import TYPE_CHECKING, Any

import pytest

from menu.domain.review import FoodCandidate, InputSet, ReviewItem
from menu.ingest import llm_eval, llm_review
from menu.ingest.llm_eval import EvalItem
from menu.ingest.llm_review import Usage

if TYPE_CHECKING:
    from pathlib import Path


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


class SubjectClient:
    """One canned verdict per subject, whatever the set chunking does."""

    model_id = "fake/model"

    def __init__(self, verdicts: dict[str, dict[str, Any]]) -> None:
        self.verdicts = verdicts
        self.calls: list[tuple[str, str, dict[str, Any], int]] = []

    def complete(
        self, system: str, user: str, schema: dict[str, Any], *, max_tokens: int
    ) -> tuple[str, Usage]:
        self.calls.append((system, user, schema, max_tokens))
        subjects = [
            line.split(": ", 1)[1] for line in user.splitlines() if line[:1].isdigit()
        ]
        items = [
            {"index": index, **self.verdicts[subject]}
            for index, subject in enumerate(subjects)
        ]
        return json.dumps({"items": items}), Usage(1, 1)


def _fixture_file(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(entries))
    return path


def test_load_fixture_rejects_an_unknown_input_set(tmp_path: Path) -> None:
    path = _fixture_file(
        tmp_path, [{"input": "bogus", "subject": "s", "expected_kind": "ok"}]
    )
    with pytest.raises(ValueError, match="unknown input set: 'bogus'"):
        llm_eval.load_fixture(path)


def test_load_fixture_rejects_a_kind_invalid_for_the_set(tmp_path: Path) -> None:
    path = _fixture_file(
        tmp_path, [{"input": "pair", "subject": "a | b", "expected_kind": "not_food"}]
    )
    with pytest.raises(ValueError, match="is not valid for pair"):
        llm_eval.load_fixture(path)


def test_load_fixture_rejects_duplicate_subjects(tmp_path: Path) -> None:
    entry: dict[str, Any] = {"input": "queue", "subject": "milk", "expected_kind": "ok"}
    path = _fixture_file(tmp_path, [entry, dict(entry)])
    with pytest.raises(ValueError, match="duplicate fixture item"):
        llm_eval.load_fixture(path)


def test_evaluate_compares_line_override_and_food_ref_targets() -> None:
    """_target_of reads each proposal's own attribute: the override's
    name and the food ref's source_food_id."""
    items = [
        EvalItem(
            ReviewItem(
                input="queue", subject="milk", lines=("70g milk roughly chopped",)
            ),
            "line_override",
            "milk",
        ),
        EvalItem(
            ReviewItem(
                input="food_ref",
                subject="icing sugar",
                candidates=(FoodCandidate("fdc", "171287", "Icing sugar"),),
            ),
            "food_ref",
            "171287",
        ),
    ]
    client = SubjectClient(
        {
            "milk": {
                "kind": "line_override",
                "line": "70g milk roughly chopped",
                "name": "milk",
                "reason": "r",
            },
            "icing sugar": {
                "kind": "food_ref",
                "source": "fdc",
                "source_food_id": "171287",
                "reason": "r",
            },
        }
    )

    results = llm_eval.evaluate(items, client)

    assert results["queue"].accuracy == 1.0
    assert results["food_ref"].accuracy == 1.0


def test_report_prints_per_set_scores_and_misses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = llm_eval.evaluate(_items(), KindClient({"kind": "ok", "reason": "r"}))

    llm_eval.report(results, "fake/model")

    out = capsys.readouterr().out
    assert "LLM review accuracy (fake/model" in out
    assert "queue: 1/2 = 0.500" in out
    assert "miss: 'wrong kind': expected alias -> milk, got ok -> None" in out


def test_main_scores_the_requested_sets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real fixture runs through the suggest path on a fake client;
    --sets keeps only the named sets."""
    constructed: list[str] = []

    def fake_client(*, model_id: str) -> KindClient:
        constructed.append(model_id)
        return KindClient({"kind": "ok", "reason": "r"})

    monkeypatch.setattr(llm_eval, "FireworksClient", fake_client)
    monkeypatch.setattr(sys, "argv", ["menu-llm-eval", "--sets", "queue,pair"])

    llm_eval.main()

    out = capsys.readouterr().out
    assert constructed == [llm_review.DEFAULT_MODEL]
    assert "queue:" in out
    assert "pair:" in out
    assert "audit:" not in out  # --sets filtered the other sets out


def test_main_rejects_an_unknown_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["menu-llm-eval", "--sets", "bogus"])
    with pytest.raises(SystemExit):
        llm_eval.main()
