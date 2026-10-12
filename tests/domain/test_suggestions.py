"""Suggestions store tests: the git-reviewed ledger between model and seed."""

import json
from typing import TYPE_CHECKING, Any

import pydantic
import pytest

from menu.domain import suggestions as suggestions_module
from menu.domain.suggestions import (
    AliasProposal,
    FoodRefProposal,
    LineOverrideProposal,
    NotFoodProposal,
    OkProposal,
    Suggestion,
    VariantProposal,
    accepted,
    load,
    reviewed_keys,
    save,
)

if TYPE_CHECKING:
    from pathlib import Path


def _suggestion(**overrides: Any) -> Suggestion:
    fields: dict[str, Any] = {
        "input": "queue",
        "kind": "alias",
        "subject": "milk",
        "proposal": {"term": "milk", "target": "milk", "variant": None, "new": False},
        "evidence": {"reason": "same food"},
        "model": {"llm": "fake/model"},
    }
    fields.update(overrides)
    return Suggestion.model_validate(fields)


def _variant_suggestion(**overrides: Any) -> Suggestion:
    return _suggestion(
        kind="variant",
        proposal={"term": "milk", "target": "milk", "variant": "warm"},
        **overrides,
    )


def _ledger_entries() -> list[dict[str, Any]]:
    """One entry per kind, exactly as llm_review and a human rejection
    write them, in the order save() produces."""
    base = {
        "evidence": {"reason": "r"},
        "model": {"llm": "fake/model"},
        "status": "pending",
    }
    return [
        {
            "input": "food_ref",
            "kind": "food_ref",
            "subject": "tahini",
            "proposal": {
                "ingredient": "tahini",
                "source": "fdc",
                "source_food_id": "1269801",
            },
            **base,
        },
        {
            "input": "queue",
            "kind": "line_override",
            "subject": "70g chocolate",
            "proposal": {"line": "70g chocolate", "name": "chocolate"},
            **base,
        },
        {
            "input": "queue",
            "kind": "not_food",
            "subject": "bundt tin",
            "proposal": {"term": "bundt tin"},
            **base,
        },
        {"input": "queue", "kind": "ok", "subject": "milk", "proposal": {}, **base},
        {
            "input": "queue",
            "kind": "alias",
            "subject": "rocket",
            "proposal": {
                "term": "rocket",
                "target": "arugula",
                "variant": None,
                "new": False,
            },
            **base,
        },
        {
            "input": "queue",
            "kind": "variant",
            "subject": "whole milk",
            "proposal": {"term": "whole milk", "target": "milk", "variant": "whole"},
            **base,
        },
    ]


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "suggestions.json"
    entries = [
        _suggestion(subject="butter"),
        _suggestion(input="pair", subject="rocket | arugula", kind="alias"),
        _variant_suggestion(subject="milk"),
    ]
    save(entries, path)
    expected = sorted(
        entries, key=lambda entry: (entry.input, entry.subject, entry.kind)
    )
    assert load(path) == expected
    assert not (tmp_path / "suggestions.tmp.json").exists()  # atomic write


def test_every_kind_round_trips_byte_for_byte(tmp_path: Path) -> None:
    """The six proposal shapes load and save unchanged: the file's JSON
    format is exactly what it was before the proposals were typed."""
    path = tmp_path / "suggestions.json"
    path.write_text(
        json.dumps(_ledger_entries(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    original = path.read_text(encoding="utf-8")

    entries = load(path)
    save(entries, path)

    assert path.read_text(encoding="utf-8") == original
    by_kind = {entry.kind: entry.proposal for entry in entries}
    assert by_kind["alias"] == AliasProposal(term="rocket", target="arugula")
    assert by_kind["variant"] == VariantProposal(
        term="whole milk", target="milk", variant="whole"
    )
    assert by_kind["line_override"] == LineOverrideProposal(
        line="70g chocolate", name="chocolate"
    )
    assert by_kind["not_food"] == NotFoodProposal(term="bundt tin")
    assert by_kind["food_ref"] == FoodRefProposal(
        ingredient="tahini", source="fdc", source_food_id="1269801"
    )
    assert by_kind["ok"] == OkProposal()


def test_missing_or_extra_proposal_keys_raise(tmp_path: Path) -> None:
    """A proposal with a required key missing or an unexpected one is a
    load-time ValidationError, never drift into seeding."""
    path = tmp_path / "suggestions.json"
    base = {
        "input": "queue",
        "kind": "alias",
        "subject": "milk",
        "evidence": {"reason": "r"},
        "model": {"llm": "m"},
        "status": "accepted",
    }
    missing = {**base, "proposal": {"term": "milk"}}  # no target
    extra = {
        **base,
        "proposal": {"term": "milk", "target": "milk", "oops": 1},
    }
    for entry in (missing, extra):
        path.write_text(json.dumps([entry]), encoding="utf-8")
        with pytest.raises(pydantic.ValidationError):
            load(path)


def test_kind_mismatch_cannot_load() -> None:
    """A proposal naming a different kind than its entry — or a
    file whose proposal dict carries a conflicting kind — is refused."""
    with pytest.raises(pydantic.ValidationError):
        Suggestion(
            input="queue",
            kind="alias",
            subject="milk",
            proposal=VariantProposal(term="milk", target="milk", variant="warm"),
            evidence={"reason": "r"},
            model={"llm": "m"},
        )
    with pytest.raises(pydantic.ValidationError, match="does not match"):
        Suggestion.model_validate(
            {
                "input": "queue",
                "kind": "alias",
                "subject": "milk",
                "proposal": {
                    "kind": "variant",
                    "term": "milk",
                    "target": "milk",
                    "variant": "warm",
                },
                "evidence": {"reason": "r"},
                "model": {"llm": "m"},
            }
        )


def test_load_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json") == []


def test_save_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "suggestions.json"
    entries = [
        _suggestion(subject="b"),
        _variant_suggestion(subject="a"),
        _suggestion(subject="a"),
    ]
    save(entries, path)
    first = path.read_text(encoding="utf-8")
    save(list(reversed(entries)), path)  # order in, not order out
    assert path.read_text(encoding="utf-8") == first
    assert first == json.dumps(json.loads(first), indent=2, ensure_ascii=False) + "\n"
    assert [entry["subject"] for entry in json.loads(first)] == ["a", "a", "b"]


def test_non_ascii_survives_unescaped(tmp_path: Path) -> None:
    path = tmp_path / "suggestions.json"
    save([_suggestion(subject="crème fraîche")], path)
    assert "crème fraîche" in path.read_text(encoding="utf-8")


def test_suggestions_are_frozen() -> None:
    entry = _suggestion()
    with pytest.raises(pydantic.ValidationError):
        entry.status = "accepted"


def test_reviewed_keys_cover_every_status() -> None:
    entries = [
        _suggestion(subject="a"),
        _suggestion(subject="b", status="accepted"),
        _suggestion(subject="c", status="rejected"),
    ]
    assert reviewed_keys(entries) == {("queue", "a"), ("queue", "b"), ("queue", "c")}


def test_accepted_filters_by_status_and_kind() -> None:
    entries = [
        _suggestion(subject="a", status="accepted"),
        _suggestion(subject="b"),
        _variant_suggestion(subject="c", status="accepted"),
        _suggestion(subject="d", status="rejected"),
    ]
    assert accepted(entries, "alias") == [entries[0]]
    assert accepted(entries, "variant") == [entries[2]]
    assert accepted(entries, "line_override") == []


def test_default_path_resolved_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load()/save() with no path read SUGGESTIONS_PATH at call time,
    so tests (and callers) can repoint the ledger."""
    path = tmp_path / "suggestions.json"
    monkeypatch.setattr(suggestions_module, "SUGGESTIONS_PATH", path)
    entry = _suggestion(subject="late")

    save([entry])

    assert path.exists()
    assert load() == [entry]
