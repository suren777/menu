"""Tests for the hand-seeded ingredient data."""

import json
import logging
from typing import TYPE_CHECKING, Any

import pytest
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
from menu.db.ingredients import repository
from menu.db.ingredients import suggestions as suggestions_module
from menu.db.ingredients.seed import ALIASES, NOT_FOOD, seed_ingredient_data

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def term_row(term: str, session: Session) -> IngredientTerm:
    row = session.scalars(
        select(IngredientTerm).where(IngredientTerm.term == term)
    ).one()
    assert isinstance(row, IngredientTerm)
    return row


def _entry(
    kind: str,
    subject: str,
    proposal: dict[str, Any],
    *,
    status: str = "accepted",
) -> dict[str, Any]:
    """One suggestions.json entry. Rejections use the documented human
    shape: input "food_ref", model {"llm": "human"}."""
    human = status == "rejected"
    return {
        "input": kind if human else "queue",
        "kind": kind,
        "subject": subject,
        "proposal": proposal,
        "evidence": {"reason": "test"},
        "model": {"llm": "human" if human else "fake/model"},
        "status": status,
    }


def _write_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *entries: dict[str, Any],
) -> None:
    """Point the suggestions ledger at a temp file holding entries."""
    path = tmp_path / "suggestions.json"
    path.write_text(json.dumps(list(entries)), encoding="utf-8")
    monkeypatch.setattr(suggestions_module, "SUGGESTIONS_PATH", path)


def conversion_row(
    ingredient_id: int, kind: str, unit: str | None, session: Session
) -> IngredientConversion:
    row = session.scalars(
        select(IngredientConversion).where(
            IngredientConversion.ingredient_id == ingredient_id,
            IngredientConversion.kind == kind,
            IngredientConversion.unit == unit,
        )
    ).one()
    assert isinstance(row, IngredientConversion)
    return row


def test_seed_creates_terms_and_canonicals(session: Session) -> None:
    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    assert milk is not None
    warm = term_row("warm milk", session)
    assert (warm.ingredient_id, warm.variant, warm.resolution) == (
        milk.id,
        "warm",
        "seed",
    )

    flour = repository.find_ingredient_by_name("flour", session)
    assert flour is not None
    density = conversion_row(flour.id, "density", None, session)
    assert (density.grams, density.origin) == (0.55, "seed")


def test_seed_creates_a_self_term_for_every_concept(session: Session) -> None:
    """ "milk" resolves through its own seed term, not only through
    variant aliases."""
    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    self_term = term_row("milk", session)
    assert milk is not None
    assert (self_term.ingredient_id, self_term.variant) == (milk.id, None)
    assert self_term.resolution == "seed"


def test_seed_king_arthur_aliases_are_brand_free(session: Session) -> None:
    """King Arthur's brand prefix is stripped from ingredient lines at
    parse time (ingredient_strip_patterns), so the seeded terms match
    the brand-free names the parser sees."""
    seed_ingredient_data(session)

    for term in (
        "unbleached all-purpose flour",
        "unbleached bread flour",
        "baker's special dry milk",
        "artisan bread topping",
        "the works bread topping",
    ):
        found = session.query(IngredientTerm).where(IngredientTerm.term == term)
        assert found.count() == 1, term

    assert all("king arthur" not in term for term, _, _ in ALIASES)


def test_seed_is_idempotent(session: Session) -> None:
    seed_ingredient_data(session)
    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    assert milk is not None
    terms = session.query(IngredientTerm).where(IngredientTerm.term == "warm milk")
    assert terms.count() == 1


def test_seed_repoints_parse_time_self_terms(session: Session) -> None:
    """A stored auto term ("warm milk" -> the "warm milk" concept the
    parser created) must end up pointing at the canonical ingredient,
    not be left alone."""
    stale = Ingredient(name="warm milk")
    session.add(stale)
    session.flush()
    session.add(
        IngredientTerm(term="warm milk", ingredient_id=stale.id, resolution="auto")
    )

    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    repointed = term_row("warm milk", session)
    assert milk is not None
    assert repointed.ingredient_id == milk.id
    assert repointed.resolution == "seed"


def test_seed_marks_equipment_not_food(session: Session) -> None:
    """Equipment the parser calls an ingredient ("12-hole mini bundt
    tin") is seeded as a not_food term with no concept: its lines stay
    stored but are excluded from aggregation and nutrition. A
    parse-time auto term for the same name is flipped."""
    stale = Ingredient(name="12-hole mini bundt tin")
    session.add(stale)
    session.flush()
    session.add(
        IngredientTerm(term=NOT_FOOD[0], ingredient_id=stale.id, resolution="auto")
    )

    seed_ingredient_data(session)

    row = term_row(NOT_FOOD[0], session)
    assert (row.resolution, row.ingredient_id, row.variant) == (
        "not_food",
        None,
        None,
    )


def test_seed_sets_densities_and_weights_on_existing(session: Session) -> None:
    session.add(Ingredient(name="egg"))
    session.flush()

    seed_ingredient_data(session)

    egg = repository.find_ingredient_by_name("egg", session)
    assert egg is not None
    weight = conversion_row(egg.id, "unit_weight", None, session)
    assert (weight.grams, weight.origin) == (50.0, "seed")


def test_seed_creates_density_ingredients_up_front(session: Session) -> None:
    """No alias points at caster sugar, yet a fresh crawl must see its
    density on the first run — the seed creates the ingredient instead
    of waiting for the crawl to."""
    seed_ingredient_data(session)

    caster = repository.find_ingredient_by_name("caster sugar", session)
    assert caster is not None
    density = conversion_row(caster.id, "density", None, session)
    assert (density.grams, density.origin) == (0.85, "seed")


def _add_fdc_food(session: Session, source_food_id: str, description: str) -> int:
    source = session.scalars(select(FoodSource)).first()
    if source is None:
        source = FoodSource(
            name="fdc", version="test", licence="public domain", citation="test"
        )
        session.add(source)
        session.flush()
    food = RefFood(
        source_id=source.id,
        source_food_id=source_food_id,
        description=description,
    )
    session.add(food)
    session.flush()
    return food.id


def test_seed_derives_weights_from_reference_portions(session: Session) -> None:
    """No seed override: the confirmed conversion reference's portions
    become portion rows, plus the unit weight (the medium portion)
    and the density (the first volumetric portion) they imply."""
    food_id = _add_fdc_food(session, "169230", "Garlic, raw")
    session.add(
        RefPortion(
            ref_food_id=food_id,
            seq_num=1,
            amount=1.0,
            unit="tsp",
            modifier="tsp",
            gram_weight=9.0,
        )
    )
    session.add(
        RefPortion(
            ref_food_id=food_id,
            seq_num=2,
            amount=1.0,
            unit="medium",
            modifier="medium",
            gram_weight=3.0,
        )
    )
    session.flush()

    seed_ingredient_data(session)

    garlic = repository.find_ingredient_by_name("garlic", session)
    assert garlic is not None
    medium = conversion_row(garlic.id, "portion", "medium", session)
    assert (medium.grams, medium.origin, medium.ref_food_id, medium.ref_seq_num) == (
        3.0,
        "fdc",
        food_id,
        2,
    )
    assert conversion_row(garlic.id, "portion", "tsp", session).grams == 9.0
    weight = conversion_row(garlic.id, "unit_weight", None, session)
    assert (weight.grams, weight.origin) == (3.0, "fdc")
    density = conversion_row(garlic.id, "density", None, session)
    assert density.grams == pytest.approx(9.0 / 4.928921593749998)


def test_seed_confirms_fdc_ids(session: Session) -> None:
    _add_fdc_food(session, "171287", "Egg, whole, raw, fresh")
    seed_ingredient_data(session)

    egg = repository.find_ingredient_by_name("egg", session)
    assert egg is not None
    refs = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == egg.id)
    ).all()
    assert [(ref.role, ref.status) for ref in refs] == [
        ("conversion", "confirmed"),
        ("nutrition", "confirmed"),
    ]


def test_seed_overrides_stale_parser_candidates(session: Session) -> None:
    """The parser's suggestion is often wrong (baking powder once
    resolved to "Baobab powder"); the confirmed seed wins and the
    stale candidate stays a candidate."""
    stale = _add_fdc_food(session, "167806", "Baobab powder")
    baking = _add_fdc_food(session, "172803", "Leavening agents, baking powder")
    powder = Ingredient(name="baking powder")
    session.add(powder)
    session.flush()
    stale_food = session.get(RefFood, stale)
    assert stale_food is not None
    session.add(
        IngredientFoodRef(
            ingredient_id=powder.id,
            ref_food_id=stale,
            source_id=stale_food.source_id,
            status="candidate",
            origin="parser",
        )
    )

    seed_ingredient_data(session)

    refs = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == powder.id)
    ).all()
    assert {ref.ref_food_id for ref in refs if ref.status == "confirmed"} == {baking}
    assert any(ref.ref_food_id == stale and ref.status == "candidate" for ref in refs)


def test_seed_rejects_candidate_from_rejected_refs(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected food_ref suggestion marks the matching candidate
    'rejected' (inserting one when no row exists) — and a confirmed
    mapping for a different food stays confirmed. suggestions.json
    replaced the old seed.REJECTED_REFS list."""
    stale = _add_fdc_food(session, "167806", "Baobab powder")
    baking = _add_fdc_food(session, "172803", "Leavening agents, baking powder")
    powder = Ingredient(name="baking powder")
    session.add(powder)
    session.flush()
    stale_food = session.get(RefFood, stale)
    assert stale_food is not None
    session.add(
        IngredientFoodRef(
            ingredient_id=powder.id,
            ref_food_id=stale,
            source_id=stale_food.source_id,
            status="candidate",
            origin="parser",
        )
    )
    session.flush()

    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "food_ref",
            "baking powder",
            {
                "ingredient": "baking powder",
                "source": "fdc",
                "source_food_id": "167806",
            },
            status="rejected",
        ),
    )
    seed_ingredient_data(session)

    rows = {
        ref.ref_food_id: ref.status
        for ref in session.scalars(
            select(IngredientFoodRef).where(
                IngredientFoodRef.ingredient_id == powder.id
            )
        )
    }
    assert rows == {stale: "rejected", baking: "confirmed"}


def test_seed_rejects_without_a_candidate(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No candidate row: the rejection inserts a rejected row, so a
    re-crawl cannot resurrect the suggestion either."""
    food_id = _add_fdc_food(session, "167806", "Baobab powder")
    powder = Ingredient(name="baking powder")
    session.add(powder)
    session.flush()

    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "food_ref",
            "baking powder",
            {
                "ingredient": "baking powder",
                "source": "fdc",
                "source_food_id": "167806",
            },
            status="rejected",
        ),
    )
    seed_ingredient_data(session)

    ref = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == powder.id)
    ).one()
    assert (ref.ref_food_id, ref.status, ref.role) == (food_id, "rejected", None)


def test_seed_never_rejects_a_confirmed_mapping(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rejection contradicting a FOOD_REFS seed is a curation
    mistake: the confirmed mapping stands and seeding warns."""
    _add_fdc_food(session, "171287", "Egg, whole, raw, fresh")
    seed_ingredient_data(session)

    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "food_ref",
            "egg",
            {"ingredient": "egg", "source": "fdc", "source_food_id": "171287"},
            status="rejected",
        ),
    )
    with caplog.at_level(logging.WARNING):
        seed_ingredient_data(session)

    egg = repository.find_ingredient_by_name("egg", session)
    assert egg is not None
    refs = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == egg.id)
    ).all()
    assert all(ref.status == "confirmed" for ref in refs)
    assert "not rejecting it" in caplog.text


def test_accepted_alias_with_new_creates_the_seed_concept(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """new: true: the accepted alias's target is created as a seed
    concept with its self-term, exactly like an ALIASES target."""
    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "alias",
            "aubergine",
            {"term": "aubergine", "target": "eggplant", "variant": None, "new": True},
        ),
    )

    seed_ingredient_data(session)

    eggplant = session.scalars(
        select(Ingredient).where(Ingredient.name == "eggplant")
    ).one()
    assert eggplant.origin == "seed"
    term = term_row("aubergine", session)
    assert (term.ingredient_id, term.variant, term.resolution) == (
        eggplant.id,
        None,
        "seed",
    )
    self_term = term_row("eggplant", session)
    assert (self_term.ingredient_id, self_term.resolution) == (eggplant.id, "seed")


def test_pending_or_rejected_alias_does_nothing(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only accepted entries seed: pending proposals wait for review
    and rejected ones stay out."""
    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "alias",
            "aubergine",
            {"term": "aubergine", "target": "eggplant", "variant": None},
            status="pending",
        ),
        _entry(
            "alias",
            "rocket",
            {"term": "rocket", "target": "arugula", "variant": None},
            status="rejected",
        ),
    )

    seed_ingredient_data(session)

    assert (
        session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "aubergine")
        ).first()
        is None
    )
    assert (
        session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "rocket")
        ).first()
        is None
    )
    assert repository.find_ingredient_by_name("eggplant", session) is None
    assert repository.find_ingredient_by_name("arugula", session) is None


def test_hand_alias_beats_conflicting_accepted_suggestion(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand seeds win: an accepted suggestion re-proposing a term the
    hand ALIASES already name changes nothing."""
    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "alias",
            "warm milk",
            {"term": "warm milk", "target": "cream", "variant": None},
        ),
    )

    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    term = term_row("warm milk", session)
    assert milk is not None
    assert (term.ingredient_id, term.variant, term.resolution) == (
        milk.id,
        "warm",
        "seed",
    )


def test_accepted_not_food_marks_the_term(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted not_food suggestion acts like a NOT_FOOD entry: the
    term is marked not_food, an existing auto term is flipped."""
    stale = Ingredient(name="stand mixer")
    session.add(stale)
    session.flush()
    session.add(
        IngredientTerm(term="stand mixer", ingredient_id=stale.id, resolution="auto")
    )

    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry("not_food", "stand mixer", {"term": "stand mixer"}),
    )

    seed_ingredient_data(session)

    row = term_row("stand mixer", session)
    assert (row.resolution, row.ingredient_id, row.variant) == ("not_food", None, None)


def test_accepted_food_ref_creates_confirmed_rows(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted food_ref suggestion confirms the mapping at role
    nutrition — plus conversion, fdc being a conversion source — and
    creates the ingredient if missing, like FOOD_REFS does."""
    _add_fdc_food(session, "1269801", "Seeds, sesame butter, tahini")
    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "food_ref",
            "tahini",
            {"ingredient": "tahini", "source": "fdc", "source_food_id": "1269801"},
        ),
    )

    seed_ingredient_data(session)

    tahini = session.scalars(
        select(Ingredient).where(Ingredient.name == "tahini")
    ).one()
    assert tahini.origin == "seed"
    refs = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == tahini.id)
    ).all()
    assert sorted((ref.role, ref.status) for ref in refs) == [
        ("conversion", "confirmed"),
        ("nutrition", "confirmed"),
    ]


def test_hand_food_ref_beats_conflicting_accepted_suggestion(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand seeds win per (ingredient, source): an accepted food_ref
    suggesting another food for a hand-seeded mapping changes nothing."""
    food_id = _add_fdc_food(session, "171287", "Egg, whole, raw, fresh")
    _add_fdc_food(session, "167806", "Baobab powder")
    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry(
            "food_ref",
            "egg",
            {"ingredient": "egg", "source": "fdc", "source_food_id": "167806"},
        ),
    )

    seed_ingredient_data(session)

    egg = repository.find_ingredient_by_name("egg", session)
    assert egg is not None
    refs = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == egg.id)
    ).all()
    assert refs
    assert all(ref.ref_food_id == food_id and ref.status == "confirmed" for ref in refs)


def test_malformed_accepted_suggestion_is_skipped(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed accepted entry (a missing proposal key) is logged
    and skipped, never a seeding crash."""
    _write_suggestions(
        tmp_path,
        monkeypatch,
        _entry("alias", "aubergine", {"term": "aubergine"}),  # no target
    )

    with caplog.at_level(logging.WARNING):
        seed_ingredient_data(session)

    assert "malformed" in caplog.text
    assert (
        session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "aubergine")
        ).first()
        is None
    )


def test_seed_derives_from_fdc_before_cnf(session: Session) -> None:
    """With confirmed conversion references in two sources, the
    derived rows come from the higher-precedence source's portions
    only, whatever the portions' seq_num."""
    fdc_food = _add_fdc_food(session, "169230", "Garlic, raw")
    session.add(
        RefPortion(
            ref_food_id=fdc_food,
            seq_num=5,
            amount=1.0,
            unit="tsp",
            modifier="tsp",
            gram_weight=9.0,
        )
    )
    session.flush()
    seed_ingredient_data(session)
    garlic = session.scalars(
        select(Ingredient).where(Ingredient.name == "garlic")
    ).one()

    cnf = FoodSource(name="cnf", version="test", licence="test", citation="test")
    session.add(cnf)
    session.flush()
    cnf_food = RefFood(source_id=cnf.id, source_food_id="1", description="Garlic")
    session.add(cnf_food)
    session.flush()
    session.add_all(
        [
            RefPortion(
                ref_food_id=cnf_food.id,
                seq_num=1,
                amount=1.0,
                unit="tsp",
                modifier=None,
                gram_weight=5.0,
            ),
            IngredientFoodRef(
                ingredient_id=garlic.id,
                ref_food_id=cnf_food.id,
                source_id=cnf_food.source_id,
                role="conversion",
                status="confirmed",
                origin="seed",
            ),
        ]
    )
    session.flush()

    seed_ingredient_data(session)

    rows = session.scalars(
        select(IngredientConversion).where(
            IngredientConversion.ingredient_id == garlic.id
        )
    ).all()
    # The cnf 5 g portion produced nothing: weights never mix sources.
    assert all(row.origin == "fdc" for row in rows)
    assert sorted((row.kind, row.unit, row.grams) for row in rows) == [
        ("density", None, pytest.approx(9.0 / 4.928921593749998)),
        ("portion", "tsp", 9.0),
    ]


def test_seed_creates_all_portion_sizes(session: Session) -> None:
    """Every ref portion size becomes a portion row, so a line can
    convert through its own size."""
    food_id = _add_fdc_food(session, "171287", "Egg, whole, raw, fresh")
    for seq_num, (unit, grams) in enumerate(
        (
            ("small", 38.0),
            ("medium", 44.0),
            ("large", 50.0),
            ("cup (4.86 large eggs)", 243.0),
        ),
        start=1,
    ):
        session.add(
            RefPortion(
                ref_food_id=food_id,
                seq_num=seq_num,
                amount=1.0,
                unit=unit,
                modifier=unit,
                gram_weight=grams,
            )
        )
    session.flush()

    seed_ingredient_data(session)

    egg = repository.find_ingredient_by_name("egg", session)
    assert egg is not None
    portions = session.scalars(
        select(IngredientConversion)
        .where(
            IngredientConversion.ingredient_id == egg.id,
            IngredientConversion.kind == "portion",
        )
        .order_by(IngredientConversion.unit)
    ).all()
    assert [(row.unit, row.grams, row.origin) for row in portions] == [
        ("cup (4.86 large eggs)", 243.0, "fdc"),
        ("large", 50.0, "fdc"),
        ("medium", 44.0, "fdc"),
        ("small", 38.0, "fdc"),
    ]


def test_reseeding_keeps_conversion_ids_stable(session: Session) -> None:
    """Upsert by the unique key: re-seeding never re-ids a surviving
    row, so lines can keep pointing at it."""
    seed_ingredient_data(session)
    before = [
        (row.id, row.ingredient_id, row.kind, row.unit, row.origin)
        for row in session.scalars(
            select(IngredientConversion).order_by(IngredientConversion.id)
        )
    ]

    seed_ingredient_data(session)

    after = [
        (row.id, row.ingredient_id, row.kind, row.unit, row.origin)
        for row in session.scalars(
            select(IngredientConversion).order_by(IngredientConversion.id)
        )
    ]
    assert before
    assert after == before
