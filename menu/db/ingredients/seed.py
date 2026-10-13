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

The curated constants themselves live in seed_data.py (data only) and
are re-exported below: modules and tests use (and monkeypatch)
seed.<NAME>, so every function here reads them through this module's
own globals.
"""

import hashlib
import json
import logging
from pathlib import Path
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
from menu.db.ingredients.seed_data import (
    ALIASES,
    CONVERSION_SOURCES,
    DENSITIES_G_PER_ML,
    FDC_IDS,
    FOOD_REFS,
    LINE_OVERRIDES,
    NOT_FOOD,
    PREPARED_FORMS,
    UNIT_WEIGHTS_G,
)
from menu.domain import suggestions
from menu.domain.suggestions import (
    AliasProposal,
    FoodRefProposal,
    LineOverrideProposal,
    NotFoodProposal,
    Suggestion,
    VariantProposal,
)
from menu.domain.units import singular, unit_ml

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Re-exports of the curated data: seeding reads these bindings, so
# monkeypatching them on this module changes what gets seeded.
__all__ = [
    "ALIASES",
    "CONVERSION_SOURCES",
    "DENSITIES_G_PER_ML",
    "FDC_IDS",
    "FOOD_REFS",
    "LINE_OVERRIDES",
    "NOT_FOOD",
    "PREPARED_FORMS",
    "UNIT_WEIGHTS_G",
]


def conversion_rank(source_name: str) -> int:
    """Position in CONVERSION_SOURCES; unlisted sources come last."""
    if source_name in CONVERSION_SOURCES:
        return CONVERSION_SOURCES.index(source_name)
    return len(CONVERSION_SOURCES)


def aliases(
    entries: list[Suggestion] | None = None,
) -> list[tuple[str, str, str | None]]:
    """The hand ALIASES plus accepted alias and variant suggestions as
    (term, canonical, variant) tuples; a hand entry wins when both name
    the same term. An accepted alias with new: true creates its target
    as a seed concept exactly like an ALIASES target. A malformed
    accepted entry cannot load: the ledger validates the proposal."""
    if entries is None:
        entries = suggestions.load()
    merged = {term: (term, target, variant) for term, target, variant in ALIASES}
    for entry in suggestions.accepted(entries, "alias"):
        proposal = entry.proposal
        if not isinstance(proposal, AliasProposal):
            continue
        merged.setdefault(
            proposal.term, (proposal.term, proposal.target, proposal.variant)
        )
    for entry in suggestions.accepted(entries, "variant"):
        proposal = entry.proposal
        if not isinstance(proposal, VariantProposal):
            continue
        merged.setdefault(
            proposal.term, (proposal.term, proposal.target, proposal.variant)
        )
    return list(merged.values())


def line_overrides(entries: list[Suggestion] | None = None) -> dict[str, str]:
    """The hand LINE_OVERRIDES plus accepted line_override suggestions,
    keyed the way the lookup reads them (squashed lowercase line); a
    hand entry wins when both name the same line."""
    if entries is None:
        entries = suggestions.load()
    merged: dict[str, str] = {}
    for entry in suggestions.accepted(entries, "line_override"):
        proposal = entry.proposal
        if not isinstance(proposal, LineOverrideProposal):
            continue
        # The key is normalised the same way as the lookup.
        merged[" ".join(proposal.line.lower().split())] = proposal.name
    merged.update(LINE_OVERRIDES)
    return merged


def not_food(entries: list[Suggestion] | None = None) -> list[str]:
    """The hand NOT_FOOD list plus accepted not_food terms."""
    if entries is None:
        entries = suggestions.load()
    merged = list(NOT_FOOD)
    for entry in suggestions.accepted(entries, "not_food"):
        proposal = entry.proposal
        if not isinstance(proposal, NotFoodProposal):
            continue
        if proposal.term not in merged:
            merged.append(proposal.term)
    return merged


def food_refs(
    entries: list[Suggestion] | None = None,
) -> dict[str, list[tuple[str, str, str]]]:
    """The hand FOOD_REFS plus accepted food_ref suggestions at role
    nutrition (plus conversion when the source is a conversion source);
    a hand entry wins when both name the same ingredient and source."""
    if entries is None:
        entries = suggestions.load()
    merged = {name: list(refs) for name, refs in FOOD_REFS.items()}
    hand = {(name, source) for name, refs in FOOD_REFS.items() for source, _, _ in refs}
    for entry in suggestions.accepted(entries, "food_ref"):
        proposal = entry.proposal
        if not isinstance(proposal, FoodRefProposal):
            continue
        name, source = proposal.ingredient, proposal.source
        if (name, source) in hand:
            continue
        hand.add((name, source))
        roles = ["nutrition"]
        if source in CONVERSION_SOURCES:
            roles.insert(0, "conversion")
        merged.setdefault(name, []).extend(
            (source, proposal.source_food_id, role) for role in roles
        )
    return merged


def rejected_refs(
    entries: list[Suggestion] | None = None,
) -> list[tuple[str, str, str]]:
    """The rejected food_ref suggestions — the human rejections the old
    REJECTED_REFS list curated: (canonical ingredient, source name,
    source_food_id)."""
    if entries is None:
        entries = suggestions.load()
    rejected = []
    for entry in entries:
        if entry.status != "rejected" or entry.kind != "food_ref":
            continue
        proposal = entry.proposal
        if not isinstance(proposal, FoodRefProposal):
            continue
        rejected.append((proposal.ingredient, proposal.source, proposal.source_food_id))
    return rejected


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


def seed_digest() -> str:
    """First 12 hex chars of sha256 over seed_data.py's bytes plus the
    JSON of the accepted suggestions: changes when a hand seed or an
    accepted suggestion changes, so a derivation run can name the
    seed state that produced its numbers."""
    digest = hashlib.sha256((Path(__file__).parent / "seed_data.py").read_bytes())
    accepted = [
        entry.model_dump(mode="json")
        for entry in suggestions.load()
        if entry.status == "accepted"
    ]
    digest.update(json.dumps(accepted, sort_keys=True).encode())
    return digest.hexdigest()[:12]


# An ingredient_conversions unique key: what _upsert_conversion upserts
# by, and what the stale-row sweep at the end of seeding compares.
type ConversionKey = tuple[int, str, str, str | None, str | None]


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
    seed term whose target changed in seed_data.py — unless repoint is
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


def _seed_terms(entries: list[Suggestion], session: Session) -> None:
    """Alias (and variant) terms: each alias points at its canonical
    ingredient, creating the canonical as needed."""
    for alias, canonical, variant in aliases(entries):
        target = _canonical_ingredient(canonical, session)
        _seed_term(alias, target.id, variant, session)


def _seed_not_food(entries: list[Suggestion], session: Session) -> None:
    """Equipment and other non-ingredients: terms with no concept,
    excluded from aggregation and nutrition."""
    for name in not_food(entries):
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


def _seed_densities(session: Session, produced: set[ConversionKey]) -> None:
    """Volume-to-mass factors, g/ml, cooking-standard measured values."""
    for name, density in DENSITIES_G_PER_ML.items():
        ingredient = _canonical_ingredient(name, session)
        produced.add(
            _upsert_conversion(
                ingredient.id, "density", None, None, density, "seed", session
            )
        )


def _seed_unit_weights(session: Session, produced: set[ConversionKey]) -> None:
    """Count-to-mass factors, grams per piece."""
    for name, weight in UNIT_WEIGHTS_G.items():
        ingredient = _canonical_ingredient(name, session)
        produced.add(
            _upsert_conversion(
                ingredient.id, "unit_weight", None, None, weight, "seed", session
            )
        )


def _ref_food(
    source_name: str, source_food_id: str, session: Session
) -> tuple[int, int] | None:
    """The (ref_food id, source id) for a (source, source_food_id)
    pair, or None while that food is not imported yet."""
    row = session.execute(
        select(RefFood.id, RefFood.source_id)
        .join(FoodSource, FoodSource.id == RefFood.source_id)
        .where(
            FoodSource.name == source_name,
            RefFood.source_food_id == source_food_id,
        )
    ).first()
    if row is None:
        return None
    ref_food_id, source_id = row
    return ref_food_id, source_id


def _seeded_ref_row(
    ingredient_id: int, ref_food_id: int, role: str, session: Session
) -> IngredientFoodRef | None:
    """An existing mapping for the (ingredient, food) pair: at the
    role, or roleless — a parser candidate or a curated rejection sits
    there, and the seed confirms it into this role instead of doubling
    the mapping."""
    ref_row = session.scalar(
        select(IngredientFoodRef).where(
            IngredientFoodRef.ingredient_id == ingredient_id,
            IngredientFoodRef.ref_food_id == ref_food_id,
            IngredientFoodRef.role == role,
        )
    )
    if ref_row is None:
        ref_row = session.scalar(
            select(IngredientFoodRef).where(
                IngredientFoodRef.ingredient_id == ingredient_id,
                IngredientFoodRef.ref_food_id == ref_food_id,
                IngredientFoodRef.role.is_(None),
            )
        )
    return ref_row


def _seed_food_refs(entries: list[Suggestion], session: Session) -> None:
    """Reference-food mappings are hand-reviewed (the --ref-review
    report shows what is left): only confirmed mappings feed
    conversion and nutrition. The parser's own suggestions seed
    candidate rows instead (lines.py); rejected ones are recorded in
    suggestions.json and applied by _seed_rejections."""
    missed = 0
    for name, refs in food_refs(entries).items():
        ingredient = _canonical_ingredient(name, session)
        for source_name, source_food_id, role in refs:
            ref_food = _ref_food(source_name, source_food_id, session)
            if ref_food is None:
                missed += 1
                continue
            ref_food_id, source_id = ref_food
            ref_row = _seeded_ref_row(ingredient.id, ref_food_id, role, session)
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


def _seed_rejections(entries: list[Suggestion], session: Session) -> None:
    """Rejected food_ref suggestions (suggestions.json, which replaced
    the old curated REJECTED_REFS list): mark the matching candidate
    'rejected' so --ref-review stops showing it — inserting a rejected
    row when the ingredient exists but no row does — never a confirmed
    mapping (seeding warns instead). Recording the rejection in the
    git-reviewed ledger means a re-crawl cannot resurrect the
    suggestion. An ingredient the parser has not created yet is
    skipped — there is nothing to reject — and the next startup
    applies the rejection once a line resolves to it."""
    for name, source_name, source_food_id in rejected_refs(entries):
        named = session.scalar(select(Ingredient).where(Ingredient.name == name))
        if named is None:
            continue
        ref_food = _ref_food(source_name, source_food_id, session)
        if ref_food is None:
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
                "A rejected suggestion lists %s / %s %s, but a"
                " confirmed mapping exists; not rejecting it",
                name,
                source_name,
                source_food_id,
            )
        else:
            ref_row.status = "rejected"


def _derive_portions(
    ingredient_id: int,
    portions: list[RefPortion],
    source_name: str,
    session: Session,
    produced: set[ConversionKey],
) -> int:
    """Every portion becomes a `portion` row, so a line converts
    through its own size ("large" egg -> 50 g)."""
    derived = 0
    for portion in portions:
        if portion.unit is None or not portion.amount:
            continue
        produced.add(
            _upsert_conversion(
                ingredient_id,
                "portion",
                portion.unit,
                portion.modifier,
                portion.gram_weight / portion.amount,
                source_name,
                session,
                ref_food_id=portion.ref_food_id,
                ref_seq_num=portion.seq_num,
            )
        )
        derived += 1
    return derived


def _derive_unit_weight(
    ingredient_id: int,
    portions: list[RefPortion],
    source_name: str,
    session: Session,
    produced: set[ConversionKey],
) -> int:
    """The medium portion yields the unit_weight row — the same
    factor the old derivation wrote."""
    medium = next((p for p in portions if p.unit == "medium" and p.amount), None)
    if medium is None or not medium.amount:
        return 0
    produced.add(
        _upsert_conversion(
            ingredient_id,
            "unit_weight",
            None,
            None,
            medium.gram_weight / medium.amount,
            source_name,
            session,
            ref_food_id=medium.ref_food_id,
            ref_seq_num=medium.seq_num,
        )
    )
    return 1


def _derive_density(
    ingredient_id: int,
    portions: list[RefPortion],
    source_name: str,
    session: Session,
    produced: set[ConversionKey],
) -> int:
    """The first volumetric portion (by seq_num) yields the density
    row — the same factor the old derivation wrote."""
    for portion in sorted(portions, key=lambda p: p.seq_num):
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
                source_name,
                session,
                ref_food_id=portion.ref_food_id,
                ref_seq_num=portion.seq_num,
            )
        )
        return 1
    return 0


def _seed_derived_conversions(session: Session, produced: set[ConversionKey]) -> None:
    """Conversion rows from the confirmed conversion reference's
    portions: every size and named unit becomes a `portion` row, so
    a line converts through its own size ("large" egg -> 50 g)
    instead of the one factor the old ingredient columns could
    hold. The medium portion also yields the unit_weight row and
    the first volumetric one (by seq_num) the density row — the
    same two factors the old derivation wrote. One source's
    portions only, so weights never mix sources; seeded rows win
    over these at read time."""
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
        portions = by_source[best]
        derived += _derive_portions(ingredient_id, portions, best, session, produced)
        derived += _derive_unit_weight(ingredient_id, portions, best, session, produced)
        derived += _derive_density(ingredient_id, portions, best, session, produced)
    if derived:
        logger.info("Derived %d conversion rows from reference portions", derived)


def _prune_stale_conversions(produced: set[ConversionKey], session: Session) -> None:
    """A row whose key nothing produced any more — a seed entry removed
    from seed_data.py, a re-imported reference — is stale data: delete
    it. Upserts kept every surviving row's id, so lines pointing at
    them stay valid."""
    for row in session.scalars(select(IngredientConversion)):
        if (
            row.ingredient_id,
            row.origin,
            row.kind,
            row.unit,
            row.modifier,
        ) not in produced:
            session.delete(row)


def seed_ingredient_data(session: Session) -> None:
    """Create the seed rows; safe to rerun. Hand seeds merge the
    accepted suggestions (hand entries win conflicts)."""
    entries = suggestions.load()
    _seed_terms(entries, session)
    _seed_not_food(entries, session)
    # Conversion factors live on ingredient_conversions rows; the set
    # collects every key this run writes, so the sweep at the end can
    # delete the rows nothing produced any more.
    produced: set[ConversionKey] = set()
    # Density and unit-weight ingredients are created even when
    # nothing points at them yet: caster sugar is no alias target, so
    # a fresh crawl would otherwise meet it before the next startup
    # and miss its density for a run.
    _seed_densities(session, produced)
    _seed_unit_weights(session, produced)
    _seed_food_refs(entries, session)
    _seed_rejections(entries, session)
    _seed_derived_conversions(session, produced)
    _prune_stale_conversions(produced, session)
