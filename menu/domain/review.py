"""Review types: the item shapes the review-input builders produce and
the LLM client classifies.

Pure data — no database, no HTTP. Models only suggest, per the plan's
principle 7: these carry the context the model may use, never the
resolution itself.
"""

from dataclasses import dataclass
from typing import Literal

InputSet = Literal["queue", "audit", "pair", "food_ref"]


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
