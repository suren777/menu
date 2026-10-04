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
    IngredientAlias,
    IngredientFoodRef,
    RecipeIngredient,
    RecipeUrls,
    RefFood,
)
from menu.db.ingredients import repository
from menu.db.ingredients.seed import LINE_OVERRIDES
from menu.ingest.ingredients import (
    canonical_name,
    line_needs_review,
    name_needs_review,
    parse_line,
)
from menu.ingest.units import count_to_mass, volume_to_mass

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from menu.ingest.ingredients import ParsedLine
    from menu.ingest.registry import SiteConfig

logger = logging.getLogger(__name__)

_YIELD_NUMBER = re.compile(r"\d+")
_OR_ALTERNATIVE = re.compile(r"\bor\b", re.IGNORECASE)


def store_recipe_ingredients(
    url: str, recipe_data: dict[str, Any], site: SiteConfig
) -> None:
    """Parse a recipe's ingredient lines and store recipe_ingredient rows.

    Re-running a recipe replaces its previous rows, so reparsing is
    idempotent. The recipe is stamped with the site and its servings
    parsed from recipeYield. A line that fails to parse is logged and
    skipped: bad lines must not stop a recipe (or a crawl), mirroring
    the pipeline's per-URL handling.
    """
    lines = recipe_data.get("recipeIngredient", [])
    if not isinstance(lines, list):
        logger.warning("recipeIngredient is not a list for %s", url)
        return

    with get_session() as session:
        record = session.scalars(
            select(RecipeUrls).where(RecipeUrls.url == url)
        ).first()
        if record is None:
            logger.warning("No recipe stored for %s; ingredients skipped", url)
            return

        record.site = site.name
        record.servings = _servings_from_yield(recipe_data.get("recipeYield"))

        session.execute(
            delete(RecipeIngredient).where(RecipeIngredient.recipe_id == record.id)
        )

        for position, raw in enumerate(lines):
            if not isinstance(raw, str):
                logger.warning("Skipping non-string ingredient for %s: %r", url, raw)
                continue
            try:
                parsed = parse_line(raw, site)
            except Exception as exc:  # noqa: BLE001 - a bad line must not stop the recipe
                logger.warning(
                    "Skipping ingredient line %r for %s: %s: %s",
                    raw,
                    url,
                    type(exc).__name__,
                    exc,
                )
                continue
            override = LINE_OVERRIDES.get(" ".join(raw.lower().split()))
            alt_parsed = (
                None if parsed.alternatives else _split_or_alternative(raw, site)
            )
            ingredient_id, variant = _resolve_ingredient(
                override if override is not None else parsed.name,
                parsed.fdc_id,
                session,
                needs_review=override is None
                and line_needs_review(raw, parsed, alt_parsed),
            )
            line = RecipeIngredient(
                recipe_id=record.id,
                position=position,
                raw_text=raw,
                ingredient_id=ingredient_id,
                variant=variant,
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
            )
            session.add(line)
            session.flush()  # the row's id is needed by the alternative rows
            for alternative in parsed.alternatives:
                alternative_id, _ = _resolve_ingredient(
                    alternative,
                    None,
                    session,
                    needs_review=name_needs_review(alternative),
                )
                session.add(
                    RecipeIngredient(
                        recipe_id=record.id,
                        position=position,
                        raw_text=raw,
                        ingredient_id=alternative_id,
                        alternative_of=line.id,
                    )
                )
            # The parser handles bare "X or Y" name alternatives itself,
            # but drops the second option of "2 tsp vanilla or 1 tsp
            # essential oil" into the note. Split it off here so it is
            # still kept as an alternative row.
            if alt_parsed is not None:
                alternative_id, _ = _resolve_ingredient(
                    alt_parsed.name,
                    alt_parsed.fdc_id,
                    session,
                    needs_review=name_needs_review(alt_parsed.name or ""),
                )
                session.add(
                    RecipeIngredient(
                        recipe_id=record.id,
                        position=position,
                        raw_text=raw,
                        ingredient_id=alternative_id,
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


def _resolve_ingredient(
    name: str | None,
    fdc_id: int | None,
    session: Session,
    needs_review: bool = False,
) -> tuple[int | None, str | None]:
    """Id and variant of the canonical ingredient a parsed name
    resolves to, in the order trusted:

    1. A curated line override already decided the name before this
       call, so an alias match carries the variant ("whole milk" ->
       milk, variant "whole").
    2. A line flagged for review — a mangled name, or a conjunction
       the parse dropped (line_needs_review) — stays unresolved (None)
       for the review queue instead of becoming a junk canonical
       ingredient.
    3. Otherwise the ingredient is created, with a self-alias so later
       variants ("warm milk") can attach to it.

    A line the parser could not name at all stays unresolved too: it
    is stored anyway and reviewed later.
    """
    if name is None:
        return None, None
    match = repository.alias_target(name, session)
    if match is not None:
        ingredient, variant = match
        return ingredient.id, variant
    if needs_review:
        return None, None
    by_name = repository.find_ingredient_by_name(name, session)
    if by_name is not None:
        return by_name.id, None
    session.add(Ingredient(name=name))
    session.flush()
    created = repository.find_ingredient_by_name(name, session)
    if created is None:  # pragma: no cover - inserted two lines above
        return None, None
    session.add(IngredientAlias(alias=canonical_name(name), ingredient_id=created.id))
    if fdc_id is not None:
        _seed_candidate(created.id, str(fdc_id), session)
    return created.id, None


def _seed_candidate(
    ingredient_id: int, source_food_id: str, session: Session
) -> None:
    """The parser's fdc_id as an unconfirmed candidate row (role
    NULL): a suggestion for the --ref-review report, never used for
    conversion or nutrition."""
    ref_food = session.execute(
        select(RefFood.id)
        .join(FoodSource, FoodSource.id == RefFood.source_id)
        .where(FoodSource.name == "fdc", RefFood.source_food_id == source_food_id)
    ).scalar_one_or_none()
    if ref_food is not None:
        session.add(
            IngredientFoodRef(ingredient_id=ingredient_id, ref_food_id=ref_food)
        )


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
    reference food with no alias between them, as candidates to
    hand-seed into seed.FOOD_REFS — a report, never an auto-merge.
    Shared USDA ids catch real variants (milk / warm milk) but also
    lump distinct ingredients (almond / vanilla / rosewater)."""
    with get_session() as session:
        names = {
            record.id: record.name
            for record in session.scalars(select(Ingredient))
        }
        links = {
            (alias.alias, alias.ingredient_id)
            for alias in session.scalars(select(IngredientAlias))
        }
        candidates = session.execute(
            select(
                IngredientFoodRef.ref_food_id,
                IngredientFoodRef.ingredient_id,
            ).where(IngredientFoodRef.confirmed.is_(False))
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
    imported source, for hand-seeding into seed.FOOD_REFS. Needs the
    reference import; a report, never an auto-merge."""
    with get_session() as session:
        rows = session.execute(
            select(func.count(RecipeIngredient.id), Ingredient.id, Ingredient.name)
            .outerjoin(
                RecipeIngredient, RecipeIngredient.ingredient_id == Ingredient.id
            )
            .outerjoin(
                IngredientFoodRef,
                and_(
                    IngredientFoodRef.ingredient_id == Ingredient.id,
                    IngredientFoodRef.confirmed.is_(True),
                ),
            )
            .where(IngredientFoodRef.id.is_(None))
            .group_by(Ingredient.id)
            .order_by(func.count(RecipeIngredient.id).desc())
            .limit(limit)
        ).all()
        sources = list(session.scalars(select(FoodSource)))
        descriptions = {
            source.id: session.execute(
                select(RefFood.source_food_id, RefFood.description).where(
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
                    IngredientFoodRef.confirmed.is_(False),
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
            # name, one candidate per source.
            words = name.lower().split()
            matches = []
            for source in sources:
                for source_food_id, description in descriptions[source.id]:
                    text = description.lower()
                    if all(word in text for word in words):
                        matches.append(
                            f"{source.name} {source_food_id}: {description}"
                        )
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
    """Delete ingredients no line and no inbound alias points at.

    A parse-time self-alias does not keep an ingredient alive: it is
    removed with the orphan. Used after a reparse, which re-resolves
    lines through the seeded aliases and strands the junk canonical
    ingredients the first pass created. Returns the number pruned.
    """
    orphans = []
    for ingredient in session.scalars(select(Ingredient)):
        lines = session.scalar(
            select(func.count()).select_from(RecipeIngredient).where(
                RecipeIngredient.ingredient_id == ingredient.id
            )
        )
        inbound = session.scalar(
            select(func.count()).select_from(IngredientAlias).where(
                IngredientAlias.ingredient_id == ingredient.id,
                IngredientAlias.alias != ingredient.name,
            )
        )
        if not lines and not inbound:
            orphans.append(ingredient)
    for ingredient in orphans:
        session.execute(
            delete(IngredientAlias).where(IngredientAlias.alias == ingredient.name)
        )
        session.delete(ingredient)
    return len(orphans)


def aggregate(
    recipe_ids: list[int],
    servings_scale: float = 1.0,
    include_optional: bool = False,
    keep_variants: bool = False,
) -> list[ShoppingLine]:
    """Aggregate recipes into shopping-list lines.

    Lines group by ingredient — never by text — and sum in base units.
    Ranges use the upper end ("2-3 onions" buys 3: under-buying a
    shopping list is worse than over-buying). Alternative rows are
    always skipped; optional rows are skipped unless include_optional.
    Volume folds into mass only when the ingredient has a known
    density, and bare counts into mass only with a unit weight;
    otherwise the lines stay separate ("250 ml + 100 g"). Named count
    units (slice, clove) never fold.

    By default variant aliases roll up to their canonical ingredient;
    keep_variants splits them back out, labelled "milk (whole)".

    Lines without a quantity are listed once as "as needed" — but if
    the same ingredient also has quantified lines, the one purchase
    covers it and no second line is emitted.
    """
    with get_session() as session:
        # Convert inside the session: the commit on exit expires the
        # ORM instances, so attributes are unreadable after it.
        records = [
            repository.to_recipe_ingredient_model(record)
            for record in session.scalars(
                select(RecipeIngredient).where(
                    RecipeIngredient.recipe_id.in_(recipe_ids)
                )
            )
        ]
        ingredients = {
            ingredient.id: repository.to_model(ingredient)
            for ingredient in session.scalars(select(Ingredient))
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
        if (
            mass is not None
            and volume is not None
            and ingredient.density_g_per_ml is not None
        ):
            sums[("mass", "g")] = mass + volume_to_mass(
                volume, ingredient.density_g_per_ml
            )
            del sums[("volume", "ml")]
        if (
            ("count", "piece") in sums
            and ("mass", "g") in sums
            and ingredient.unit_weight_g is not None
        ):
            sums[("mass", "g")] += count_to_mass(
                sums[("count", "piece")], ingredient.unit_weight_g
            )
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
                    label=label, dimension=None, total=None, base_unit=None,
                    variant=variant, as_needed=True,
                )
            )

    return sorted(shopping, key=lambda line: (line.unresolved, line.label))
