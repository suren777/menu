"""Ingredient actions: store parsed lines, aggregate a shopping list."""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from menu.db.connection import get_session
from menu.db.database import (
    Ingredient,
    IngredientAlias,
    RecipeIngredient,
    RecipeUrls,
)
from menu.db.ingredients import repository
from menu.ingest.ingredients import canonical_name, parse_line
from menu.ingest.units import count_to_mass, volume_to_mass

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from menu.ingest.registry import SiteConfig

logger = logging.getLogger(__name__)

_YIELD_NUMBER = re.compile(r"\d+")


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
            line = RecipeIngredient(
                recipe_id=record.id,
                position=position,
                raw_text=raw,
                ingredient_id=_ensure_ingredient(parsed.name, parsed.fdc_id, session),
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
                session.add(
                    RecipeIngredient(
                        recipe_id=record.id,
                        position=position,
                        raw_text=raw,
                        ingredient_id=_ensure_ingredient(alternative, None, session),
                        alternative_of=line.id,
                    )
                )


def _ensure_ingredient(
    name: str | None, fdc_id: int | None, session: Session
) -> int | None:
    """Id of the canonical ingredient a parsed name resolves to.

    An existing alias wins. Otherwise an ingredient is created, with a
    self-alias so later variants ("warm milk") can attach to it. A
    line the parser could not name stays unresolved (None): it is
    stored anyway and reviewed later.
    """
    if name is None:
        return None
    existing = repository.alias_target(name, session)
    if existing is not None:
        return existing.id
    by_name = repository.find_ingredient_by_name(name, session)
    if by_name is not None:
        return by_name.id
    session.add(Ingredient(name=name, fdc_id=fdc_id))
    session.flush()
    created = repository.find_ingredient_by_name(name, session)
    if created is None:  # pragma: no cover - inserted two lines above
        return None
    session.add(IngredientAlias(alias=canonical_name(name), ingredient_id=created.id))
    return created.id


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
    as_needed: bool = False
    unresolved: bool = False


def aggregate(
    recipe_ids: list[int], servings_scale: float = 1.0, include_optional: bool = False
) -> list[ShoppingLine]:
    """Aggregate recipes into shopping-list lines.

    Lines group by ingredient — never by text — and sum in base units.
    Alternative rows are always skipped; optional rows are skipped
    unless include_optional. Volume folds into mass only when the
    ingredient has a known density, and bare counts into mass only
    with a unit weight; otherwise the lines stay separate ("250 ml +
    100 g"). Named count units (slice, clove) never fold.

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
    grouped: dict[int, list[repository.RecipeIngredientModel]] = {}

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
        grouped.setdefault(record.ingredient_id, []).append(record)

    for ingredient_id in sorted(grouped):
        ingredient = ingredients.get(ingredient_id)
        if ingredient is None:
            continue
        sums: dict[tuple[str, str], float] = {}
        has_quantity = False
        has_unquantified = False
        for row in grouped[ingredient_id]:
            if row.quantity is None:
                has_unquantified = True
                continue
            has_quantity = True
            key = (row.dimension or "", row.base_unit or "piece")
            sums[key] = sums.get(key, 0.0) + row.quantity * servings_scale

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
                    label=ingredient.name,
                    dimension=dimension or None,
                    total=total,
                    base_unit=base_unit,
                )
            )
        if not has_quantity and has_unquantified:
            shopping.append(
                ShoppingLine(
                    label=ingredient.name, dimension=None, total=None, base_unit=None,
                    as_needed=True,
                )
            )

    return sorted(shopping, key=lambda line: (line.unresolved, line.label))
