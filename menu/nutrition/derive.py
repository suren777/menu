"""Recipe nutrition: published totals and derived-from-ingredients.

Published values are parsed from the JSON-LD nutrition block the
sites already ship ("350 calories", "12 g"); derived values are
summed from ingredient lines and their confirmed nutrition
references. The fixed nutrient set is what BBC and King Arthur
publish, so validation compares like with like.
"""

import re
from typing import Any

_LEADING_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# JSON-LD nutrition keys -> our fixed nutrient vocabulary. Keys
# outside it (cholesterol, trans fat, servingSize) are ignored.
_PUBLISHED_KEYS = {
    "calories": "energy_kcal",
    "proteinContent": "protein_g",
    "fatContent": "fat_g",
    "saturatedFatContent": "saturated_fat_g",
    "carbohydrateContent": "carbohydrate_g",
    "sugarContent": "sugars_g",
    "fiberContent": "fibre_g",
    "sodiumContent": "sodium_mg",
}


def parse_published_nutrition(data: dict[str, Any]) -> dict[str, float]:
    """The recipe's published nutrition totals, parsed from its
    JSON-LD nutrition block ("350 calories", "12 g", "0.61 milligram
    of sodium"). Empty when the recipe publishes none."""
    block = data.get("nutrition")
    if not isinstance(block, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, nutrient in _PUBLISHED_KEYS.items():
        value = block.get(key)
        if isinstance(value, str) and (match := _LEADING_NUMBER.search(value)):
            parsed[nutrient] = float(match.group())
    return parsed
