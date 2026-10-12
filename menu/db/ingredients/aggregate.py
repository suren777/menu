"""Aggregation: folding recipes into shopping-list lines."""

from dataclasses import dataclass

from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import Ingredient, IngredientConversion, RecipeIngredient
from menu.db.ingredients import repository
from menu.ingest.units import MASS, count_to_mass, volume_to_mass


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
    records, ingredients, factors = _load_inputs(recipe_ids)

    shopping: list[ShoppingLine] = []
    grouped, unresolved = _group_lines(records, include_optional, keep_variants)
    shopping.extend(unresolved)

    for group_key in sorted(grouped, key=lambda key: (key[0], key[1] or "")):
        ingredient = ingredients.get(group_key[0])
        if ingredient is None:
            continue
        variant = group_key[1]
        label = f"{ingredient.name} ({variant})" if variant else ingredient.name
        density, unit_weight = factors.get(ingredient.id, (None, None))
        shopping.extend(
            _aggregate_group(
                grouped[group_key],
                label,
                variant,
                density,
                unit_weight,
                servings_scale,
            )
        )

    return sorted(shopping, key=lambda line: (line.unresolved, line.label))


def _load_inputs(
    recipe_ids: list[int],
) -> tuple[
    list[repository.RecipeIngredientModel],
    dict[int, repository.IngredientModel],
    dict[int, tuple[float | None, float | None]],
]:
    """The recipes' parsed lines, the ingredient names and each
    ingredient's (density, unit_weight) factors, read in one session."""
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

    return records, ingredients, factors


def _group_lines(
    records: list[repository.RecipeIngredientModel],
    include_optional: bool,
    keep_variants: bool,
) -> tuple[
    dict[tuple[int, str | None], list[repository.RecipeIngredientModel]],
    list[ShoppingLine],
]:
    """Partition the parsed lines: lines with an effective ingredient
    group by (ingredient, variant when keep_variants); the rest are
    unresolved, one ShoppingLine each labelled by their raw text."""
    unresolved: list[ShoppingLine] = []
    grouped: dict[tuple[int, str | None], list[repository.RecipeIngredientModel]] = {}

    for record in records:
        if record.alternative_of is not None:
            continue
        if record.optional and not include_optional:
            continue
        if record.ingredient_id is None:
            unresolved.append(
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

    return grouped, unresolved


def _aggregate_group(
    rows: list[repository.RecipeIngredientModel],
    label: str,
    variant: str | None,
    density: float | None,
    unit_weight: float | None,
    servings_scale: float,
) -> list[ShoppingLine]:
    """One ingredient group's lines: a single gram line when every
    quantified row converted to grams, else the per-dimension sums
    (folded across dimensions) plus an "as needed" line when the
    group also has unquantified rows."""
    quantified = [row for row in rows if row.quantity is not None]
    if quantified and all(row.grams is not None for row in quantified):
        # Every line converted: one gram line replaces the
        # per-dimension fold.
        return [
            ShoppingLine(
                label=label,
                dimension=MASS,
                total=_gram_total(quantified, servings_scale),
                base_unit="g",
                variant=variant,
            )
        ]

    sums, has_quantity, has_unquantified = _sum_dimensions(rows, servings_scale)
    _fold_cross_dimension(sums, density, unit_weight)
    lines = [
        ShoppingLine(
            label=label,
            dimension=dimension or None,
            total=total,
            base_unit=base_unit,
            variant=variant,
        )
        for (dimension, base_unit), total in sorted(sums.items())
    ]
    if not has_quantity and has_unquantified:
        lines.append(
            ShoppingLine(
                label=label,
                dimension=None,
                total=None,
                base_unit=None,
                variant=variant,
                as_needed=True,
            )
        )
    return lines


def _gram_total(
    quantified: list[repository.RecipeIngredientModel], servings_scale: float
) -> float:
    """Sum the scaled gram weights of the group's quantified rows."""
    total = 0.0
    for row in quantified:
        # Ranges buy the upper end, like the fold below.
        if row.grams_max is not None:
            total += row.grams_max * servings_scale
        elif row.grams is not None:
            total += row.grams * servings_scale
    return total


def _sum_dimensions(
    rows: list[repository.RecipeIngredientModel], servings_scale: float
) -> tuple[dict[tuple[str, str], float], bool, bool]:
    """Sum the scaled quantities per (dimension, base unit); ranges
    buy the upper end. Also whether the group has quantified and
    unquantified rows — the "as needed" rule reads both."""
    sums: dict[tuple[str, str], float] = {}
    has_quantity = False
    has_unquantified = False
    for row in rows:
        if row.quantity is None:
            has_unquantified = True
            continue
        has_quantity = True
        # Ranges buy the upper end: under-buying a shopping list is
        # worse than over-buying it.
        amount = row.quantity_max if row.quantity_max is not None else row.quantity
        unit_key = (row.dimension or "", row.base_unit or "piece")
        sums[unit_key] = sums.get(unit_key, 0.0) + amount * servings_scale
    return sums, has_quantity, has_unquantified


def _fold_cross_dimension(
    sums: dict[tuple[str, str], float],
    density: float | None,
    unit_weight: float | None,
) -> None:
    """Fold volume and bare counts into mass when the ingredient's
    conversion factors allow: volume needs a density, counts a unit
    weight; otherwise the lines stay separate ("250 ml + 100 g").
    Named count units (slice, clove) never fold."""
    mass = sums.get(("mass", "g"))
    volume = sums.get(("volume", "ml"))
    if mass is not None and volume is not None and density is not None:
        sums[("mass", "g")] = mass + volume_to_mass(volume, density)
        del sums[("volume", "ml")]
    if ("count", "piece") in sums and ("mass", "g") in sums and unit_weight is not None:
        sums[("mass", "g")] += count_to_mass(sums[("count", "piece")], unit_weight)
        del sums[("count", "piece")]
