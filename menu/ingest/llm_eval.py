"""Evaluate the LLM review prompts on a hand-labelled fixture.

Ground truth is `tests/ingest/fixtures/llm_eval.json`: each item is a
ReviewItem plus the expected kind (and target where applicable). Items
run through the real `suggest` path — per-set prompts, schemas and
mechanical checks — with the client injectable, so a prompt change can
be scored as per-input-set accuracy. A script, not a test:

    uv run menu-llm-eval [--sets queue,audit] [--model MODEL_ID]
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from menu.ingest import llm_review
from menu.ingest.llm_review import (
    DEFAULT_MODEL,
    FireworksClient,
    FoodCandidate,
    InputSet,
    LLMClient,
    ReviewItem,
    suggest,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from menu.db.ingredients.suggestions import Suggestion

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests/ingest/fixtures/llm_eval.json"
)

INPUT_SETS: dict[str, InputSet] = {
    "queue": "queue",
    "audit": "audit",
    "pair": "pair",
    "food_ref": "food_ref",
}


@dataclass(frozen=True)
class EvalItem:
    """A labelled review item: what the model should answer."""

    item: ReviewItem
    expected_kind: str
    expected_target: str | None = None
    """alias/variant target, line_override name or reference food id."""


@dataclass(frozen=True)
class Miss:
    """An item whose suggestion missed the label, or never landed."""

    subject: str
    expected_kind: str
    expected_target: str | None
    got_kind: str | None  # None when no suggestion survived the checks
    got_target: str | None


@dataclass
class EvalResult:
    """Per-input-set score: hits and misses out of the labelled total."""

    hits: int = 0
    total: int = 0
    misses: list[Miss] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.hits / self.total if self.total else 0.0


def load_fixture(path: Path = FIXTURE_PATH) -> list[EvalItem]:
    """The labelled items; ValueError on an unknown set or kind, or a
    duplicate (input, subject) — duplicates would alias one verdict."""
    raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    items: list[EvalItem] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw:
        try:
            input_set = INPUT_SETS[entry["input"]]
        except KeyError:
            raise ValueError(f"unknown input set: {entry['input']!r}") from None
        expected_kind = str(entry["expected_kind"])
        if expected_kind not in llm_review._KINDS_BY_INPUT[input_set]:
            raise ValueError(
                f"{entry['subject']!r}: kind {expected_kind!r} "
                f"is not valid for {input_set}"
            )
        key = (input_set, str(entry["subject"]))
        if key in seen:
            raise ValueError(f"duplicate fixture item: {key}")
        seen.add(key)
        items.append(
            EvalItem(
                item=ReviewItem(
                    input=input_set,
                    subject=entry["subject"],
                    lines=tuple(entry.get("lines", ())),
                    candidates=tuple(
                        FoodCandidate(
                            candidate["source"],
                            candidate["source_food_id"],
                            candidate["description"],
                        )
                        for candidate in entry.get("candidates", ())
                    ),
                    known_concepts=tuple(entry.get("known_concepts", ())),
                ),
                expected_kind=expected_kind,
                expected_target=entry.get("expected_target"),
            )
        )
    return items


_TARGET_FIELDS = {
    "alias": "target",
    "variant": "target",
    "line_override": "name",
    "food_ref": "source_food_id",
}


def _target_of(suggestion: Suggestion) -> str | None:
    """The proposal value an expected_target compares against."""
    key = _TARGET_FIELDS.get(suggestion.kind)
    value = suggestion.proposal.get(key) if key else None
    return value if isinstance(value, str) else None


def evaluate(items: Sequence[EvalItem], client: LLMClient) -> dict[str, EvalResult]:
    """Score the labelled items: one `suggest` run, one chunk per set.

    An item hits when the suggestion surviving the mechanical checks
    has the expected kind and (where labelled) target; a verdict the
    model left out, or one a check dropped, is a miss with got=None.
    """
    result = suggest(
        [entry.item for entry in items], client=client, chunk_size=len(items) or 1
    )
    by_key: dict[tuple[str, str], Suggestion] = {
        (suggestion.input, suggestion.subject): suggestion
        for suggestion in result.suggestions
    }
    outcomes: dict[str, EvalResult] = {}
    for entry in items:
        outcome = outcomes.setdefault(entry.item.input, EvalResult())
        outcome.total += 1
        suggestion = by_key.get((entry.item.input, entry.item.subject))
        got_target = _target_of(suggestion) if suggestion else None
        if (
            suggestion is not None
            and suggestion.kind == entry.expected_kind
            and got_target == entry.expected_target
        ):
            outcome.hits += 1
        else:
            outcome.misses.append(
                Miss(
                    subject=entry.item.subject,
                    expected_kind=entry.expected_kind,
                    expected_target=entry.expected_target,
                    got_kind=suggestion.kind if suggestion else None,
                    got_target=got_target,
                )
            )
    return outcomes


def report(results: dict[str, EvalResult], model_id: str) -> None:
    print(f"LLM review accuracy ({model_id}, {FIXTURE_PATH}):")
    for input_set, outcome in sorted(results.items()):
        print(f"  {input_set}: {outcome.hits}/{outcome.total} = {outcome.accuracy:.3f}")
        for miss in outcome.misses:
            expected = miss.expected_kind
            if miss.expected_target:
                expected += f" -> {miss.expected_target}"
            got = (
                f"{miss.got_kind} -> {miss.got_target}"
                if miss.got_kind is not None
                else "no suggestion"
            )
            print(f"    miss: {miss.subject!r}: expected {expected}, got {got}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the LLM review prompts on the labelled "
        "llm_eval.json fixture (per-input-set accuracy)."
    )
    parser.add_argument(
        "--sets",
        default=None,
        help="comma-separated subset of queue,audit,pair,food_ref (default: all)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="chat model id (default: %(default)s)"
    )
    args = parser.parse_args()
    items = load_fixture()
    if args.sets:
        wanted = {name.strip() for name in args.sets.split(",")}
        unknown = wanted - INPUT_SETS.keys()
        if unknown:
            parser.error(f"unknown input set(s): {', '.join(sorted(unknown))}")
        items = [entry for entry in items if entry.item.input in wanted]
    report(evaluate(items, FireworksClient(model_id=args.model)), args.model)


if __name__ == "__main__":
    main()
