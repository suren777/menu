"""Ingredient actions: store parsed lines, aggregate a shopping list."""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from menu.db.connection import get_session
from menu.db.database import (
    Ingredient,
    IngredientAlias,
    RecipeIngredient,
    RecipeUrls,
)
from menu.db.ingredients import repository
from menu.db.ingredients.seed import LINE_OVERRIDES
from menu.ingest.ingredients import (
    canonical_name,
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
            ingredient_id, variant = _resolve_ingredient(
                override if override is not None else parsed.name,
                parsed.fdc_id,
                session,
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
                alternative_id, _ = _resolve_ingredient(alternative, None, session)
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
            if not parsed.alternatives:
                alt_parsed = _split_or_alternative(raw, site)
                if alt_parsed is not None:
                    alternative_id, _ = _resolve_ingredient(
                        alt_parsed.name, alt_parsed.fdc_id, session
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
    parsed on its own. None when the line has no unhandled "or" or the
    remainder fails to parse."""
    parts = _OR_ALTERNATIVE.split(raw, maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return None
    try:
        return parse_line(parts[1].strip(), site)
    except Exception as exc:  # noqa: BLE001 - a bad line must not stop the recipe
        logger.warning(
            "Skipping alternative of %r: %s: %s", raw, type(exc).__name__, exc
        )
        return None


def _resolve_ingredient(
    name: str | None, fdc_id: int | None, session: Session
) -> tuple[int | None, str | None]:
    """Id and variant of the canonical ingredient a parsed name
    resolves to, in the order trusted:

    1. A curated line override already decided the name before this
       call, so an alias match carries the variant ("whole milk" ->
       milk, variant "whole").
    2. A name the parser likely mangled (name_needs_review) stays
       unresolved (None) for the review queue instead of becoming a
       junk canonical ingredient.
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
    if name_needs_review(name):
        return None, None
    by_name = repository.find_ingredient_by_name(name, session)
    if by_name is not None:
        return by_name.id, None
    session.add(Ingredient(name=name, fdc_id=fdc_id))
    session.flush()
    created = repository.find_ingredient_by_name(name, session)
    if created is None:  # pragma: no cover - inserted two lines above
        return None, None
    session.add(IngredientAlias(alias=canonical_name(name), ingredient_id=created.id))
    return created.id, None


def _servings_from_yield(value: Any) -> int | None:
    """First integer in a recipeYield: 6, "Serves 12", "Makes 1 loaf"."""
    if value is None:
        return None
    match = _YIELD_NUMBER.search(str(value))
    return int(match.group()) if match else None


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
