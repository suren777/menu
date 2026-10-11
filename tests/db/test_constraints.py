"""The database refuses what the schema promises it refuses: each test
exercises a constraint directly, not the code around it. The scratch
DB is not migrated (rebuild is the migration), so these run against
freshly created tables."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
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


def _add_ref_fixture(session: Session) -> tuple[Ingredient, RefFood, RefFood]:
    """An ingredient and two foods, each from its own source: the
    minimum real parents so a refusal below means the constraint, not
    a missing foreign key."""
    fdc = FoodSource(
        name="fdc", version="1", licence="public domain", citation="FDC"
    )
    cnf = FoodSource(name="cnf", version="1", licence="test", citation="CNF")
    session.add_all([fdc, cnf])
    session.flush()
    ingredient = Ingredient(name="milk")
    session.add(ingredient)
    session.flush()
    fdc_food = RefFood(
        source_id=fdc.id, source_food_id="1", description="Milk, whole"
    )
    cnf_food = RefFood(
        source_id=cnf.id, source_food_id="1", description="Lait, entier"
    )
    session.add_all([fdc_food, cnf_food])
    session.flush()
    return ingredient, fdc_food, cnf_food


def test_unknown_food_ref_role_is_refused(session: Session) -> None:
    ingredient, fdc_food, _ = _add_ref_fixture(session)
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=fdc_food.id,
            source_id=fdc_food.source_id,
            role="guessing",  # only conversion | nutrition | NULL
            status="candidate",
            origin="parser",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_food_ref_status_is_refused(session: Session) -> None:
    ingredient, fdc_food, _ = _add_ref_fixture(session)
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=fdc_food.id,
            source_id=fdc_food.source_id,
            role="nutrition",
            status="tentative",  # only candidate | confirmed | rejected
            origin="seed",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_confirmed_food_ref_without_role_is_refused(session: Session) -> None:
    """A confirmed mapping has decided what the reference is for;
    only candidates (and rejections) may sit roleless."""
    ingredient, fdc_food, _ = _add_ref_fixture(session)
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=fdc_food.id,
            source_id=fdc_food.source_id,
            role=None,
            status="confirmed",
            origin="seed",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_food_ref_source_id_must_match_the_food(session: Session) -> None:
    """The denormalised source_id is part of the composite FK: it can
    only name the ref_food's own source."""
    ingredient, fdc_food, cnf_food = _add_ref_fixture(session)
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=fdc_food.id,
            source_id=cnf_food.source_id,  # not fdc_food's source
            role="nutrition",
            status="confirmed",
            origin="seed",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_one_confirmed_reference_per_role_per_source(session: Session) -> None:
    """The partial unique index: two confirmed nutrition references
    from the same source for one ingredient cannot both stand."""
    ingredient, fdc_food, _ = _add_ref_fixture(session)
    second_fdc_food = RefFood(
        source_id=fdc_food.source_id,
        source_food_id="2",
        description="Milk, skim",
    )
    session.add(second_fdc_food)
    session.flush()
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=fdc_food.id,
            source_id=fdc_food.source_id,
            role="nutrition",
            status="confirmed",
            origin="seed",
        )
    )
    session.flush()
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=second_fdc_food.id,
            source_id=second_fdc_food.source_id,
            role="nutrition",
            status="confirmed",
            origin="seed",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_confirmed_references_from_two_sources_allowed(session: Session) -> None:
    """One confirmed reference per role per *source*: fdc and cnf may
    each hold the ingredient's nutrition reference — the site's
    nutrition_sources order picks between them."""
    ingredient, fdc_food, cnf_food = _add_ref_fixture(session)
    session.add_all(
        [
            IngredientFoodRef(
                ingredient_id=ingredient.id,
                ref_food_id=fdc_food.id,
                source_id=fdc_food.source_id,
                role="nutrition",
                status="confirmed",
                origin="seed",
            ),
            IngredientFoodRef(
                ingredient_id=ingredient.id,
                ref_food_id=cnf_food.id,
                source_id=cnf_food.source_id,
                role="nutrition",
                status="confirmed",
                origin="seed",
            ),
        ]
    )
    session.flush()  # no IntegrityError: both stand


def test_same_mapping_not_stored_twice(session: Session) -> None:
    """The expression index folds a NULL role into the key, so a
    candidate and its re-suggestion share one row."""
    ingredient, fdc_food, _ = _add_ref_fixture(session)
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=fdc_food.id,
            source_id=fdc_food.source_id,
            status="candidate",
            origin="parser",
        )
    )
    session.flush()
    session.add(
        IngredientFoodRef(
            ingredient_id=ingredient.id,
            ref_food_id=fdc_food.id,
            source_id=fdc_food.source_id,
            status="candidate",
            origin="parser",
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


def test_review_term_with_ingredient_id_is_refused(session: Session) -> None:
    """A review term is unresolved by definition: it must not point at
    a concept."""
    ingredient = Ingredient(name="milk")
    session.add(ingredient)
    session.flush()
    session.add(
        IngredientTerm(
            term="mangled name", ingredient_id=ingredient.id, resolution="review"
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_term_resolution_is_refused(session: Session) -> None:
    """Only seed | auto | review | not_food."""
    ingredient = Ingredient(name="milk")
    session.add(ingredient)
    session.flush()
    session.add(
        IngredientTerm(
            term="milk", ingredient_id=ingredient.id, resolution="guessed"
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_a_line_cascades_to_alternatives(session: Session) -> None:
    """An alternative row exists only through its primary: deleting
    the primary deletes the alternatives (ondelete CASCADE)."""
    recipe = _add_recipe(session)
    session.flush()
    primary = RecipeIngredient(
        recipe_id=recipe.id, position=0, raw_text="rosewater"
    )
    session.add(primary)
    session.flush()
    session.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            position=0,
            raw_text="rosewater or vanilla",
            alternative_of=primary.id,
        )
    )
    session.flush()
    session.delete(primary)
    session.flush()
    assert session.scalars(select(RecipeIngredient)).all() == []
