"""Tests for the hand-seeded ingredient data."""

import logging
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientAlias,
    IngredientFoodRef,
    RefFood,
    RefPortion,
)
from menu.db.ingredients import repository
from menu.db.ingredients import seed as seed_module
from menu.db.ingredients.seed import ALIASES, seed_ingredient_data

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_seed_creates_aliases_and_canonicals(session: Session) -> None:
    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    assert milk is not None
    warm = repository.alias_target("warm milk", session)
    assert warm is not None
    assert warm[0].id == milk.id

    flour = repository.find_ingredient_by_name("flour", session)
    assert flour is not None
    assert flour.density_g_per_ml == 0.55


def test_seed_king_arthur_aliases_are_brand_free(session: Session) -> None:
    """King Arthur's brand prefix is stripped from ingredient lines at
    parse time (ingredient_strip_patterns), so the seeded aliases match
    the brand-free names the parser sees."""
    seed_ingredient_data(session)

    for alias in (
        "unbleached all-purpose flour",
        "unbleached bread flour",
        "baker's special dry milk",
        "artisan bread topping",
        "the works bread topping",
    ):
        found = session.query(IngredientAlias).where(IngredientAlias.alias == alias)
        assert found.count() == 1, alias

    assert all("king arthur" not in alias for alias, _, _ in ALIASES)


def test_seed_is_idempotent(session: Session) -> None:
    seed_ingredient_data(session)
    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    assert milk is not None
    aliases = session.query(IngredientAlias).where(IngredientAlias.alias == "warm milk")
    assert aliases.count() == 1


def test_seed_repoints_parse_time_self_aliases(session: Session) -> None:
    """A stored self-alias ("warm milk" -> "warm milk") must end up
    pointing at the canonical ingredient, not be left alone."""
    stale = Ingredient(name="warm milk")
    session.add(stale)
    session.flush()
    session.add(IngredientAlias(alias="warm milk", ingredient_id=stale.id))

    seed_ingredient_data(session)

    milk = repository.find_ingredient_by_name("milk", session)
    repointed = repository.alias_target("warm milk", session)
    assert milk is not None
    assert repointed is not None
    assert repointed[0].id == milk.id


def test_seed_sets_densities_and_weights_on_existing(session: Session) -> None:
    session.add(Ingredient(name="egg"))
    session.flush()

    seed_ingredient_data(session)

    egg = repository.find_ingredient_by_name("egg", session)
    assert egg is not None
    assert egg.unit_weight_g == 50.0


def test_seed_creates_density_ingredients_up_front(session: Session) -> None:
    """No alias points at caster sugar, yet a fresh crawl must see its
    density on the first run — the seed creates the ingredient instead
    of waiting for the crawl to."""
    seed_ingredient_data(session)

    caster = repository.find_ingredient_by_name("caster sugar", session)
    assert caster is not None
    assert caster.density_g_per_ml == 0.85


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
    """No seed override: the confirmed conversion reference's
    portions fill the ingredient's density and unit weight."""
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
    assert garlic.unit_weight_g == 3.0
    assert garlic.density_g_per_ml == pytest.approx(9.0 / 4.928921593749998)


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
    assert any(
        ref.ref_food_id == stale and ref.status == "candidate" for ref in refs
    )


def test_seed_rejects_candidate_from_rejected_refs(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A REJECTED_REFS entry marks the matching candidate 'rejected'
    (inserting one when no row exists) — and a confirmed mapping for
    a different food stays confirmed."""
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

    monkeypatch.setattr(
        seed_module, "REJECTED_REFS", [("baking powder", "fdc", "167806")]
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
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No candidate row: the rejection inserts a rejected row, so a
    re-crawl cannot resurrect the suggestion either."""
    food_id = _add_fdc_food(session, "167806", "Baobab powder")
    powder = Ingredient(name="baking powder")
    session.add(powder)
    session.flush()

    monkeypatch.setattr(
        seed_module, "REJECTED_REFS", [("baking powder", "fdc", "167806")]
    )
    seed_ingredient_data(session)

    ref = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == powder.id)
    ).one()
    assert (ref.ref_food_id, ref.status, ref.role) == (food_id, "rejected", None)


def test_seed_never_rejects_a_confirmed_mapping(
    session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REJECTED_REFS contradicting a FOOD_REFS seed is a curation
    mistake: the confirmed mapping stands and seeding warns."""
    _add_fdc_food(session, "171287", "Egg, whole, raw, fresh")
    seed_ingredient_data(session)
    monkeypatch.setattr(seed_module, "REJECTED_REFS", [("egg", "fdc", "171287")])

    with caplog.at_level(logging.WARNING):
        seed_ingredient_data(session)

    egg = repository.find_ingredient_by_name("egg", session)
    assert egg is not None
    refs = session.scalars(
        select(IngredientFoodRef).where(IngredientFoodRef.ingredient_id == egg.id)
    ).all()
    assert all(ref.status == "confirmed" for ref in refs)
    assert "not rejecting it" in caplog.text


def test_seed_derives_from_fdc_before_cnf(session: Session) -> None:
    """With confirmed conversion references in two sources, the
    weights come from the higher-precedence source's portions only,
    whatever the portions' seq_num."""
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
    garlic.density_g_per_ml = None
    session.flush()

    seed_ingredient_data(session)

    assert garlic.density_g_per_ml == pytest.approx(9.0 / 4.928921593749998)
