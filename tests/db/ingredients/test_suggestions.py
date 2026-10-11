"""Suggestions store tests: the git-reviewed ledger between model and seed."""

import json
from typing import TYPE_CHECKING, Any

import pydantic
import pytest

from menu.db.ingredients import suggestions as suggestions_module
from menu.db.ingredients.suggestions import (
    Suggestion,
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


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "suggestions.json"
    entries = [
        _suggestion(subject="butter"),
        _suggestion(input="pair", subject="rocket | arugula", kind="alias"),
        _suggestion(subject="milk", kind="variant"),
    ]
    save(entries, path)
    expected = sorted(
        entries, key=lambda entry: (entry.input, entry.subject, entry.kind)
    )
    assert load(path) == expected
    assert not (tmp_path / "suggestions.tmp.json").exists()  # atomic write


def test_load_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json") == []


def test_save_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "suggestions.json"
    entries = [
        _suggestion(subject="b"),
        _suggestion(subject="a", kind="variant"),
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
        _suggestion(subject="c", kind="variant", status="accepted"),
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
