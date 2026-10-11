"""LLM review tests: never touch the network.

The fake client returns canned JSON; FireworksClient is exercised
through a monkeypatched requests.post, so nothing leaves the process.
"""

import json
from typing import Any

import pytest
import requests

from menu.ingest import llm_review
from menu.ingest.llm_review import (
    FireworksClient,
    FoodCandidate,
    InputSet,
    LLMError,
    ReviewItem,
    SuggestResult,
    Usage,
    suggest,
)


class FakeClient:
    """One canned payload per complete() call; records every call."""

    model_id = "fake/model"

    def __init__(self, payloads: list[str | Exception]) -> None:
        self.payloads = list(payloads)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.max_tokens: list[int] = []

    def complete(
        self, system: str, user: str, schema: dict[str, Any], *, max_tokens: int
    ) -> tuple[str, Usage]:
        self.calls.append((system, user, schema))
        self.max_tokens.append(max_tokens)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload, Usage(prompt_tokens=10, completion_tokens=5)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


def _payload(verdicts: list[Any]) -> str:
    return json.dumps({"items": verdicts})


def _run(item: ReviewItem, verdicts: list[Any]) -> SuggestResult:
    return suggest([item], client=FakeClient([_payload(verdicts)]))


def _queue_item(**overrides: Any) -> ReviewItem:
    fields: dict[str, Any] = {
        "input": "queue",
        "subject": "dark chocolate",
        "lines": ("70g dark chocolate",),
        "known_concepts": ("chocolate",),
    }
    fields.update(overrides)
    return ReviewItem(**fields)


def test_120_items_chunk_into_three_requests() -> None:
    payload = _payload([{"index": 0, "kind": "ok", "reason": "right"}])
    fake = FakeClient([payload, payload, payload])
    items = [ReviewItem(input="queue", subject=f"term {n}") for n in range(120)]
    result = suggest(items, client=fake, chunk_size=50)
    assert len(fake.calls) == 3
    assert len(result.suggestions) == 3
    first = fake.calls[0][1]
    assert "0. term: term 0" in first
    assert "49. term: term 49" in first
    assert "term 50" not in first
    last = fake.calls[2][1]
    assert "19. term: term 119" in last
    assert "20." not in last


def test_chunk_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        suggest([], client=FakeClient([]), chunk_size=0)


def test_inputs_are_grouped_into_separate_requests() -> None:
    fake = FakeClient([_payload([]), _payload([])])
    items = [
        ReviewItem(input="queue", subject="butter"),
        ReviewItem(input="pair", subject="rocket | arugula"),
        ReviewItem(input="queue", subject="cream"),
    ]
    result = suggest(items, client=fake)
    assert len(fake.calls) == 2
    assert fake.calls[0][1] == "0. term: butter\n1. term: cream"
    assert fake.calls[1][1] == "0. pair: rocket | arugula"
    assert result.suggestions == []


def test_user_message_carries_the_item_context() -> None:
    fake = FakeClient([_payload([])])
    suggest(
        [
            ReviewItem(
                input="food_ref",
                subject="icing sugar",
                candidates=(FoodCandidate("fdc", "175079", "Sugars, powdered"),),
                known_concepts=("sugar",),
            )
        ],
        client=fake,
    )
    assert fake.calls[0][1] == (
        "0. ingredient: icing sugar\n"
        "   candidate fdc 175079: Sugars, powdered\n"
        "   known concepts: sugar"
    )


def test_numbers_not_in_the_raw_text_are_dropped() -> None:
    result = _run(
        _queue_item(),
        [
            {
                "index": 0,
                "kind": "line_override",
                "line": "70g dark chocolate",
                "name": "100g dark chocolate",
                "reason": "fix the amount",
            }
        ],
    )
    assert result.dropped == {"numbers": 1}
    assert result.suggestions == []


def test_numbers_from_the_raw_line_survive() -> None:
    result = _run(
        _queue_item(),
        [
            {
                "index": 0,
                "kind": "line_override",
                "line": "70g dark chocolate",
                "name": "70g chocolate",
                "reason": "brand stripped",
            }
        ],
    )
    assert result.dropped == {}
    (suggestion,) = result.suggestions
    assert suggestion.input == "queue"
    assert suggestion.kind == "line_override"
    assert suggestion.subject == "dark chocolate"
    assert suggestion.proposal == {
        "line": "70g dark chocolate",
        "name": "70g chocolate",
    }
    assert suggestion.evidence == {"reason": "brand stripped"}
    assert suggestion.model == {"llm": "fake/model"}
    assert suggestion.status == "pending"


def test_alias_target_must_be_known_or_new() -> None:
    unknown = {
        "index": 0,
        "kind": "alias",
        "target": "cacao",
        "variant": None,
        "new": False,
        "reason": "r",
    }
    assert _run(_queue_item(), [unknown]).dropped == {"target": 1}

    new = {
        "index": 0,
        "kind": "alias",
        "target": "cacao",
        "variant": None,
        "new": True,
        "reason": "r",
    }
    result = _run(_queue_item(), [new])
    assert result.dropped == {}
    assert result.suggestions[0].proposal == {
        "term": "dark chocolate",
        "target": "cacao",
        "variant": None,
        "new": True,
    }

    known = {
        "index": 0,
        "kind": "alias",
        "target": "chocolate",
        "variant": None,
        "new": False,
        "reason": "r",
    }
    result = _run(_queue_item(), [known])
    assert result.suggestions[0].proposal == {
        "term": "dark chocolate",
        "target": "chocolate",
        "variant": None,
        "new": False,
    }


def test_variant_proposals_must_target_a_known_concept() -> None:
    unknown = {
        "index": 0,
        "kind": "variant",
        "target": "cacao",
        "variant": "dark",
        "new": True,
        "reason": "r",
    }
    assert _run(_queue_item(), [unknown]).dropped == {"target": 1}

    known = {
        "index": 0,
        "kind": "variant",
        "target": "chocolate",
        "variant": "dark",
        "reason": "r",
    }
    result = _run(_queue_item(), [known])
    assert result.dropped == {}
    assert result.suggestions[0].proposal == {
        "term": "dark chocolate",
        "target": "chocolate",
        "variant": "dark",
    }


def test_food_ref_proposal_must_pick_a_listed_candidate() -> None:
    item = ReviewItem(
        input="food_ref",
        subject="icing sugar",
        candidates=(FoodCandidate("fdc", "175079", "Sugars, powdered"),),
    )
    unknown = {
        "index": 0,
        "kind": "food_ref",
        "source": "cnf",
        "source_food_id": "42",
        "reason": "r",
    }
    assert _run(item, [unknown]).dropped == {"candidate": 1}

    picked = {
        "index": 0,
        "kind": "food_ref",
        "source": "fdc",
        "source_food_id": "175079",
        "reason": "same food",
    }
    result = _run(item, [picked])
    assert result.suggestions[0].proposal == {
        "ingredient": "icing sugar",
        "source": "fdc",
        "source_food_id": "175079",
    }

    none = {"index": 0, "kind": "none", "reason": "none fits"}
    result = _run(item, [none])
    assert (result.suggestions[0].kind, result.suggestions[0].proposal) == ("ok", {})


def test_override_line_must_be_verbatim() -> None:
    verdict = {
        "index": 0,
        "kind": "line_override",
        "line": "some other line",
        "name": "chocolate",
        "reason": "r",
    }
    assert _run(_queue_item(), [verdict]).dropped == {"line": 1}


def test_kind_and_index_are_validated() -> None:
    wrong_kind = {"index": 0, "kind": "food_ref", "reason": "r"}
    assert _run(_queue_item(), [wrong_kind]).dropped == {"kind": 1}

    out_of_range = {"index": 5, "kind": "ok", "reason": "r"}
    assert _run(_queue_item(), [out_of_range]).dropped == {"kind": 1}

    string_index = {"index": "0", "kind": "ok", "reason": "r"}
    assert _run(_queue_item(), [string_index]).dropped == {"kind": 1}

    missing_target = {"index": 0, "kind": "alias", "new": False, "reason": "r"}
    assert _run(_queue_item(), [missing_target]).dropped == {"kind": 1}

    result = _run(_queue_item(), ["not an object"])
    assert result.dropped == {"kind": 1}


def test_pair_verdicts_derive_term_and_target_from_the_subject() -> None:
    same = ReviewItem(
        input="pair", subject="rocket | arugula", known_concepts=("arugula",)
    )
    result = _run(
        same, [{"index": 0, "kind": "alias", "new": False, "reason": "regional names"}]
    )
    assert result.suggestions[0].proposal == {
        "term": "rocket",
        "target": "arugula",
        "variant": None,
        "new": False,
    }

    whole = ReviewItem(
        input="pair", subject="whole milk | milk", known_concepts=("milk",)
    )
    result = _run(
        whole, [{"index": 0, "kind": "variant", "variant": "whole", "reason": "r"}]
    )
    assert result.suggestions[0].proposal == {
        "term": "whole milk",
        "target": "milk",
        "variant": "whole",
    }

    different = ReviewItem(input="pair", subject="coriander | ground coriander")
    result = _run(different, [{"index": 0, "kind": "ok", "reason": "herb vs spice"}])
    assert result.suggestions[0].proposal == {}


def test_empty_and_invalid_replies_are_failed_chunks() -> None:
    payload = _payload([{"index": 0, "kind": "ok", "reason": "right"}])
    fake = FakeClient(["", "not json", '{"no": 1}', LLMError("boom"), payload])
    items = [_queue_item(subject=f"term {n}") for n in range(5)]
    result = suggest(items, client=fake, chunk_size=1)
    assert result.failed_chunks == 4
    assert len(result.suggestions) == 1
    assert result.dropped == {}
    assert result.usage == Usage(prompt_tokens=40, completion_tokens=20)


def test_schemas_restrict_kinds_per_input_set() -> None:
    expected: dict[InputSet, list[str]] = {
        "queue": ["alias", "line_override", "not_food", "ok", "variant"],
        "audit": ["alias", "line_override", "not_food", "ok", "variant"],
        "pair": ["alias", "ok", "variant"],
        "food_ref": ["food_ref", "none"],
    }
    for input_set, kinds in expected.items():
        schema = llm_review._SCHEMAS[input_set]
        verdict = schema["properties"]["items"]["items"]
        assert verdict["properties"]["kind"]["enum"] == kinds
        assert set(verdict["required"]) == {
            "index",
            "kind",
            "reason",
            *llm_review._VERDICT_FIELDS[input_set],
        }


def test_system_prompts_carry_the_language_only_rules() -> None:
    for system in llm_review._SYSTEM_PROMPTS.values():
        assert "Never output quantities, weights or nutrients" in system
        assert '"Related" is not "same"' in system
        assert "UK and US names are both in use" in system


def _reply(content: str) -> FakeResponse:
    return FakeResponse(
        {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
    )


def test_fireworks_client_builds_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured.update(url=url, headers=kwargs["headers"], body=kwargs["json"])
        return _reply('{"items": []}')

    monkeypatch.delenv("MENU_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MENU_LLM_MODEL", raising=False)
    monkeypatch.setenv("FIREWORKS_API_KEY", "key-123")
    monkeypatch.setattr(requests, "post", fake_post)

    content, usage = FireworksClient().complete(
        "system text", "user text", {"type": "object"}, max_tokens=4000
    )

    assert content == '{"items": []}'
    assert usage == Usage(prompt_tokens=11, completion_tokens=7)
    assert captured["url"] == f"{llm_review.DEFAULT_BASE_URL}/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer key-123"}
    assert captured["body"] == {
        "model": llm_review.DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": "system text"},
            {"role": "user", "content": "user text"},
        ],
        "max_tokens": 4000,
        "reasoning_effort": "low",
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "menu_review",
                "strict": True,
                "schema": {"type": "object"},
            },
        },
    }


def test_fireworks_client_honours_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured.update(url=url, body=kwargs["json"])
        return _reply("{}")

    monkeypatch.setenv("MENU_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("MENU_LLM_MODEL", "local/model")
    monkeypatch.setenv("FIREWORKS_API_KEY", "key")
    monkeypatch.setattr(requests, "post", fake_post)

    FireworksClient().complete("s", "u", {}, max_tokens=4000)
    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert captured["body"]["model"] == "local/model"


def test_fireworks_client_without_api_key_raises_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    with pytest.raises(LLMError, match="FIREWORKS_API_KEY"):
        FireworksClient().complete("s", "u", {}, max_tokens=4000)


def test_fireworks_client_reports_http_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", "key")

    def refusing_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse({}, status_code=503)

    monkeypatch.setattr(requests, "post", refusing_post)
    with pytest.raises(LLMError, match="503"):
        FireworksClient().complete("s", "u", {}, max_tokens=4000)


def test_fireworks_client_maps_missing_content_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", "key")
    payload = {"choices": [{"message": {"content": None}}], "usage": {}}

    def length_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(payload)

    monkeypatch.setattr(requests, "post", length_post)
    content, usage = FireworksClient().complete("s", "u", {}, max_tokens=4000)
    assert content == ""
    assert usage == Usage(0, 0)


def test_max_tokens_scale_with_the_chunk() -> None:
    payload = _payload([])
    fake = FakeClient([payload, payload])
    items = [ReviewItem(input="queue", subject=f"term {n}") for n in range(60)]
    suggest(items, client=fake, chunk_size=50)
    assert fake.max_tokens == [1_000 + 150 * 50, 1_000 + 150 * 10]


def test_food_ref_ok_that_names_a_candidate_is_dropped() -> None:
    item = ReviewItem(
        input="food_ref",
        subject="icing sugar",
        candidates=(FoodCandidate("fdc", "169656", "Sugars, powdered"),),
    )
    result = _run(
        item,
        [
            {
                "index": 0,
                "kind": "none",
                "source": "fdc",
                "source_food_id": "169656",
                "reason": "same as powdered sugar",
            }
        ],
    )
    assert result.suggestions == []
    assert result.dropped == {"kind": 1}


def test_only_the_first_verdict_per_item_counts() -> None:
    result = _run(
        _queue_item(),
        [
            {"index": 0, "kind": "ok", "reason": "right"},
            {"index": 0, "kind": "not_food", "reason": "changed its mind"},
        ],
    )
    assert [s.kind for s in result.suggestions] == ["ok"]
    assert result.dropped == {"kind": 1}
