"""LLM review: classify review items into suggestions.

Models only suggest, per the plan's principle 7: they decide language
(what the ingredient is, which part of a line is an alternative, what
is equipment) and never write resolution or produce numbers. `suggest`
groups review items by input set, sends them in chunks through an
LLMClient under a strict JSON schema, and runs the mechanical checks
on every verdict. A chunk that comes back empty or unparseable is
logged and skipped, never a crash; items the model leaves out simply
yield nothing. What survives lands as pending Suggestions for the
suggestions.json ledger, where they are reviewed in git.
"""

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import requests
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from menu.db.ingredients.suggestions import Suggestion

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from urllib3 import BaseHTTPResponse

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_MODEL = "accounts/revolut-non-coding/deployments/revolut-non-coding-flash"

# Connect/read timeout: fail fast on a dead connection, wait out a
# slow completion.
_TIMEOUT: tuple[int, int] = (10, 120)
_MAX_RETRY_AFTER_SECONDS = 60
# Sized to the chunk: under a strict grammar the model can pad its
# reply with whitespace up to the ceiling, and a flat 16k ceiling
# turned one 1-item request into an 8-minute hang (2026-10-11).
_BASE_TOKENS = 1_000
_TOKENS_PER_ITEM = 150
_SCHEMA_NAME = "menu_review"

InputSet = Literal["queue", "audit", "pair", "food_ref"]
SuggestionKind = Literal[
    "alias", "variant", "line_override", "not_food", "food_ref", "ok"
]


class _CappedRetry(Retry):
    """Retry whose server-sent Retry-After never exceeds the cap.

    A rate limit that says "wait 10 minutes" would otherwise freeze a
    100-chunk run for hours; waiting past a minute is the crawl's
    choice to make (fetch.py waits out Retry-After once, its pacing),
    not a blind per-request retry's.
    """

    def get_retry_after(self, response: BaseHTTPResponse) -> float | None:
        retry_after = super().get_retry_after(response)
        if retry_after is None:
            return None
        return min(retry_after, float(_MAX_RETRY_AFTER_SECONDS))


_RETRIES = _CappedRetry(
    total=4,
    backoff_factor=2,
    backoff_max=_MAX_RETRY_AFTER_SECONDS,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"POST"}),
    respect_retry_after_header=True,
    raise_on_status=False,
)


def _new_session() -> Session:
    """Own session for the LLM endpoint, separate from the crawl's.

    The crawl session (menu.ingest.http) carries the crawler's
    User-Agent and GET-only retry policy and must not be reused here.
    """
    session = Session()
    adapter = HTTPAdapter(max_retries=_RETRIES)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class _PostSession(Protocol):
    """The one method a chat completion needs; test fakes satisfy it."""

    def post(self, url: str, **kwargs: Any) -> Any: ...


class LLMError(RuntimeError):
    """The LLM request could not be made or answered unusably."""


@dataclass(frozen=True)
class FoodCandidate:
    """One reference-food row an ingredient might map to."""

    source: str  # "fdc" | "cofid" | "cnf"
    source_food_id: str
    description: str


@dataclass(frozen=True)
class ReviewItem:
    """One item to classify, with the context the model may use."""

    input: InputSet
    subject: str  # term text; for "pair": "a | b"; for "food_ref": the ingredient name
    lines: tuple[str, ...] = ()  # raw recipe lines that use the subject
    candidates: tuple[FoodCandidate, ...] = ()  # food_ref only
    known_concepts: tuple[str, ...] = ()  # existing concept names an alias may target


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int


class LLMClient(Protocol):
    """Anything that completes a prompt and reports its token usage."""

    model_id: str

    def complete(
        self, system: str, user: str, schema: dict[str, Any], *, max_tokens: int
    ) -> tuple[str, Usage]: ...


class FireworksClient:
    """LLMClient on an OpenAI-compatible chat completions endpoint.

    Checked by hand on 2026-10-11: no batch mode on this deployment,
    so chunks go out synchronously, and the reasoning model can hit
    the token ceiling and return empty content, which `suggest`
    treats as a failed chunk.

    Transient failures (429, 5xx, connection errors) are retried with
    exponential backoff, honouring Retry-After up to the 60 s cap; a
    chat completion has no side effects beyond billing, so the retry
    cannot double-apply anything. When retries are exhausted a
    non-200 still raises LLMError with the status and body excerpt.
    """

    model_id: str

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model_id: str | None = None,
        session: _PostSession | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("MENU_LLM_BASE_URL") or DEFAULT_BASE_URL
        )
        self.model_id = model_id or os.environ.get("MENU_LLM_MODEL") or DEFAULT_MODEL
        self._session = session or _new_session()

    def complete(
        self, system: str, user: str, schema: dict[str, Any], *, max_tokens: int
    ) -> tuple[str, Usage]:
        api_key = os.environ.get("FIREWORKS_API_KEY")
        if not api_key:
            raise LLMError("FIREWORKS_API_KEY is not set — export it before suggesting")
        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "reasoning_effort": "low",
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": _SCHEMA_NAME,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        try:
            response = self._session.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(
                f"LLM API returned HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise LLMError("LLM API response is not a JSON object")
        usage = data.get("usage") or {}
        choices = data.get("choices") or [{}]
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = (message or {}).get("content") or ""
        return content, Usage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )


_RULES = """\
Decide language only: what the ingredient is, which part of the line is an \
alternative, and what is equipment. Never output quantities, weights or \
nutrients. UK and US names are both in use ("courgette" and "zucchini" \
name the same vegetable). "Related" is not "same": coriander leaf and \
ground coriander seed are different foods. Answer only with JSON matching \
the schema: one verdict per item, its number as "index", and leave an \
item out rather than guess."""

_QUEUE_SYSTEM = f"""\
You review terms from parsed recipe ingredient lines. For each term choose \
one kind. "alias": the term names the same food as an existing concept or \
should become a new one — give "target", set "new" true only when no known \
concept fits, and "variant" to the variety or preparation word the term \
carries, if any. "variant": the term is the same food as a known concept \
but a distinct variety or preparation of it — give "target" and the \
"variant" word ("whole" for whole milk). "line_override": a raw line was \
parsed wrongly and should name something else — give "line" copied \
verbatim from the item and the correct "name". "not_food": the term is \
equipment or otherwise inedible. "ok": the parse is right and nothing \
needs to change.

{_RULES}"""

_AUDIT_SYSTEM = f"""\
You audit names the parser extracted on its own from recipe ingredient \
lines. For each name choose one kind. "alias": the name is the same food \
as an existing concept or should become a new one — give "target", set \
"new" true only when no known concept fits, and "variant" to the variety \
or preparation word the name carries, if any. "variant": the name is the \
same food as a known concept but a distinct variety or preparation of it \
— give "target" and the "variant" word. "line_override": the parsed name \
is wrong — the wrong alternative was kept, or boilerplate survived — give \
"line" copied verbatim from the item and the name it should be. \
"not_food": the name is equipment or otherwise inedible. "ok": the parsed \
name is right.

{_RULES}"""

_PAIR_SYSTEM = f"""\
Each item is two ingredient names separated by " | ". Decide how the \
first relates to the second. "alias": the first names the same food as \
the second (regional names and spellings) — set "new" true only when the \
second name should become a concept of its own. "variant": the first is \
the same food as the second but a distinct variety or preparation of it — \
give the "variant" word ("whole" for "whole milk | milk"). "ok": they are \
different foods.

{_RULES}"""

_FOOD_REF_SYSTEM = f"""\
Each item is an ingredient name with candidate rows from reference \
nutrient datasets. "food_ref": one candidate is the same food as the \
ingredient — give "source" and "source_food_id" exactly as listed. "none": \
no candidate fits, and "source" and "source_food_id" are null.

{_RULES}"""

_SYSTEM_PROMPTS: dict[InputSet, str] = {
    "queue": _QUEUE_SYSTEM,
    "audit": _AUDIT_SYSTEM,
    "pair": _PAIR_SYSTEM,
    "food_ref": _FOOD_REF_SYSTEM,
}

_KINDS_BY_INPUT: dict[InputSet, frozenset[str]] = {
    "queue": frozenset({"alias", "variant", "line_override", "not_food", "ok"}),
    "audit": frozenset({"alias", "variant", "line_override", "not_food", "ok"}),
    "pair": frozenset({"alias", "variant", "ok"}),
    "food_ref": frozenset({"food_ref", "ok"}),
}

# The model reads "ok" on a food_ref item as "this candidate is fine"
# and names it anyway, so the wire word for "no match" is "none".
_WIRE_KINDS: dict[InputSet, dict[str, str]] = {"food_ref": {"ok": "none"}}


def _wire_kinds(input_set: InputSet) -> list[str]:
    renames = _WIRE_KINDS.get(input_set, {})
    return sorted(renames.get(kind, kind) for kind in _KINDS_BY_INPUT[input_set])


def _from_wire(input_set: InputSet, kind: str) -> str:
    for ours, wire in _WIRE_KINDS.get(input_set, {}).items():
        if kind == wire:
            return ours
    return kind


# Strict schema mode wants every property required, so fields a kind
# doesn't use are nullable instead of absent.
_STRING_OR_NULL: dict[str, Any] = {"type": ["string", "null"]}
_BOOL_OR_NULL: dict[str, Any] = {"type": ["boolean", "null"]}

_CONCEPT_FIELDS: dict[str, dict[str, Any]] = {
    "target": _STRING_OR_NULL,
    "variant": _STRING_OR_NULL,
    "new": _BOOL_OR_NULL,
    "line": _STRING_OR_NULL,
    "name": _STRING_OR_NULL,
}

_VERDICT_FIELDS: dict[InputSet, dict[str, dict[str, Any]]] = {
    "queue": _CONCEPT_FIELDS,
    "audit": _CONCEPT_FIELDS,
    "pair": {"variant": _STRING_OR_NULL, "new": _BOOL_OR_NULL},
    "food_ref": {"source": _STRING_OR_NULL, "source_food_id": _STRING_OR_NULL},
}


def _response_schema(input_set: InputSet) -> dict[str, Any]:
    verdict: dict[str, Any] = {
        "index": {"type": "integer"},
        "kind": {"enum": _wire_kinds(input_set)},
        **_VERDICT_FIELDS[input_set],
        "reason": {"type": "string"},
    }
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": verdict,
                    "required": [*verdict],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


_SCHEMAS: dict[InputSet, dict[str, Any]] = {
    input_set: _response_schema(input_set) for input_set in _KINDS_BY_INPUT
}


def _user_message(input_set: InputSet, chunk: Sequence[ReviewItem]) -> str:
    label = {"pair": "pair", "food_ref": "ingredient"}.get(input_set, "term")
    out: list[str] = []
    for number, item in enumerate(chunk):
        out.append(f"{number}. {label}: {item.subject}")
        for line in item.lines:
            out.append(f"   line: {line}")
        for candidate in item.candidates:
            out.append(
                f"   candidate {candidate.source} {candidate.source_food_id}: "
                f"{candidate.description}"
            )
        if item.known_concepts:
            out.append("   known concepts: " + ", ".join(item.known_concepts))
    return "\n".join(out)


def _chunks(group: list[ReviewItem], size: int) -> Iterator[list[ReviewItem]]:
    for start in range(0, len(group), size):
        yield group[start : start + size]


_NUMBER_RE = re.compile(r"\d+(?:[.,/]\d+)?")


def _string_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _string_values(nested)


def _numbers_ok(item: ReviewItem, proposal: dict[str, Any]) -> bool:
    if item.input in ("pair", "food_ref"):
        return all(
            not _NUMBER_RE.search(text)
            for key, value in proposal.items()
            if key != "source_food_id"
            for text in _string_values(value)
        )
    allowed = {
        match.group(0)
        for text in (*item.lines, item.subject)
        for match in _NUMBER_RE.finditer(text)
    }
    return all(
        set(_NUMBER_RE.findall(text)) <= allowed
        for value in proposal.values()
        for text in _string_values(value)
    )


def _concept_proposal(
    item: ReviewItem, kind: str, verdict: dict[str, Any]
) -> dict[str, Any] | str:
    """alias/variant for queue and audit: the model picks the target."""
    target = verdict.get("target")
    variant = verdict.get("variant")
    new = verdict.get("new") is True
    if not isinstance(target, str):
        return "kind"
    if variant is not None and not isinstance(variant, str):
        return "kind"
    if kind == "variant":
        if not isinstance(variant, str):
            return "kind"  # a variant proposal needs the variant word
        if target not in item.known_concepts:
            return "target"  # variants must target a known concept
        return {"term": item.subject, "target": target, "variant": variant}
    if target not in item.known_concepts and not new:
        return "target"
    return {"term": item.subject, "target": target, "variant": variant, "new": new}


def _pair_proposal(
    item: ReviewItem, kind: str, verdict: dict[str, Any]
) -> dict[str, Any] | str:
    """alias/variant for pairs: the subject's halves are term and target."""
    head, _, tail = item.subject.partition(" | ")
    if not tail:
        return "kind"  # pair subjects are always "a | b"
    new = verdict.get("new") is True
    if kind == "variant":
        variant = verdict.get("variant")
        if not isinstance(variant, str):
            return "kind"
        if tail not in item.known_concepts:
            return "target"
        return {"term": head, "target": tail, "variant": variant}
    if tail not in item.known_concepts and not new:
        return "target"
    return {"term": head, "target": tail, "variant": None, "new": new}


def _proposal(
    item: ReviewItem, kind: str, verdict: dict[str, Any]
) -> dict[str, Any] | str:
    """The proposal dict, or the mechanical-check reason it is dropped."""
    if kind == "ok":
        # "None fits" that still names a candidate contradicts itself;
        # recording it as reviewed would hide the item for good.
        if item.input == "food_ref" and verdict.get("source_food_id") is not None:
            return "kind"
        return {}
    if kind == "not_food":
        return {"term": item.subject}
    if item.input in ("queue", "audit"):
        if kind == "line_override":
            line = verdict.get("line")
            name = verdict.get("name")
            if not isinstance(line, str) or not isinstance(name, str):
                return "kind"
            if line not in item.lines:
                return "line"
            return {"line": line, "name": name}
        return _concept_proposal(item, kind, verdict)
    if item.input == "pair":
        return _pair_proposal(item, kind, verdict)
    source = verdict.get("source")
    source_food_id = verdict.get("source_food_id")
    if not isinstance(source, str) or not isinstance(source_food_id, str):
        return "kind"
    if not any(
        candidate.source == source and candidate.source_food_id == source_food_id
        for candidate in item.candidates
    ):
        return "candidate"
    return {
        "ingredient": item.subject,
        "source": source,
        "source_food_id": source_food_id,
    }


def _verdict_suggestion(
    chunk: Sequence[ReviewItem], verdict: dict[str, Any], model_id: str
) -> Suggestion | str:
    """A Suggestion for the verdict, or the mechanical-check drop reason."""
    index = verdict.get("index")
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not 0 <= index < len(chunk)
    ):
        return "kind"
    item = chunk[index]
    kind = verdict.get("kind")
    if isinstance(kind, str):
        kind = _from_wire(item.input, kind)
    if not isinstance(kind, str) or kind not in _KINDS_BY_INPUT[item.input]:
        return "kind"
    proposal = _proposal(item, kind, verdict)
    if isinstance(proposal, str):
        return proposal
    if not _numbers_ok(item, proposal):
        return "numbers"
    return Suggestion(
        input=item.input,
        kind=kind,
        subject=item.subject,
        proposal=proposal,
        evidence={"reason": str(verdict.get("reason") or "")},
        model={"llm": model_id},
    )


def _parse_verdicts(content: str) -> list[Any]:
    """The items array of the model's JSON reply; ValueError otherwise."""
    data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("expected a JSON object with an items array")
    return list(data["items"])


@dataclass
class SuggestResult:
    """Suggestions that survived the checks, plus what didn't and why."""

    suggestions: list[Suggestion]
    dropped: dict[str, int]  # reason -> count, from the mechanical checks
    failed_chunks: int
    usage: Usage  # summed over the calls that returned one


def suggest(
    items: Sequence[ReviewItem],
    *,
    client: LLMClient | None = None,
    chunk_size: int = 50,
) -> SuggestResult:
    """Classify review items into pending suggestions, in chunks.

    Items are grouped by input set (one prompt and schema per set) and
    sent chunk_size at a time; callers skip anything already reviewed
    (suggestions.reviewed_keys). An empty or unparseable chunk reply is
    logged and skipped, never a crash.
    """
    worker = client or FireworksClient()
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    suggestions: list[Suggestion] = []
    dropped: Counter[str] = Counter()
    failed_chunks = 0
    prompt_tokens = 0
    completion_tokens = 0

    by_input: dict[InputSet, list[ReviewItem]] = {}
    for item in items:
        by_input.setdefault(item.input, []).append(item)

    for input_set, group in by_input.items():
        system = _SYSTEM_PROMPTS[input_set]
        schema = _SCHEMAS[input_set]
        for chunk in _chunks(group, chunk_size):
            user = _user_message(input_set, chunk)
            try:
                content, usage = worker.complete(
                    system,
                    user,
                    schema,
                    max_tokens=_BASE_TOKENS + _TOKENS_PER_ITEM * len(chunk),
                )
            except (LLMError, ValueError) as exc:
                failed_chunks += 1
                logger.warning("LLM chunk for %s failed: %.200s", input_set, exc)
                continue
            prompt_tokens += usage.prompt_tokens
            completion_tokens += usage.completion_tokens
            try:
                verdicts = _parse_verdicts(content)
            except ValueError as exc:
                failed_chunks += 1
                logger.warning(
                    "LLM verdict for %s chunk unusable: %.200s", input_set, exc
                )
                continue
            seen: set[Any] = set()
            for entry in verdicts:
                if not isinstance(entry, dict) or entry.get("index") in seen:
                    dropped["kind"] += 1
                    continue
                seen.add(entry.get("index"))
                outcome = _verdict_suggestion(chunk, entry, worker.model_id)
                if isinstance(outcome, str):
                    dropped[outcome] += 1
                else:
                    suggestions.append(outcome)

    return SuggestResult(
        suggestions=suggestions,
        dropped=dict(dropped),
        failed_chunks=failed_chunks,
        usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )
