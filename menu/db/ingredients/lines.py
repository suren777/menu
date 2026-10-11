"""Line storage: parsing a recipe's ingredient lines and storing
recipe_ingredients rows."""

import logging
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    Recipe,
    RecipeIngredient,
    RefFood,
)
from menu.db.ingredients import repository, seed
from menu.db.ingredients.grams import _line_grams
from menu.ingest.ingredients import (
    canonical_name,
    line_needs_review,
    name_needs_review,
    parse_line,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from menu.ingest.ingredients import ParsedLine
    from menu.ingest.registry import SiteConfig

logger = logging.getLogger(__name__)

_YIELD_NUMBER = re.compile(r"\d+")
_OR_ALTERNATIVE = re.compile(r"\bor\b", re.IGNORECASE)


def store_recipe_ingredients(
    recipe_id: int,
    recipe_data: dict[str, Any],
    site: SiteConfig,
    session: Session,
) -> None:
    """Parse a recipe's ingredient lines and store recipe_ingredients rows.

    Runs in the caller's transaction: the pipeline writes the recipe
    and its lines in one commit, so a crash cannot leave a recipe
    without lines. Re-running a recipe replaces its previous rows, so
    reparsing is idempotent. The recipe's servings are parsed from
    recipeYield and the raw yield is kept as yield_text. A line that
    fails to parse is stored with parse_status 'failed' and no term:
    bad lines must not stop a recipe (or a crawl), mirroring the
    pipeline's per-URL handling, but they are data too.
    """
    lines = recipe_data.get("recipeIngredient", [])
    if not isinstance(lines, list):
        logger.warning("recipeIngredient is not a list for recipe %s", recipe_id)
        return

    record = session.get(Recipe, recipe_id)
    if record is None:
        logger.warning("No recipe %s stored; ingredients skipped", recipe_id)
        return

    record.servings = _servings_from_yield(recipe_data.get("recipeYield"))
    record.yield_text = _yield_text(recipe_data.get("recipeYield"))

    session.execute(
        delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
    )

    # Line overrides read the seeds and accepted suggestions once per
    # call, not once per line.
    overrides = seed.line_overrides()

    for position, raw in enumerate(lines):
        if not isinstance(raw, str):
            logger.warning(
                "Skipping non-string ingredient for recipe %s: %r", recipe_id, raw
            )
            continue
        try:
            parsed = parse_line(raw, site)
        except Exception as exc:  # noqa: BLE001 - a bad line must not stop the recipe
            logger.warning(
                "Storing failed line %r for recipe %s: %s: %s",
                raw,
                recipe_id,
                type(exc).__name__,
                exc,
            )
            session.add(
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=position,
                    raw_text=raw,
                    parse_status="failed",
                )
            )
            continue
        override = overrides.get(" ".join(raw.lower().split()))
        alt_parsed = None if parsed.alternatives else _split_or_alternative(raw, site)
        line_review = override is None and line_needs_review(raw, parsed, alt_parsed)
        term = _resolve_term(
            override if override is not None else parsed.name,
            parsed.fdc_id,
            session,
        )
        # Stored as the heuristic says; a seeded term overrides it in
        # effective_ingredient_id, so a later seed still wins.
        needs_review = line_review
        ingredient_id = repository.effective_ingredient_id(needs_review, term)
        grams = grams_max = None
        grams_source = None
        conversion_id = None
        if ingredient_id is not None:
            grams, grams_max, grams_source, conversion_id = _line_grams(
                parsed.quantity,
                parsed.quantity_max,
                parsed.dimension,
                parsed.base_unit,
                parsed.original_unit,
                parsed.preparation,
                parsed.size,
                ingredient_id,
                session,
            )
        line = RecipeIngredient(
            recipe_id=recipe_id,
            position=position,
            raw_text=raw,
            term_id=term.id if term is not None else None,
            needs_review=needs_review,
            quantity=parsed.quantity,
            quantity_max=parsed.quantity_max,
            dimension=parsed.dimension,
            base_unit=parsed.base_unit,
            original_quantity_text=parsed.original_quantity_text,
            original_unit=parsed.original_unit,
            preparation=parsed.preparation,
            size=parsed.size,
            note=parsed.note,
            optional=parsed.optional,
            parse_confidence=parsed.parse_confidence,
            grams=grams,
            grams_max=grams_max,
            grams_source=grams_source,
            conversion_id=conversion_id,
        )
        session.add(line)
        session.flush()  # the row's id is needed by the alternative rows
        for alternative in parsed.alternatives:
            alternative_term = _resolve_term(alternative, None, session)
            session.add(
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=position,
                    raw_text=raw,
                    term_id=(
                        alternative_term.id if alternative_term is not None else None
                    ),
                    alternative_of=line.id,
                )
            )
        # The parser handles bare "X or Y" name alternatives itself,
        # but drops the second option of "2 tsp vanilla or 1 tsp
        # essential oil" into the note. Split it off here so it is
        # still kept as an alternative row.
        if alt_parsed is not None:
            alternative_term = _resolve_term(
                alt_parsed.name, alt_parsed.fdc_id, session
            )
            session.add(
                RecipeIngredient(
                    recipe_id=recipe_id,
                    position=position,
                    raw_text=raw,
                    term_id=(
                        alternative_term.id if alternative_term is not None else None
                    ),
                    quantity=alt_parsed.quantity,
                    quantity_max=alt_parsed.quantity_max,
                    dimension=alt_parsed.dimension,
                    base_unit=alt_parsed.base_unit,
                    alternative_of=line.id,
                )
            )


def _split_or_alternative(raw: str, site: SiteConfig) -> ParsedLine | None:
    """The second option of "2 tsp vanilla or 1 tsp essential oil",
    parsed on its own. None when the line has no unhandled "or", when
    the "or" sits inside brackets ("(or 3 small)" is a parenthetical,
    not an alternative) or when the remainder has no ingredient name
    ("or to taste", "or more if needed") — storing those would flood
    the review queue with nameless rows."""
    parts = _OR_ALTERNATIVE.split(raw, maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return None
    if parts[0].count("(") > parts[0].count(")"):
        return None
    try:
        alt = parse_line(parts[1].strip(), site)
    except Exception as exc:  # noqa: BLE001 - a bad line must not stop the recipe
        logger.warning(
            "Skipping alternative of %r: %s: %s", raw, type(exc).__name__, exc
        )
        return None
    if alt.name is None:
        return None
    return alt


def _resolve_term(
    name: str | None, fdc_id: int | None, session: Session
) -> IngredientTerm | None:
    """The term a parsed name resolves to, in the order trusted:

    1. A curated line override already decided the name before this
       call, so a seeded term match carries the variant ("whole milk"
       -> milk, variant "whole").
    2. An existing term is used as-is, whatever its resolution: a
       seed, an auto guess, a review entry or not_food.
    3. A new term: 'review' (no concept) when the name looks mangled
       (name_needs_review); otherwise 'auto', pointing at the concept
       of the same name — created here when missing, with the parser's
       fdc candidate as a suggestion.

    A line the parser could not name at all gets no term: it is
    stored anyway and reviewed later.
    """
    if name is None:
        return None
    canonical = canonical_name(name)
    term = session.scalar(
        select(IngredientTerm).where(IngredientTerm.term == canonical)
    )
    if term is not None:
        return term
    if name_needs_review(name):
        term = IngredientTerm(
            term=canonical, ingredient_id=None, variant=None, resolution="review"
        )
        session.add(term)
        session.flush()
        return term
    ingredient = session.scalar(select(Ingredient).where(Ingredient.name == canonical))
    if ingredient is None:
        ingredient = Ingredient(name=canonical, origin="auto")
        session.add(ingredient)
        session.flush()
        if fdc_id is not None:
            _seed_candidate(ingredient.id, str(fdc_id), session)
    term = IngredientTerm(
        term=canonical, ingredient_id=ingredient.id, variant=None, resolution="auto"
    )
    session.add(term)
    session.flush()
    return term


def _seed_candidate(ingredient_id: int, source_food_id: str, session: Session) -> None:
    """The parser's fdc_id as a candidate row (role NULL, status
    'candidate'): a suggestion for the --ref-review report, never used
    for conversion or nutrition. Only called for an ingredient created
    by this very parse, so it cannot double an existing row — the
    (ingredient, food, role) expression index would refuse that."""
    food = session.execute(
        select(RefFood.id, RefFood.source_id)
        .join(FoodSource, FoodSource.id == RefFood.source_id)
        .where(FoodSource.name == "fdc", RefFood.source_food_id == source_food_id)
    ).first()
    if food is not None:
        ref_food_id, source_id = food
        session.add(
            IngredientFoodRef(
                ingredient_id=ingredient_id,
                ref_food_id=ref_food_id,
                source_id=source_id,
                status="candidate",
                origin="parser",
            )
        )


def _yield_text(value: Any) -> str | None:
    """The raw recipeYield as text ("Serves 12"); a list takes the
    first entry, like _servings_from_yield."""
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    return None if value is None else str(value)


def _servings_from_yield(value: Any) -> int | None:
    """Upper-end integer in a recipeYield: 6, "Serves 12", "Makes 1
    loaf", "Servings: 3 to 4" -> 4 — range quantities buy the upper
    end, like ingredient lines. King Arthur yields are lists; the
    first entry wins."""
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    matches = _YIELD_NUMBER.findall(str(value))
    return int(matches[-1]) if matches else None
