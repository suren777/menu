"""Nutrition tests: the published-nutrition parser."""

import json
from pathlib import Path

from menu.nutrition.derive import parse_published_nutrition

FIXTURE = Path(__file__).parent / "fixtures" / "published_nutrition.json"


def test_parse_published_nutrition_bbc() -> None:
    """BBC spells values out ("22 grams fat", "0.61 milligram of
    sodium")."""
    data = json.loads(FIXTURE.read_text())
    assert parse_published_nutrition({"nutrition": data["bbc_good_food"]}) == {
        "energy_kcal": 681.0,
        "protein_g": 16.0,
        "fat_g": 22.0,
        "saturated_fat_g": 11.0,
        "carbohydrate_g": 98.0,
        "sugars_g": 37.0,
        "fibre_g": 4.0,
        "sodium_mg": 0.61,
    }


def test_parse_published_nutrition_king_arthur() -> None:
    """King Arthur spells values compactly ("20g") and publishes keys
    outside the fixed set (cholesterol, trans fat, servingSize),
    which are ignored."""
    data = json.loads(FIXTURE.read_text())
    assert parse_published_nutrition({"nutrition": data["king_arthur"]}) == {
        "energy_kcal": 150.0,
        "protein_g": 4.0,
        "fat_g": 6.0,
        "saturated_fat_g": 1.5,
        "carbohydrate_g": 20.0,
        "sugars_g": 3.0,
        "fibre_g": 2.0,
        "sodium_mg": 200.0,
    }


def test_parse_published_nutrition_missing_block() -> None:
    """Recipes without a nutrition block parse to nothing."""
    assert parse_published_nutrition({}) == {}
