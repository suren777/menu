"""Hand-seeded ingredient data: terms, densities, unit weights, FDC ids.

Three things cannot come from the parser and are curated here by hand:

- Variant canonicalisation ("warm milk" -> "milk") is term-driven;
  the parser keeps modifiers fused into the name.
- USDA FoodData Central has no directly usable density, so
  volume->mass aggregation needs these measured values.
- The parser's fdc_id is a suggestion and often wrong; FDC_IDS
  carries hand-reviewed mappings.

Rerunning is idempotent: missing rows are created, and a seed term
that exists as an auto/review guess (typically a parse-time term for
a variant name) is re-pointed to the canonical ingredient.
"""

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientConversion,
    IngredientFoodRef,
    IngredientTerm,
    RefFood,
    RefPortion,
)
from menu.ingest.units import singular, unit_ml

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# alias -> (canonical ingredient, variant)
ALIASES: list[tuple[str, str, str | None]] = [
    ("warm milk", "milk", "warm"),
    ("whole milk", "milk", "whole"),
    ("salted butter", "butter", "salted"),
    ("unsalted butter", "butter", "unsalted"),
    ("melted butter", "butter", "melted"),
    ("plain flour", "flour", "plain"),
    ("bread flour", "flour", "bread"),
    ("strong white bread flour", "flour", "bread"),
    ("egg white", "egg", "white"),
    ("egg yolk", "egg", "yolk"),
    ("instant dried yeast", "fast-action dried yeast", None),
    # The parser turns "rosewater or vanilla extract" into
    # "rosewater extract"; map it back to rosewater.
    ("rosewater extract", "rosewater", None),
    (
        "whole blanched almonds roughly chopped",
        "blanched almond",
        "whole, roughly chopped",
    ),
    # The King Arthur site strips its "King Arthur " brand prefix at
    # parse time (ingredient_strip_patterns), so the aliases below are
    # the brand-free names the parser sees. Bread flour folds into the
    # existing "flour" canonical rather than fragmenting a second one.
    # "semolina flour" needs no alias: the stripped name already parses
    # to its own canonical.
    ("unbleached all-purpose flour", "all-purpose flour", None),
    ("unbleached bread flour", "flour", "bread"),
    ("baker's special dry milk", "nonfat dry milk", None),
    ("artisan bread topping", "bread topping", "artisan"),
    ("the works bread topping", "bread topping", "the works"),
]

# Whitespace-normalised raw line -> canonical ingredient, for lines the
# parser merges wrongly. The real cached line "70g  milk or dark
# chocolate roughly chopped (optional)" (double space) parses as name
# "milk" with confidence 1.0 — no confidence threshold catches it, only
# curated data does. Lookup squashes the line's whitespace, so keys
# hold the single-spaced form. Checked before the parser's name is
# trusted.
LINE_OVERRIDES: dict[str, str] = {
    "70g milk or dark chocolate roughly chopped (optional)": "milk chocolate",
}

# Equipment and other non-ingredients the parser calls an ingredient
# ("12-hole mini bundt tin" parses as a food). Their terms are marked
# not_food: the lines stay stored, but are excluded from aggregation
# and nutrition.
NOT_FOOD: list[str] = [
    "12-hole mini bundt tin",
]

# g/ml, cooking-standard measured values
DENSITIES_G_PER_ML: dict[str, float] = {
    "milk": 1.03,
    "butter": 0.96,
    "flour": 0.55,
    "caster sugar": 0.85,
}

# g per piece
UNIT_WEIGHTS_G: dict[str, float] = {
    "egg": 50.0,
}

# Hand-reviewed canonical ingredient -> USDA FDC id, worked top-down
# by line count (the --fdc-review report shows what is left). Only
# confirmed mappings feed nutrition: the parser's fdc_id is a
# suggestion and often wrong — baking powder resolved to "Baobab
# powder", rapeseed oil to "Sunflower oil". Where the seed and the
# parser disagree, the seed wins. Rapeseed oil and vanilla bean paste
# are deliberately absent: no honest FDC food exists for them yet.
FDC_IDS: dict[str, int] = {
    "butter": 173430,  # Butter, without salt
    "egg": 171287,  # Egg, whole, raw, fresh
    "granulated sugar": 169655,  # Sugars, granulated
    "table salt": 173468,  # Salt, table
    "all-purpose flour": 168936,  # Wheat flour, all-purpose, enriched
    "garlic": 169230,  # Garlic, raw
    "olive oil": 2710186,  # Olive oil
    "milk": 2705385,  # Milk, whole
    "baking powder": 172803,  # Leavening agents, baking powder
    "flour": 168936,
    "vegetable oil": 172370,  # Oil, vegetable, soybean, refined
    "water": 2710707,  # Water, tap
    "lemon": 2709168,  # Lemon, raw
    "onion": 170000,  # Onions, raw
    "instant yeast": 175043,  # Yeast, baker's, active dry
    "ground cinnamon": 171320,
    "caster sugar": 169655,
    "confectioners' sugar": 2710259,
    "light brown sugar": 168833,  # Sugars, brown
    "baking soda": 175040,
    "black pepper": 170931,
    "vanilla extract": 173471,
    "heavy cream": 2705597,  # Cream, heavy
    "honey": 169640,
    "salt": 173468,
    "lemon juice": 167747,
    "dark brown sugar": 168833,
    "double cream": 2705597,
    "red onion": 170000,
    "carrot": 170393,
    "ginger": 169231,
    "spring onion": 170005,
    "lime": 168155,
    "icing sugar": 169656,  # Sugars, powdered
    "sunflower oil": 2710192,
    "parsley": 2709796,
    "coriander": 2709782,  # Cilantro, raw
    "maple syrup": 169661,
    "kosher salt": 173468,
    "ground cumin": 170923,
    "nonfat dry milk": 171272,
    "golden caster sugar": 169655,
    "walnut": 170187,  # Nuts, walnuts, english
    "cornflour": 169698,  # Cornstarch
    "tomato purée": 170460,  # Tomato puree, without salt added
    "self-raising flour": 168895,
    "fine sea salt": 173468,
    "orange": 2709171,
    "light brown soft sugar": 168833,
    "buttermilk": 2705393,
    "parmesan": 171247,  # Cheese, parmesan, grated
    "bay leaf": 170917,
    "ground ginger": 170926,
    "ground nutmeg": 171326,
    "cream cheese": 173418,
    "smoked paprika": 171329,  # Spices, paprika
    "extra virgin olive oil": 171413,  # Oil, olive, salad or cooking
    "pecan": 170182,  # Nuts, pecans
    "tomato": 2709719,  # Tomatoes, raw
    "sesame seed": 2707586,
    "dark chocolate": 2710336,  # Dark chocolate candy
    "sour cream": 171257,  # Cream, sour, cultured
    "chicken stock": 172884,  # Soup, stock, chicken, home-prepared
    "ground turmeric": 172231,
    "chopped tomato": 2709719,
    "cumin seed": 170923,
    "espresso powder": 2710378,  # Coffee, espresso
    "chive": 169994,
    "vegetable stock": 171583,  # Soup, vegetable broth, ready to serve
    "active dry yeast": 175043,
    "dried oregano": 171328,
    "shallot": 170499,
    "thyme": 173470,  # Thyme, fresh
    "coarse sparkling sugar": 169655,
    "ground coriander": 170922,  # Spices, coriander seed
    "celery": 169988,
    "dijon mustard": 2710085,  # Mustard
    "red chilli": 170106,  # Peppers, hot chili, red, raw
    "soy sauce": 2707442,
}


# FDC foods give both household portion weights (conversion) and
# per-100g nutrients (nutrition), so every FDC_IDS mapping serves
# both roles.
FOOD_REFS: dict[str, list[tuple[str, str, str]]] = {
    name: [("fdc", str(fdc_id), "conversion"), ("fdc", str(fdc_id), "nutrition")]
    for name, fdc_id in FDC_IDS.items()
}


# Reviewed-and-rejected parser suggestions, curated in git like
# FOOD_REFS: (canonical ingredient, source name, source_food_id). When
# --ref-review shows a candidate that is wrong but no honest reference
# exists to seed instead, add it here and seeding marks the matching
# candidate 'rejected' (inserting a rejected row if the ingredient
# exists but no row does) — it then stays out of --ref-review for good,
# and a re-crawl cannot resurrect it. A confirmed mapping is never
# rejected: seeding logs a warning instead.
REJECTED_REFS: list[tuple[str, str, str]] = []


# Portion weights come from the first of these sources the ingredient
# has a confirmed conversion reference in (seed overrides beat all).
CONVERSION_SOURCES: tuple[str, ...] = ("fdc", "cnf")


def conversion_rank(source_name: str) -> int:
    """Position in CONVERSION_SOURCES; unlisted sources come last."""
    if source_name in CONVERSION_SOURCES:
        return CONVERSION_SOURCES.index(source_name)
    return len(CONVERSION_SOURCES)


# A preparation that changes the food itself: "2 egg yolks" is not
# egg, "juice of 1 lemon" is not lemon, cooked rice is not raw.
# (canonical ingredient, keyword in the line's preparation, parsed
# name or alias variant) -> (source, source_food_id). Hand-seeded,
# so hand-seeded is the confirmation.
PREPARED_FORMS: dict[tuple[str, str], tuple[str, str]] = {
    ("egg", "yolk"): ("fdc", "172184"),
    ("egg", "white"): ("fdc", "172183"),
    ("lemon", "juice"): ("fdc", "167747"),
    ("lime", "juice"): ("fdc", "168156"),
    ("rice", "cooked"): ("fdc", "168878"),
}


def prepared_form(ingredient_name: str, *texts: str | None) -> tuple[str, str] | None:
    """The prepared form whose keyword appears in one of the line's
    texts (parsed name, preparation, stored variant).

    Notes are deliberately excluded — "freeze the whites for another
    recipe" must not turn an egg into egg white.
    """
    haystack = " ".join(text.lower() for text in texts if text)
    for (canonical, keyword), form in PREPARED_FORMS.items():
        if canonical == ingredient_name and keyword in haystack:
            return form
    return None


def _canonical_ingredient(name: str, session: Session) -> Ingredient:
    """The seed concept called `name`, created with origin 'seed' when
    missing; a concept the parser had already created as 'auto' is
    flipped to 'seed'. Every seed concept also gets its own seed term
    (the self-term), so "milk" resolves to milk."""
    ingredient = session.scalar(select(Ingredient).where(Ingredient.name == name))
    if ingredient is None:
        ingredient = Ingredient(name=name, origin="seed")
        session.add(ingredient)
        session.flush()
    elif ingredient.origin == "auto":
        ingredient.origin = "seed"
    _seed_term(name, ingredient.id, None, session, repoint=False)
    return ingredient


def _seed_term(
    term: str,
    ingredient_id: int | None,
    variant: str | None,
    session: Session,
    *,
    repoint: bool = True,
) -> None:
    """Upsert one seed term, overriding an existing auto/review term
    (a parser guess loses to curated data) and re-pointing an existing
    seed term whose target changed in seed.py — unless repoint is
    False: a seed concept's self-term must not override an ALIASES
    entry spelled the same ("warm milk" is an alias of milk even when
    a density seeds a "warm milk" concept). A not_food term is a
    curated decision too and stands."""
    existing = session.scalar(select(IngredientTerm).where(IngredientTerm.term == term))
    if existing is None:
        session.add(
            IngredientTerm(
                term=term,
                ingredient_id=ingredient_id,
                variant=variant,
                resolution="seed",
            )
        )
    elif existing.resolution in ("auto", "review") or (
        repoint and existing.resolution == "seed"
    ):
        existing.ingredient_id = ingredient_id
        existing.variant = variant
        existing.resolution = "seed"


def _upsert_conversion(
    ingredient_id: int,
    kind: str,
    unit: str | None,
    modifier: str | None,
    grams: float,
    origin: str,
    session: Session,
    *,
    ref_food_id: int | None = None,
    ref_seq_num: int | None = None,
) -> tuple[int, str, str, str | None, str | None]:
    """Upsert one conversion row by its unique key — re-seeding keeps
    ids stable, so lines can keep pointing at them — and return that
    key, for the stale-row sweep at the end of seeding."""
    row = session.scalar(
        select(IngredientConversion).where(
            IngredientConversion.ingredient_id == ingredient_id,
            IngredientConversion.kind == kind,
            IngredientConversion.origin == origin,
            IngredientConversion.unit == unit,
            IngredientConversion.modifier == modifier,
        )
    )
    if row is None:
        session.add(
            IngredientConversion(
                ingredient_id=ingredient_id,
                kind=kind,
                unit=unit,
                modifier=modifier,
                grams=grams,
                origin=origin,
                ref_food_id=ref_food_id,
                ref_seq_num=ref_seq_num,
            )
        )
    else:
        row.grams = grams
        row.ref_food_id = ref_food_id
        row.ref_seq_num = ref_seq_num
    return (ingredient_id, origin, kind, unit, modifier)


def seed_ingredient_data(session: Session) -> None:
    """Create the seed rows; safe to rerun."""
    for alias, canonical, variant in ALIASES:
        target = _canonical_ingredient(canonical, session)
        _seed_term(alias, target.id, variant, session)

    # Equipment and other non-ingredients: terms with no concept,
    # excluded from aggregation and nutrition.
    for name in NOT_FOOD:
        existing = session.scalar(
            select(IngredientTerm).where(IngredientTerm.term == name)
        )
        if existing is None:
            session.add(
                IngredientTerm(
                    term=name,
                    ingredient_id=None,
                    variant=None,
                    resolution="not_food",
                )
            )
        elif existing.resolution in ("auto", "review"):
            existing.ingredient_id = None
            existing.variant = None
            existing.resolution = "not_food"

    # Conversion factors live on ingredient_conversions rows; the set
    # collects every key this run writes, so the sweep at the end can
    # delete the rows nothing produced any more.
    produced: set[tuple[int, str, str, str | None, str | None]] = set()

    # Density and unit-weight ingredients are created even when
    # nothing points at them yet: caster sugar is no alias target, so
    # a fresh crawl would otherwise meet it before the next startup
    # and miss its density for a run.
    for name, density in DENSITIES_G_PER_ML.items():
        ingredient = _canonical_ingredient(name, session)
        produced.add(
            _upsert_conversion(
                ingredient.id, "density", None, None, density, "seed", session
            )
        )

    for name, weight in UNIT_WEIGHTS_G.items():
        ingredient = _canonical_ingredient(name, session)
        produced.add(
            _upsert_conversion(
                ingredient.id, "unit_weight", None, None, weight, "seed", session
            )
        )

    # Reference-food mappings are hand-reviewed (the --ref-review
    # report shows what is left): only confirmed mappings feed
    # conversion and nutrition. The parser's own suggestions seed
    # candidate rows instead (lines.py); reviewed-and-rejected
    # suggestions are curated below and marked 'rejected'.
    missed = 0
    for name, refs in FOOD_REFS.items():
        ingredient = _canonical_ingredient(name, session)
        for source_name, source_food_id, role in refs:
            ref_food = session.execute(
                select(RefFood.id, RefFood.source_id)
                .join(FoodSource, FoodSource.id == RefFood.source_id)
                .where(
                    FoodSource.name == source_name,
                    RefFood.source_food_id == source_food_id,
                )
            ).first()
            if ref_food is None:
                missed += 1
                continue
            ref_food_id, source_id = ref_food
            ref_row = session.scalar(
                select(IngredientFoodRef).where(
                    IngredientFoodRef.ingredient_id == ingredient.id,
                    IngredientFoodRef.ref_food_id == ref_food_id,
                    IngredientFoodRef.role == role,
                )
            )
            if ref_row is None:
                # A parser candidate or a curated rejection sits on the
                # same (ingredient, food) pair with no role: the seed
                # confirms it into this role instead of doubling the
                # mapping.
                ref_row = session.scalar(
                    select(IngredientFoodRef).where(
                        IngredientFoodRef.ingredient_id == ingredient.id,
                        IngredientFoodRef.ref_food_id == ref_food_id,
                        IngredientFoodRef.role.is_(None),
                    )
                )
            if ref_row is None:
                session.add(
                    IngredientFoodRef(
                        ingredient_id=ingredient.id,
                        ref_food_id=ref_food_id,
                        source_id=source_id,
                        role=role,
                        status="confirmed",
                        origin="seed",
                    )
                )
            else:
                ref_row.role = role
                ref_row.status = "confirmed"
                ref_row.origin = "seed"
    if missed:
        logger.warning(
            "%d seed mappings skipped: their foods are not in the ref"
            " tables yet; run menu-fdc-import",
            missed,
        )

    # Reviewed-and-rejected candidates (REJECTED_REFS above): mark the
    # matching candidate 'rejected' so --ref-review stops showing it,
    # never a confirmed mapping. An ingredient the parser has not
    # created yet is skipped — there is nothing to reject — and the
    # next startup applies the rejection once a line resolves to it.
    for name, source_name, source_food_id in REJECTED_REFS:
        named = session.scalar(select(Ingredient).where(Ingredient.name == name))
        if named is None:
            continue
        ref_food = session.execute(
            select(RefFood.id, RefFood.source_id)
            .join(FoodSource, FoodSource.id == RefFood.source_id)
            .where(
                FoodSource.name == source_name,
                RefFood.source_food_id == source_food_id,
            )
        ).first()
        if ref_food is None:
            missed += 1
            continue
        ref_food_id, source_id = ref_food
        ref_row = session.scalar(
            select(IngredientFoodRef).where(
                IngredientFoodRef.ingredient_id == named.id,
                IngredientFoodRef.ref_food_id == ref_food_id,
            )
        )
        if ref_row is None:
            session.add(
                IngredientFoodRef(
                    ingredient_id=named.id,
                    ref_food_id=ref_food_id,
                    source_id=source_id,
                    status="rejected",
                    origin="seed",
                )
            )
        elif ref_row.status == "confirmed":
            logger.warning(
                "REJECTED_REFS lists %s / %s %s, but a confirmed"
                " mapping exists; not rejecting it",
                name,
                source_name,
                source_food_id,
            )
        else:
            ref_row.status = "rejected"

    # Conversion rows from the confirmed conversion reference's
    # portions: every size and named unit becomes a `portion` row, so
    # a line converts through its own size ("large" egg -> 50 g)
    # instead of the one factor the old ingredient columns could
    # hold. The medium portion also yields the unit_weight row and
    # the first volumetric one (by seq_num) the density row — the
    # same two factors the old derivation wrote. One source's
    # portions only, so weights never mix sources; seeded rows win
    # over these at read time.
    rows = session.execute(
        select(Ingredient.id, FoodSource.name, RefPortion)
        .join(IngredientFoodRef, IngredientFoodRef.ingredient_id == Ingredient.id)
        .join(RefFood, RefFood.id == IngredientFoodRef.ref_food_id)
        .join(FoodSource, FoodSource.id == RefFood.source_id)
        .join(RefPortion, RefPortion.ref_food_id == RefFood.id)
        .where(
            IngredientFoodRef.role == "conversion",
            IngredientFoodRef.status == "confirmed",
        )
    ).all()
    by_ingredient: dict[int, dict[str, list[RefPortion]]] = {}
    for ingredient_id, source_name, portion in rows:
        by_source = by_ingredient.setdefault(ingredient_id, {})
        by_source.setdefault(source_name, []).append(portion)
    derived = 0
    for ingredient_id, by_source in by_ingredient.items():
        # One source's portions only, so weights never mix sources.
        best = min(by_source, key=conversion_rank)
        for portion in by_source[best]:
            if portion.unit is None or not portion.amount:
                continue
            produced.add(
                _upsert_conversion(
                    ingredient_id,
                    "portion",
                    portion.unit,
                    portion.modifier,
                    portion.gram_weight / portion.amount,
                    best,
                    session,
                    ref_food_id=portion.ref_food_id,
                    ref_seq_num=portion.seq_num,
                )
            )
            derived += 1
        medium = next(
            (p for p in by_source[best] if p.unit == "medium" and p.amount), None
        )
        if medium is not None and medium.amount:
            produced.add(
                _upsert_conversion(
                    ingredient_id,
                    "unit_weight",
                    None,
                    None,
                    medium.gram_weight / medium.amount,
                    best,
                    session,
                    ref_food_id=medium.ref_food_id,
                    ref_seq_num=medium.seq_num,
                )
            )
            derived += 1
        for portion in sorted(by_source[best], key=lambda p: p.seq_num):
            if portion.unit is None or not portion.amount:
                continue
            ml = unit_ml(singular(portion.unit))
            if ml is None:
                continue
            produced.add(
                _upsert_conversion(
                    ingredient_id,
                    "density",
                    None,
                    None,
                    portion.gram_weight / (portion.amount * ml),
                    best,
                    session,
                    ref_food_id=portion.ref_food_id,
                    ref_seq_num=portion.seq_num,
                )
            )
            derived += 1
            break
    if derived:
        logger.info("Derived %d conversion rows from reference portions", derived)

    # A row whose key nothing produced any more — a seed entry removed
    # from seed.py, a re-imported reference — is stale data: delete
    # it. Upserts kept every surviving row's id, so lines pointing at
    # them stay valid.
    for row in session.scalars(select(IngredientConversion)):
        if (
            row.ingredient_id,
            row.origin,
            row.kind,
            row.unit,
            row.modifier,
        ) not in produced:
            session.delete(row)
