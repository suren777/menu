"""parse_line against the edge-case lines from the cached BBC pages.

Every line below was taken from a saved .cache page (AGENTS.md: no
live requests) and the parser runs locally.
"""

import json
from pathlib import Path
from typing import cast

import pytest

from menu.db.ingredients.actions import _split_or_alternative
from menu.ingest.ingredients import (
    canonical_name,
    line_needs_review,
    name_needs_review,
    parse_line,
)
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

US = BBC_GOOD_FOOD.model_copy(update={"unit_system": "us"})

FIXTURE = Path(__file__).parent / "fixtures" / "bbc_lines.json"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # words inflect must leave alone
        ("asparagus", "asparagus"),
        ("couscous", "couscous"),
        ("hummus", "hummus"),
        ("molasses", "molasses"),
        ("cassis", "cassis"),
        ("milk", "milk"),
        # real plurals
        ("eggs", "egg"),
        ("raspberries", "raspberry"),
        ("cherry tomatoes", "cherry tomato"),
        ("bay leaves", "bay leaf"),
        ("anchovy fillets", "anchovy fillet"),
        ("golden caster sugar", "golden caster sugar"),
    ],
)
def test_canonical_name_singularises(raw: str, expected: str) -> None:
    """Proper singularisation: no more "asparagu", "raspberrie",
    "cherry tomatoe" from the old strip-a-trailing-s rule."""
    assert canonical_name(raw) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("milk", False),
        ("warm milk", False),
        ("smooth lotus biscoff spread", False),
        # long modifier chains and unsplit conjunctions
        ("whole blanched almonds roughly chopped", True),
        ("good-quality candied orange and lemon peel", True),
        ("milk or dark chocolate", True),
    ],
)
def test_name_needs_review(name: str, expected: bool) -> None:
    """The review-queue heuristic: names the parser likely mangled
    must not become canonical ingredients unchecked."""
    assert name_needs_review(name) == expected


def test_rosewater_or_vanilla_parser_quirk() -> None:
    """The parser turns "rosewater or vanilla extract" into the name
    "rosewater extract" — a known quirk. The seed alias maps it back
    to rosewater; the alternative keeps vanilla extract."""
    parsed = parse_line("rosewater or vanilla extract", BBC_GOOD_FOOD)

    assert parsed.name is not None
    assert parsed.name.startswith("rosewater")
    assert parsed.alternatives == ("vanilla extract",)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param(
            "240ml warm milk",
            {
                "name": "warm milk",
                "dimension": "volume",
                "quantity": 240.0,
                "base_unit": "ml",
                "preparation": None,
            },
            id="state-fused-with-name",
        ),
        pytest.param(
            "25g butter, softened",
            {
                "name": "butter",
                "preparation": "softened",
                "dimension": "mass",
                "quantity": 25.0,
                "base_unit": "g",
            },
            id="preparation-separate",
        ),
        pytest.param(
            "2 eggs, beaten",
            {
                "name": "egg",
                "preparation": "beaten",
                "dimension": "count",
                "quantity": 2.0,
                "base_unit": "piece",
            },
            id="count-unit",
        ),
        pytest.param(
            "½ tsp vanilla extract",
            {
                "dimension": "volume",
                "quantity": pytest.approx(2.959694010416667),
                "base_unit": "ml",
            },
            id="unicode-fraction",
        ),
        pytest.param(
            "2-3 large onions, sliced",
            {
                "name": "onion",
                "size": "large",
                "preparation": "sliced",
                "quantity": 2.0,
                "quantity_max": 3.0,
                "dimension": "count",
            },
            id="range-and-size",
        ),
        pytest.param(
            "100g butter, plus extra for the tin",
            {"quantity": 100.0, "base_unit": "g", "note": "plus extra for the tin"},
            id="plus-extra",
        ),
        pytest.param(
            "45g desiccated coconut (optional)",
            {"optional": True, "quantity": 45.0, "base_unit": "g"},
            id="optional",
        ),
        pytest.param(
            "oil, for proving",
            {"quantity": None, "dimension": None, "note": "for proving"},
            id="no-quantity",
        ),
        pytest.param(
            "rosewater or vanilla extract",
            {"alternatives": ("vanilla extract",)},
            id="alternatives",
        ),
        pytest.param(
            "2 slices ham",
            {"dimension": "count", "quantity": 2.0, "base_unit": "slice"},
            id="named-count-unit",
        ),
        pytest.param(
            "5 medium eggs",
            {"size": "medium", "name": "egg", "quantity": 5.0},
            id="size-modifier",
        ),
        pytest.param(
            "sea salt flakes, to serve",
            {"quantity": None, "note": "to serve"},
            id="to-serve",
        ),
        pytest.param(
            "1 lb butter",
            {"dimension": "mass", "quantity": pytest.approx(453.592), "base_unit": "g"},
            id="lb-to-g",
        ),
        pytest.param(
            "1 orange, zested and juiced",
            {"name": "orange", "dimension": "count", "quantity": 1.0},
            id="one-purchase-several-uses",
        ),
        pytest.param(
            "1 stick butter",
            {"dimension": "count", "quantity": 1.0, "base_unit": "stick"},
            id="stick-butter",
        ),
        pytest.param(
            "pink and yellow food colouring gels",
            {
                "name": "pink food colouring gel",
                "alternatives": ("yellow food colouring gel",),
            },
            id="and-captured-as-alternatives",
        ),
    ],
)
def test_parse_edge_cases(line: str, expected: dict[str, object]) -> None:
    parsed = parse_line(line, BBC_GOOD_FOOD)
    for field, value in expected.items():
        assert getattr(parsed, field) == value, field


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("pink and yellow food colouring gels", True),
        ("200g good-quality candied orange and lemon peel", True),
        ("rosewater or vanilla extract", False),
        ("1 tsp salt or to taste", False),
        ("1 tbsp milk, or more if needed", False),
        ("1 orange, zested and juiced", False),
        ("2 large eggs (or 3 small)", False),
        ("2 tsp vanilla or 1 tsp essential oil", False),
        ("100g butter, plus extra for the tin", False),
        ("250g bread flour plus 20g for the yukone and extra for dusting", False),
    ],
)
def test_line_needs_review(line: str, expected: bool) -> None:
    """A conjunction the parse dropped sends the line to the review
    queue; one accounted for by the name, a sidecar, a bracketed
    parenthetical or a stored alternative does not."""
    parsed = parse_line(line, BBC_GOOD_FOOD)
    or_alternative = (
        None if parsed.alternatives else _split_or_alternative(line, BBC_GOOD_FOOD)
    )
    assert line_needs_review(line, parsed, or_alternative) is expected


def test_cup_is_ambiguous_between_systems() -> None:
    """The site's unit system decides what a cup means."""
    imperial = parse_line("1 cup milk", BBC_GOOD_FOOD)
    us = parse_line("1 cup milk", US)

    assert imperial.quantity == pytest.approx(284.130625)
    assert us.quantity == pytest.approx(236.5882365)


def test_optional_defaults_to_false() -> None:
    parsed = parse_line("25g butter", BBC_GOOD_FOOD)

    assert parsed.optional is False


def _fixture_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = json.loads(FIXTURE.read_text())
    return cases


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["name"])
def test_real_cached_lines_parse(case: dict[str, object]) -> None:
    """Every ingredient line of the cached BBC recipes parses without
    raising, verbatim — double spaces, no commas, exactly as stored.
    The earlier test table used cleaned-up lines; these are the real
    fixture."""
    lines = cast("list[str]", case["lines"])
    for line in lines:
        parsed = parse_line(line, BBC_GOOD_FOOD)
        assert parsed.raw_text == line
