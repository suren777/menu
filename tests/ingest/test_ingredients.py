"""parse_line against the edge-case lines from the cached BBC pages.

Every line below was taken from a saved .cache page (AGENTS.md: no
live requests) and the parser runs locally.
"""

import pytest

from menu.ingest.ingredients import parse_line
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD

US = BBC_GOOD_FOOD.model_copy(update={"unit_system": "us"})


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
            {"name": "rosewater extract", "alternatives": ("vanilla extract",)},
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
    ],
)
def test_parse_edge_cases(line: str, expected: dict[str, object]) -> None:
    parsed = parse_line(line, BBC_GOOD_FOOD)
    for field, value in expected.items():
        assert getattr(parsed, field) == value, field


def test_cup_is_ambiguous_between_systems() -> None:
    """The site's unit system decides what a cup means."""
    imperial = parse_line("1 cup milk", BBC_GOOD_FOOD)
    us = parse_line("1 cup milk", US)

    assert imperial.quantity == pytest.approx(284.130625)
    assert us.quantity == pytest.approx(236.5882365)


def test_optional_defaults_to_false() -> None:
    parsed = parse_line("25g butter", BBC_GOOD_FOOD)

    assert parsed.optional is False
