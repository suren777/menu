"""Tests for the hand-seeded ingredient data."""

from typing import TYPE_CHECKING

from menu.db.database import Ingredient, IngredientAlias
from menu.db.ingredients import repository
from menu.db.ingredients.seed import seed_ingredient_data

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
