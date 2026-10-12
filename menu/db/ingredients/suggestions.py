"""The suggestions store: what the models propose, reviewed in git.

`menu/db/ingredients/suggestions.json` is the ledger between model
output and the seeds: embeddings and the LLM write entries here with
status "pending", you accept or reject them by editing `status` in a
reviewed diff, and seeding loads only the accepted ones. A rejected
entry stays in the file, so it is never proposed again. A missing file
is an empty ledger, not an error.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pydantic

if TYPE_CHECKING:
    from collections.abc import Iterable

SUGGESTIONS_PATH = Path(__file__).with_name("suggestions.json")
"""The committed suggestions ledger, next to this module."""

Status = Literal["pending", "accepted", "rejected"]


class Suggestion(pydantic.BaseModel):
    """One model proposal, reviewed by editing `status` in git."""

    model_config = pydantic.ConfigDict(frozen=True)

    input: str
    """The InputSet that produced it."""
    kind: str
    subject: str
    proposal: dict[str, Any]
    evidence: dict[str, Any]
    """At least {"reason": str}; the embedding wave adds neighbours."""
    model: dict[str, str]
    """{"llm": model id, ...} — the models that produced it."""
    status: Status = "pending"


def load(path: Path | None = None) -> list[Suggestion]:
    """Read the ledger; a missing file is an empty one. The default
    path is resolved at call time, so tests can repoint it."""
    if path is None:
        path = SUGGESTIONS_PATH
    if not path.exists():
        return []
    entries: list[Any] = json.loads(path.read_text(encoding="utf-8"))
    return [Suggestion.model_validate(entry) for entry in entries]


def save(suggestions: Iterable[Suggestion], path: Path | None = None) -> None:
    """Write the ledger deterministically: sorted, indented, atomic.
    The default path is resolved at call time, so tests can repoint it."""
    if path is None:
        path = SUGGESTIONS_PATH
    ordered = sorted(
        suggestions, key=lambda entry: (entry.input, entry.subject, entry.kind)
    )
    payload = [entry.model_dump(mode="json") for entry in ordered]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def reviewed_keys(suggestions: Iterable[Suggestion]) -> set[tuple[str, str]]:
    """(input, subject) of every reviewed item, any status."""
    return {(entry.input, entry.subject) for entry in suggestions}


def accepted(suggestions: Iterable[Suggestion], kind: str) -> list[Suggestion]:
    """Accepted suggestions of one kind — what seeding loads."""
    return [
        entry
        for entry in suggestions
        if entry.status == "accepted" and entry.kind == kind
    ]
