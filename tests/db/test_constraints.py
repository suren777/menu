"""The database refuses what the schema promises it refuses: each test
exercises a constraint directly, not the code around it. The scratch
DB is not migrated (rebuild is the migration), so these run against
freshly created tables."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrition,
    RefFood,
    Sitemap,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_recipe(session: Session) -> Recipe:
    """A recipe row, reached through its page (the ledger)."""
    page = Page(url="https://x/1", site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id,
        name="Test",
        data={},
        data_sha256="x",
        extractor="json_ld",
    )
    session.add(recipe)
    return recipe


def test_unknown_dimension_is_refused(session: Session) -> None:
    session.add(
        RecipeIngredient(
            recipe_id=_add_recipe(session).id,
            position=0,
            raw_text="1 onion",
            dimension="weight",  # not mass/volume/count
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_nutrition_source_is_refused(session: Session) -> None:
    session.add(
        RecipeNutrition(
            recipe_id=_add_recipe(session).id,
            source="guessed",  # only derived | published
            energy_kcal=1.0,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_page_status_is_refused(session: Session) -> None:
    session.add(
        Page(
            url="https://x/page",
            site="bbc_good_food",
            status="pending",  # not a page status
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_sitemap_kind_is_refused(session: Session) -> None:
    session.add(
        Sitemap(url="https://x/sitemap.xml", site="bbc_good_food", kind="feed")
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_sitemap_status_is_refused(session: Session) -> None:
    session.add(
        Sitemap(
            url="https://x/sitemap.xml",
            site="bbc_good_food",
            status="queued",  # pending | done | failed | blocked
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_extractor_is_refused(session: Session) -> None:
    recipe = _add_recipe(session)
    recipe.extractor = "guessing"  # json_ld | test_id | microdata
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_food_ref_role_is_refused(session: Session) -> None:
    ingredient = Ingredient(name="milk")
    source = FoodSource(
        name="fdc", version="1", licence="public domain", citation="FDC"
    )
    session.add_all([ingredient, source])
    session.flush()
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=1,
            role="guessing",  # only conversion | nutrition | NULL
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_ref_food_natural_key_is_unique(session: Session) -> None:
    source = FoodSource(
        name="fdc", version="1", licence="public domain", citation="FDC"
    )
    session.add(source)
    session.flush()
    session.add(
        RefFood(
            source_id=source.id,
            source_food_id="171912",
            description="Milk, whole",
        )
    )
    session.flush()
    session.add(
        RefFood(
            source_id=source.id,
            source_food_id="171912",  # same (source, source_food_id)
            description="Milk, whole, re-imported under a new id",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
