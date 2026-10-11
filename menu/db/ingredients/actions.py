"""Ingredient actions: store parsed lines, aggregate a shopping list."""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientConversion,
    IngredientFoodRef,
    IngredientTerm,
    Recipe,
    RecipeIngredient,
    RefFood,
)
from menu.db.ingredients import repository, seed
from menu.db.ingredients.seed import LINE_OVERRIDES, seed_ingredient_data
from menu.ingest.ingredients import (
    canonical_name,
    line_needs_review,
    name_needs_review,
    parse_line,
)
from menu.ingest.units import (
    COUNT,
    MASS,
    VOLUME,
    count_to_mass,
    singular,
    unit_ml,
    volume_to_mass,
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
        override = LINE_OVERRIDES.get(" ".join(raw.lower().split()))
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


# Household-unit spellings the parser emits that FDC portion rows
# spell differently.
_UNIT_ALIASES = {
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
}


def _portion_row(
    conversions: list[IngredientConversion],
    unit: str | None,
    prefer: str | None = None,
) -> IngredientConversion | None:
    """The `portion` row spoken as `unit` ("tbsp", "medium", "stick").

    Egg's cup row is "cup (4.86 large eggs)", so a unit followed by a
    parenthetical counts as the unit too. When `prefer` is given (the
    line's preparation or size), rows whose modifier says so come
    first.
    """
    if unit is None:
        return None
    matches = [
        row
        for row in conversions
        if row.kind == "portion"
        and row.unit is not None
        and (row.unit == unit or row.unit.startswith(f"{unit} ("))
    ]
    if prefer is not None:
        for candidate in matches:
            if candidate.modifier == prefer:
                return candidate
    return matches[0] if matches else None


def _factor_row(
    conversions: list[IngredientConversion], kind: str
) -> IngredientConversion | None:
    """The ingredient's density or unit-weight row, seed rows before
    derived ones."""
    for row in sorted(conversions, key=lambda r: r.origin != "seed"):
        if row.kind == kind:
            return row
    return None


def _portion_density(
    conversions: list[IngredientConversion], prefer: str | None
) -> tuple[float, IngredientConversion] | None:
    """(g/ml, portion row) implied by a volumetric portion: salt's
    "tsp = 6 g" row is 1.22 g/ml, so "a pinch" converts.
    Preparation-matched rows come first."""
    for row in sorted(conversions, key=lambda r: r.modifier != prefer):
        if row.kind != "portion" or row.unit is None:
            continue
        ml = unit_ml(singular(row.unit))
        if ml is None:
            continue
        return row.grams / ml, row
    return None


def to_grams(
    quantity: float | None,
    quantity_max: float | None,
    dimension: str | None,
    base_unit: str | None,
    original_unit: str | None,
    preparation: str | None,
    size: str | None,
    conversions: list[IngredientConversion],
) -> tuple[float | None, float | None, str | None, int | None]:
    """(grams, grams_max, provenance, conversion row id) for one line.

    Precedence over the ingredient's conversion rows, seed rows
    before derived ones — a conversion is never guessed:

    - mass is already grams (no lookup: provenance and row NULL).
    - volume converts through the portion row for the line's unit
      ("1 cup egg" -> its 243 g row), preferring a modifier that
      matches the line's preparation; else the seeded density; else a
      derived one; else the density a volumetric portion row implies
      (salt's "tsp = 6 g"), recorded against that row.
    - count converts through the portion row matching the line's size
      ("large" egg -> the large row) or named count unit ("2 sticks
      butter" -> the stick row); else the seeded unit weight; else a
      derived one; else the "medium" portion row.
    """
    if quantity is None:
        return None, None, None, None
    if dimension == MASS:
        return quantity, quantity_max, None, None
    if dimension == VOLUME:
        name = singular(_UNIT_ALIASES.get(original_unit or "", original_unit or ""))
        portion = _portion_row(conversions, name, preparation)
        if portion is not None:
            factor, conversion_id, origin = (
                portion.grams,
                portion.id,
                portion.origin,
            )
        else:
            density = _factor_row(conversions, "density")
            if density is not None:
                factor, conversion_id, origin = (
                    density.grams,
                    density.id,
                    density.origin,
                )
            else:
                implied = _portion_density(conversions, preparation)
                if implied is None:
                    return None, None, None, None
                factor, row = implied
                conversion_id, origin = row.id, row.origin
        grams_max = quantity_max * factor if quantity_max is not None else None
        return quantity * factor, grams_max, origin, conversion_id
    if dimension == COUNT:
        # Named count units convert through a portion with that name
        # ("stick", "slice"); bare pieces through the size's row.
        if base_unit == "piece":
            portion = _portion_row(conversions, size) if size is not None else None
        else:
            portion = _portion_row(conversions, base_unit)
        if portion is not None:
            weight, conversion_id, origin = (
                portion.grams,
                portion.id,
                portion.origin,
            )
        else:
            unit_weight = _factor_row(conversions, "unit_weight")
            if unit_weight is not None:
                weight, conversion_id, origin = (
                    unit_weight.grams,
                    unit_weight.id,
                    unit_weight.origin,
                )
            elif base_unit == "piece":
                medium = _portion_row(conversions, "medium")
                if medium is None:
                    return None, None, None, None
                weight, conversion_id, origin = (
                    medium.grams,
                    medium.id,
                    medium.origin,
                )
            else:
                return None, None, None, None
        grams_max = quantity_max * weight if quantity_max is not None else None
        return quantity * weight, grams_max, origin, conversion_id
    return None, None, None, None


def _ingredient_conversions(
    ingredient_id: int, session: Session
) -> list[IngredientConversion]:
    """All of the ingredient's conversion rows, created by seeding:
    the hand seeds and the confirmed conversion reference's portions
    side by side. Only confirmed references feed the derived rows —
    candidates and rejected ones never do."""
    return list(
        session.scalars(
            select(IngredientConversion).where(
                IngredientConversion.ingredient_id == ingredient_id
            )
        )
    )


def _line_grams(
    quantity: float | None,
    quantity_max: float | None,
    dimension: str | None,
    base_unit: str | None,
    original_unit: str | None,
    preparation: str | None,
    size: str | None,
    ingredient_id: int,
    session: Session,
) -> tuple[float | None, float | None, str | None, int | None]:
    """Grams for a line, from the ingredient's conversion rows — so a
    re-resolve can re-run the conversion without the parser."""
    return to_grams(
        quantity,
        quantity_max,
        dimension,
        base_unit,
        original_unit,
        preparation,
        size,
        _ingredient_conversions(ingredient_id, session),
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


def fdc_id_conflicts() -> list[tuple[str, list[str]]]:
    """Canonical ingredients sharing an unconfirmed candidate
    reference food with no seed term linking them, as candidates to
    hand-seed into seed.FOOD_REFS — a report, never an auto-merge.
    Shared USDA ids catch real variants (milk / warm milk) but also
    lump distinct ingredients (almond / vanilla / rosewater)."""
    with get_session() as session:
        names = {
            record.id: record.name for record in session.scalars(select(Ingredient))
        }
        links = {
            (term.term, term.ingredient_id)
            for term in session.scalars(select(IngredientTerm))
            if term.resolution == "seed" and term.ingredient_id is not None
        }
        candidates = session.execute(
            select(
                IngredientFoodRef.ref_food_id,
                IngredientFoodRef.ingredient_id,
            ).where(IngredientFoodRef.status == "candidate")
        ).all()
        descriptions = dict(
            session.execute(select(RefFood.id, RefFood.description)).all()
        )

    by_food: dict[int, list[int]] = {}
    for ref_food_id, ingredient_id in candidates:
        by_food.setdefault(ref_food_id, []).append(ingredient_id)

    conflicts = []
    for ref_food_id, group in sorted(by_food.items()):
        if len(group) < 2:
            continue
        # An alias row pointing one member's name at another member
        # means the pair is already linked by a curated seed.
        if any(
            (names[other], member) in links
            for member in group
            for other in group
            if member != other
        ):
            continue
        label = descriptions.get(ref_food_id) or f"ref_food {ref_food_id}"
        conflicts.append((label, sorted(names[member] for member in group)))
    return conflicts


def ref_review(
    limit: int = 100,
) -> list[tuple[int, str, str | None, tuple[str, ...]]]:
    """Top ingredients without a confirmed reference by line count,
    with the parser's candidate and the best text matches from each
    imported source, for hand-seeding into seed.FOOD_REFS. Candidates
    that REJECTED_REFS turned down — and their foods as text matches —
    stay out. Needs the reference import; a report, never an
    auto-merge. Lines count through their terms' effective ingredient
    (repository.effective_ingredient_predicates)."""
    with get_session() as session:
        rows = session.execute(
            select(func.count(RecipeIngredient.id), Ingredient.id, Ingredient.name)
            .join(IngredientTerm, IngredientTerm.ingredient_id == Ingredient.id)
            .join(RecipeIngredient, RecipeIngredient.term_id == IngredientTerm.id)
            .outerjoin(
                IngredientFoodRef,
                and_(
                    IngredientFoodRef.ingredient_id == Ingredient.id,
                    IngredientFoodRef.status == "confirmed",
                ),
            )
            .where(
                IngredientFoodRef.id.is_(None),
                *repository.effective_ingredient_predicates(),
            )
            .group_by(Ingredient.id)
            .order_by(func.count(RecipeIngredient.id).desc())
            .limit(limit)
        ).all()
        rejected: dict[int, set[int]] = {}
        for owner_id, food_id in session.execute(
            select(
                IngredientFoodRef.ingredient_id, IngredientFoodRef.ref_food_id
            ).where(IngredientFoodRef.status == "rejected")
        ).all():
            rejected.setdefault(owner_id, set()).add(food_id)
        sources = list(session.scalars(select(FoodSource)))
        descriptions = {
            source.id: session.execute(
                select(RefFood.id, RefFood.source_food_id, RefFood.description).where(
                    RefFood.source_id == source.id
                )
            ).all()
            for source in sources
        }
        results = []
        for count, ingredient_id, name in rows:
            candidate_food = session.scalar(
                select(IngredientFoodRef.ref_food_id).where(
                    IngredientFoodRef.ingredient_id == ingredient_id,
                    IngredientFoodRef.status == "candidate",
                )
            )
            candidate = (
                session.scalar(
                    select(RefFood.description).where(RefFood.id == candidate_food)
                )
                if candidate_food is not None
                else None
            )
            # Text match: descriptions containing every word of the
            # name, one candidate per source. A food this ingredient's
            # REJECTED_REFS entry turned down is not offered again.
            words = name.lower().split()
            rejected_foods = rejected.get(ingredient_id, set())
            matches = []
            for source in sources:
                for ref_food_id, source_food_id, description in descriptions[source.id]:
                    if ref_food_id in rejected_foods:
                        continue
                    text = description.lower()
                    if all(word in text for word in words):
                        matches.append(f"{source.name} {source_food_id}: {description}")
                        break
            results.append((count, name, candidate, tuple(matches)))
    return results


@dataclass
class ShoppingLine:
    """One aggregated shopping-list line, in base units."""

    label: str
    dimension: str | None
    total: float | None
    """Sum in base units; None for "as needed" lines."""
    base_unit: str | None
    variant: str | None = None
    """Set only when aggregate ran with keep_variants."""
    as_needed: bool = False
    unresolved: bool = False


def prune_orphan_ingredients(session: Session) -> int:
    """Delete `auto` terms no line uses, then `auto` concepts no term
    points at and no curated reference (confirmed or rejected — a
    decision in seed.py) keeps: a concept's parser candidate rows go
    with it. Seed concepts are never pruned. Used after a re-resolve
    or reparse, which re-resolves lines through the seeded terms and
    strands the junk auto concepts the first pass created. Returns the
    number of concepts pruned."""
    used = set(session.scalars(select(RecipeIngredient.term_id)))
    unused_terms = [
        term
        for term in session.scalars(
            select(IngredientTerm).where(IngredientTerm.resolution == "auto")
        )
        if term.id not in used
    ]
    for term in unused_terms:
        session.delete(term)
    session.flush()  # the concepts lose their pointers before the count

    curated = set(
        session.scalars(
            select(IngredientFoodRef.ingredient_id).where(
                IngredientFoodRef.status != "candidate"
            )
        )
    )
    pointed = set(
        session.scalars(
            select(IngredientTerm.ingredient_id).where(
                IngredientTerm.ingredient_id.is_not(None)
            )
        )
    )
    orphans = [
        ingredient
        for ingredient in session.scalars(
            select(Ingredient).where(Ingredient.origin == "auto")
        )
        if ingredient.id not in curated and ingredient.id not in pointed
    ]
    for ingredient in orphans:
        session.execute(
            delete(IngredientFoodRef).where(
                IngredientFoodRef.ingredient_id == ingredient.id
            )
        )
        session.delete(ingredient)
    return len(orphans)


def _recompute_terms(session: Session) -> int:
    """Re-apply the review heuristic to every non-seed term: a
    name_needs_review term goes back to 'review', everything else to
    'auto' pointing at the concept of the same name (created when
    missing). Seed and not_food terms still in seed.py are curated
    decisions and stand; one whose entry was removed from seed.py is
    recomputed like any other term. Run after seeding. Returns the
    number of terms changed."""
    curated = {alias for alias, _, _ in seed.ALIASES} | set(seed.NOT_FOOD)
    curated |= set(
        session.scalars(select(Ingredient.name).where(Ingredient.origin == "seed"))
    )
    changed = 0
    terms = [
        term
        for term in session.scalars(select(IngredientTerm))
        if term.resolution in ("auto", "review") or term.term not in curated
    ]
    for term in terms:
        if name_needs_review(term.term):
            resolution, ingredient_id = "review", None
        else:
            ingredient = session.scalar(
                select(Ingredient).where(Ingredient.name == term.term)
            )
            if ingredient is None:
                ingredient = Ingredient(name=term.term, origin="auto")
                session.add(ingredient)
                session.flush()
            resolution, ingredient_id = "auto", ingredient.id
        if (term.resolution, term.ingredient_id) != (resolution, ingredient_id):
            term.resolution = resolution
            term.ingredient_id = ingredient_id
            term.variant = None
            changed += 1
    return changed


def re_resolve() -> tuple[int, int, int]:
    """Re-apply the seeds and review heuristics to the terms, in one
    transaction, with no parser: a seed change reaches every line by
    re-pointing its term. Every line's grams are recomputed from the
    stored columns — a seed change can move grams without moving the
    ingredient (a conversion row, a confirmed conversion reference) —
    with the conversion rows read once per ingredient. Orphans are
    pruned last. Returns (terms changed, lines regrammed, concepts
    pruned)."""
    with get_session() as session:
        seed_ingredient_data(session)
        terms_changed = _recompute_terms(session)
        session.flush()
        inputs: dict[int, list[IngredientConversion]] = {}
        regrammed = 0
        for line, term in session.execute(repository.lines_with_terms_select()):
            effective = repository.effective_ingredient_id(line.needs_review, term)
            grams: tuple[float | None, float | None, str | None, int | None] = (
                None,
                None,
                None,
                None,
            )
            if effective is not None:
                if effective not in inputs:
                    inputs[effective] = _ingredient_conversions(effective, session)
                grams = to_grams(
                    line.quantity,
                    line.quantity_max,
                    line.dimension,
                    line.base_unit,
                    line.original_unit,
                    line.preparation,
                    line.size,
                    inputs[effective],
                )
            if grams != (
                line.grams,
                line.grams_max,
                line.grams_source,
                line.conversion_id,
            ):
                (
                    line.grams,
                    line.grams_max,
                    line.grams_source,
                    line.conversion_id,
                ) = grams
                regrammed += 1
        pruned = prune_orphan_ingredients(session)
    return terms_changed, regrammed, pruned


def aggregate(
    recipe_ids: list[int],
    servings_scale: float = 1.0,
    include_optional: bool = False,
    keep_variants: bool = False,
) -> list[ShoppingLine]:
    """Aggregate recipes into shopping-list lines.

    Lines group by ingredient — never by text — and sum in base units.
    When every quantified line of an ingredient converted to grams,
    the group is one gram line; the per-dimension fold below is the
    fallback for ingredients with unconverted lines. Ranges use the
    upper end ("2-3 onions" buys 3: under-buying a shopping list is
    worse than over-buying). Alternative rows are always skipped;
    optional rows are skipped unless include_optional. Volume folds
    into mass only when the ingredient has a known density, and bare
    counts into mass only with a unit weight; otherwise the lines
    stay separate ("250 ml + 100 g"). Named count units (slice,
    clove) never fold.

    By default variant aliases roll up to their canonical ingredient;
    keep_variants splits them back out, labelled "milk (whole)".

    Lines without a quantity are listed once as "as needed" — but if
    the same ingredient also has quantified lines, the one purchase
    covers it and no second line is emitted.
    """
    with get_session() as session:
        # Convert inside the session: the commit on exit expires the
        # ORM instances, so attributes are unreadable after it.
        records = []
        for line, term in session.execute(
            repository.lines_with_terms_select(
                RecipeIngredient.recipe_id.in_(recipe_ids)
            )
        ).all():
            model = repository.to_recipe_ingredient_model(line, term)
            # Failed parses and not_food equipment stay stored but are
            # not shopping.
            if model.parse_status == "failed" or model.term_resolution == "not_food":
                continue
            records.append(model)
        ingredients = {
            ingredient.id: repository.to_model(ingredient)
            for ingredient in session.scalars(select(Ingredient))
        }
        conversions: dict[int, list[IngredientConversion]] = {}
        for conversion in session.scalars(select(IngredientConversion)):
            conversions.setdefault(conversion.ingredient_id, []).append(conversion)
        # Inside the session like every other read: the commit on exit
        # expires the ORM instances.
        factors = {
            ingredient_id: repository.conversion_factors(rows)
            for ingredient_id, rows in conversions.items()
        }

    shopping: list[ShoppingLine] = []
    grouped: dict[tuple[int, str | None], list[repository.RecipeIngredientModel]] = {}

    for record in records:
        if record.alternative_of is not None:
            continue
        if record.optional and not include_optional:
            continue
        if record.ingredient_id is None:
            shopping.append(
                ShoppingLine(
                    label=record.raw_text,
                    dimension=record.dimension,
                    total=record.quantity,
                    base_unit=record.base_unit,
                    as_needed=record.quantity is None,
                    unresolved=True,
                )
            )
            continue
        key = record.ingredient_id, record.variant if keep_variants else None
        grouped.setdefault(key, []).append(record)

    def _sort_key(group_key: tuple[int, str | None]) -> tuple[int, str]:
        ingredient_id, variant = group_key
        return ingredient_id, variant or ""

    for group_key in sorted(grouped, key=_sort_key):
        ingredient = ingredients.get(group_key[0])
        if ingredient is None:
            continue
        variant = group_key[1]
        label = f"{ingredient.name} ({variant})" if variant else ingredient.name
        quantified = [row for row in grouped[group_key] if row.quantity is not None]
        if quantified and all(row.grams is not None for row in quantified):
            # Every line converted: one gram line replaces the
            # per-dimension fold.
            total = 0.0
            for row in quantified:
                # Ranges buy the upper end, like the fold below.
                if row.grams_max is not None:
                    total += row.grams_max * servings_scale
                elif row.grams is not None:
                    total += row.grams * servings_scale
            shopping.append(
                ShoppingLine(
                    label=label,
                    dimension=MASS,
                    total=total,
                    base_unit="g",
                    variant=variant,
                )
            )
            continue
        sums: dict[tuple[str, str], float] = {}
        has_quantity = False
        has_unquantified = False
        for row in grouped[group_key]:
            if row.quantity is None:
                has_unquantified = True
                continue
            has_quantity = True
            # Ranges buy the upper end: under-buying a shopping list is
            # worse than over-buying it.
            amount = row.quantity_max if row.quantity_max is not None else row.quantity
            unit_key = (row.dimension or "", row.base_unit or "piece")
            sums[unit_key] = sums.get(unit_key, 0.0) + amount * servings_scale

        mass = sums.get(("mass", "g"))
        volume = sums.get(("volume", "ml"))
        density, unit_weight = factors.get(ingredient.id, (None, None))
        if mass is not None and volume is not None and density is not None:
            sums[("mass", "g")] = mass + volume_to_mass(volume, density)
            del sums[("volume", "ml")]
        if (
            ("count", "piece") in sums
            and ("mass", "g") in sums
            and unit_weight is not None
        ):
            sums[("mass", "g")] += count_to_mass(sums[("count", "piece")], unit_weight)
            del sums[("count", "piece")]

        for (dimension, base_unit), total in sorted(sums.items()):
            shopping.append(
                ShoppingLine(
                    label=label,
                    dimension=dimension or None,
                    total=total,
                    base_unit=base_unit,
                    variant=variant,
                )
            )
        if not has_quantity and has_unquantified:
            shopping.append(
                ShoppingLine(
                    label=label,
                    dimension=None,
                    total=None,
                    base_unit=None,
                    variant=variant,
                    as_needed=True,
                )
            )

    return sorted(shopping, key=lambda line: (line.unresolved, line.label))
