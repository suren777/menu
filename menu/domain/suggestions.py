"""The suggestions store: what the models propose, reviewed in git.

`menu/db/ingredients/suggestions.json` is the ledger between model
output and the seeds: embeddings and the LLM write entries here with
status "pending", you accept or reject them by editing `status` in a
reviewed diff, and seeding loads only the accepted ones. A rejected
entry stays in the file, so it is never proposed again. A missing file
is an empty ledger, not an error.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import pydantic

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

SUGGESTIONS_PATH = (
    Path(__file__).resolve().parents[1] / "db/ingredients/suggestions.json"
)
"""The committed suggestions ledger, under menu/db/ingredients/."""

_ledger_override: ContextVar[Path | None] = ContextVar("ledger_override", default=None)


def ledger_path() -> Path:
    """The ledger load()/save() act on by default: a `use_ledger`
    override > the committed SUGGESTIONS_PATH."""
    return _ledger_override.get() or SUGGESTIONS_PATH


@contextmanager
def use_ledger(path: Path) -> Iterator[None]:
    """Make `path` the default ledger for load()/save() calls with no
    explicit path run in the block — and only there."""
    token = _ledger_override.set(path)
    try:
        yield
    finally:
        _ledger_override.reset(token)


Status = Literal["pending", "accepted", "rejected"]

Kind = Literal["alias", "variant", "line_override", "not_food", "food_ref", "ok"]
"""What a proposal is; also the discriminator on every proposal model.

The ledger stores the kind only at the top level of an entry; loading
copies it into the proposal dict so the union can discriminate (see
Suggestion._tag_proposal), and it is excluded from every dump."""


class AliasProposal(pydantic.BaseModel):
    """alias: the term names the same food as `target`."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    kind: Literal["alias"] = pydantic.Field(default="alias", exclude=True)
    term: str
    target: str
    variant: str | None = None
    new: bool = False


class VariantProposal(pydantic.BaseModel):
    """variant: the term is `target`'s food in a distinct `variant`."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    kind: Literal["variant"] = pydantic.Field(default="variant", exclude=True)
    term: str
    target: str
    variant: str


class LineOverrideProposal(pydantic.BaseModel):
    """line_override: the raw `line` should parse as `name`."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    kind: Literal["line_override"] = pydantic.Field(
        default="line_override", exclude=True
    )
    line: str
    name: str


class NotFoodProposal(pydantic.BaseModel):
    """not_food: the term is equipment or otherwise inedible."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    kind: Literal["not_food"] = pydantic.Field(default="not_food", exclude=True)
    term: str


class FoodRefProposal(pydantic.BaseModel):
    """food_ref: the ingredient maps to this reference food."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    kind: Literal["food_ref"] = pydantic.Field(default="food_ref", exclude=True)
    ingredient: str
    source: str
    source_food_id: str


class OkProposal(pydantic.BaseModel):
    """ok: the parse is right and nothing needs to change."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ok"] = pydantic.Field(default="ok", exclude=True)


Proposal = Annotated[
    AliasProposal
    | VariantProposal
    | LineOverrideProposal
    | NotFoodProposal
    | FoodRefProposal
    | OkProposal,
    pydantic.Field(discriminator="kind"),
]
"""The six proposal shapes, discriminated by their `kind`."""


class Suggestion(pydantic.BaseModel):
    """One model proposal, reviewed by editing `status` in git."""

    model_config = pydantic.ConfigDict(frozen=True)

    input: str
    """The InputSet that produced it."""
    kind: Kind
    subject: str
    proposal: Proposal
    evidence: dict[str, Any]
    """At least {"reason": str}; the embedding wave adds neighbours."""
    model: dict[str, str]
    """{"llm": model id, ...} — the models that produced it."""
    status: Status = "pending"

    @pydantic.model_validator(mode="before")
    @classmethod
    def _tag_proposal(cls, data: Any) -> Any:
        """Copy the entry's kind into a dict proposal: the ledger keeps
        the kind only at the top level, and the discriminated union
        reads it from the proposal. A proposal that carries its own,
        different kind stays untouched and fails the check below."""
        if isinstance(data, dict):
            proposal = data.get("proposal")
            if isinstance(proposal, dict) and "kind" not in proposal:
                data = {**data, "proposal": {**proposal, "kind": data.get("kind")}}
        return data

    @pydantic.model_validator(mode="after")
    def _kinds_agree(self) -> Suggestion:
        """A proposal naming a different kind than its entry cannot
        load: the ledger's kind is the only one."""
        if self.proposal.kind != self.kind:
            raise ValueError("proposal kind does not match the entry kind")
        return self


def load(path: Path | None = None) -> list[Suggestion]:
    """Read the ledger; a missing file is an empty one. A path of None
    resolves through ledger_path()."""
    if path is None:
        path = ledger_path()
    if not path.exists():
        return []
    entries: list[Any] = json.loads(path.read_text(encoding="utf-8"))
    return [Suggestion.model_validate(entry) for entry in entries]


def save(suggestions: Iterable[Suggestion], path: Path | None = None) -> None:
    """Write the ledger deterministically: sorted, indented, atomic.
    A path of None resolves through ledger_path()."""
    if path is None:
        path = ledger_path()
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
