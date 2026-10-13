"""Nutrition report tests: validation against published values,
run-to-run comparison, pruning, and the set-based derivation's
statement count."""

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event, select

from menu.db.connection import get_session
from menu.db.database import (
    DerivationRun,
    FoodSource,
    Ingredient,
    IngredientFoodRef,
    IngredientTerm,
    Page,
    Recipe,
    RecipeIngredient,
    RecipeNutrient,
    RecipeNutritionRun,
    RecipePublishedNutrient,
    RefFood,
)
from menu.db.ingredients import seed as seed_module
from menu.domain import suggestions
from menu.domain.suggestions import AliasProposal, Suggestion
from menu.nutrition.reports import (
    coverage_report,
    derive_all_nutrition,
    latest_run_id,
    run_comparison,
    run_comparison_report,
    validation_report,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def _add_recipe(
    session: Session, url: str, name: str, servings: int | None = None
) -> Recipe:
    """A bare recipe row, reached through its page (the ledger)."""
    page = Page(url=url, site="bbc_good_food")
    session.add(page)
    session.flush()
    recipe = Recipe(
        page_id=page.id,
        name=name,
        data={},
        data_sha256=url,
        extractor="json_ld",
        servings=servings,
    )
    session.add(recipe)
    session.flush()
    return recipe


def _add_run(
    session: Session,
    recipes: list[tuple[int, float | None, dict[str, float]]],
    *,
    seed_digest: str = "digest",
    parser_version: str = "test",
) -> DerivationRun:
    """A hand-made derivation run: per-recipe coverage and nutrient
    amounts, as the derivation would have stored them."""
    run = DerivationRun(
        kind="nutrition",
        parser_version=parser_version,
        seed_digest=seed_digest,
        sources={"fdc": "test"},
        note=None,
    )
    session.add(run)
    session.flush()
    for recipe_id, coverage, nutrients in recipes:
        session.add(
            RecipeNutritionRun(
                run_id=run.id,
                recipe_id=recipe_id,
                coverage=coverage,
                unconverted_lines=0,
                sources=["fdc"],
                definitions_mixed=False,
            )
        )
        session.flush()
        for nutrient, amount in nutrients.items():
            session.add(
                RecipeNutrient(
                    run_id=run.id,
                    recipe_id=recipe_id,
                    nutrient=nutrient,
                    amount=amount,
                    definitions=["kcal"],
                    sources=["fdc"],
                )
            )
    return run


def _add_published(
    session: Session, recipe_id: int, nutrient: str, amount: float
) -> None:
    session.add(
        RecipePublishedNutrient(
            recipe_id=recipe_id,
            nutrient=nutrient,
            amount=amount,
            raw_text=f"{amount} {nutrient}",
        )
    )


@pytest.mark.usefixtures("db_engine")
def test_validation_report_compares_per_serving(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Derived totals are divided by servings before comparing with
    the published per-serving values, from the run's rows."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/omelette", "Omelette", servings=2)
        _add_published(session, recipe.id, "energy_kcal", 100.0)
        run_id = _add_run(session, [(recipe.id, 0.95, {"energy_kcal": 250.0})]).id

    with get_session() as session:
        validation_report(session)
    out = capsys.readouterr().out
    assert f"Validating derivation run {run_id}" in out
    assert "bbc_good_food via fdc: 1 recipes" in out
    assert "25%" in out
    assert "Worst recipes by kcal error:" in out


@pytest.mark.usefixtures("db_engine")
def test_validation_report_validates_the_named_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--run names the run to validate, not necessarily the latest."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/omelette", "Omelette", servings=2)
        _add_published(session, recipe.id, "energy_kcal", 100.0)
        older_id = _add_run(session, [(recipe.id, 0.95, {"energy_kcal": 250.0})]).id
        _add_run(session, [(recipe.id, 0.95, {"energy_kcal": 120.0})])

    with get_session() as session:
        validation_report(session, run_id=older_id)
    out = capsys.readouterr().out
    assert f"Validating derivation run {older_id}" in out
    assert "25%" in out  # the older run's 250 kcal, not the latest's 120


@pytest.mark.usefixtures("db_engine")
def test_validation_report_without_a_run_prints_the_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No run yet: say what to do first instead of an empty report."""
    with get_session() as session:
        validation_report(session)
    assert "Nothing to validate: run derive-nutrition first" in capsys.readouterr().out


@pytest.mark.usefixtures("db_engine")
def test_validation_report_skips_low_coverage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A derivation covering under 90% of the grams is not compared —
    low coverage is marked, not trusted."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/soup", "Soup", servings=2)
        _add_published(session, recipe.id, "energy_kcal", 100.0)
        _add_run(session, [(recipe.id, 0.5, {"energy_kcal": 250.0})])

    with get_session() as session:
        validation_report(session)
    assert "bbc_good_food" not in capsys.readouterr().out


@pytest.mark.usefixtures("db_engine")
def test_validation_report_skips_zero_servings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Some BBC recipes publish a yield of 0; there is no per-serving
    value to compare, so they are skipped rather than divided by."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/stock", "Stock", servings=0)
        _add_published(session, recipe.id, "energy_kcal", 100.0)
        _add_run(session, [(recipe.id, 0.95, {"energy_kcal": 250.0})])

    with get_session() as session:
        validation_report(session)
    assert "bbc_good_food" not in capsys.readouterr().out


@pytest.mark.usefixtures("db_engine")
def test_latest_run_id_is_the_newest() -> None:
    """The newest nutrition run's id, or None before the first."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/x", "X")
        assert latest_run_id(session) is None
        first = _add_run(session, [(recipe.id, 0.9, {})])
        second = _add_run(session, [(recipe.id, 0.9, {})])
        assert latest_run_id(session) == second.id
        assert second.id > first.id


@pytest.mark.usefixtures("db_engine")
def test_run_comparison_counts_changes() -> None:
    """Per nutrient in COMPARED: the recipes moved by more than 1% and
    the median relative change; mean coverage of each run; the recipes
    whose coverage changed."""
    with get_session() as session:
        recipe_a = _add_recipe(session, "https://x/a", "A", servings=2)
        recipe_b = _add_recipe(session, "https://x/b", "B", servings=2)
        against_id = _add_run(
            session,
            [
                (recipe_a.id, 0.9, {"energy_kcal": 100.0}),
                (recipe_b.id, 0.9, {"energy_kcal": 100.0}),
            ],
            seed_digest="old-seed",
            parser_version="1.0",
        ).id
        run_id = _add_run(
            session,
            [
                (recipe_a.id, 0.95, {"energy_kcal": 150.0}),
                (recipe_b.id, 0.9, {"energy_kcal": 100.0}),
            ],
            seed_digest="new-seed",
            parser_version="1.1",
        ).id

    with get_session() as session:
        comparison = run_comparison(session, run_id, against_id)

    assert comparison.run.seed_digest == "new-seed"
    assert comparison.run.parser_version == "1.1"
    assert comparison.against.seed_digest == "old-seed"
    assert comparison.against.parser_version == "1.0"
    changed, median_change = comparison.nutrients["energy_kcal"]
    assert changed == 1  # only recipe A moved (recipe B is unchanged)
    assert median_change == pytest.approx(0.25)  # median of 50% and 0%
    assert comparison.mean_coverage == pytest.approx((0.95 + 0.9) / 2)
    assert comparison.mean_coverage_against == pytest.approx(0.9)
    assert comparison.coverage_changed == 1


@pytest.mark.usefixtures("db_engine")
def test_run_comparison_report_prints_provenance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both runs' ids, seed digests and parser versions, so a reviewer
    sees what changed."""
    with get_session() as session:
        recipe = _add_recipe(session, "https://x/a", "A", servings=2)
        against_id = _add_run(
            session,
            [(recipe.id, 0.9, {"energy_kcal": 100.0})],
            seed_digest="old-seed",
            parser_version="1.0",
        ).id
        run_id = _add_run(
            session,
            [(recipe.id, 0.9, {"energy_kcal": 150.0})],
            seed_digest="new-seed",
            parser_version="1.1",
        ).id

    with get_session() as session:
        run_comparison_report(session, run_id, against_id)
    out = capsys.readouterr().out
    assert f"Run {against_id}: seed old-seed, parser 1.0" in out
    assert f"Run {run_id}: seed new-seed, parser 1.1" in out
    assert "Mean coverage: 90% -> 90%" in out
    assert "Recipes whose coverage changed: 0" in out
    assert "energy_kcal: 1 changed by more than 1%, median change 50.0%" in out


@pytest.mark.usefixtures("db_engine")
def test_prune_runs_default_keeps_ten() -> None:
    """derive_all_nutrition prunes to the last 10 runs by default."""
    with get_session() as session:
        for n in range(11):
            _add_recipe(session, f"https://x/{n}", f"R{n}")

    for _ in range(11):
        with get_session() as session:
            derive_all_nutrition(session)

    with get_session() as session:
        runs = list(
            session.scalars(select(DerivationRun.id).order_by(DerivationRun.id))
        )
        assert len(runs) == 10
        assert runs[0] == 2  # the first run was pruned


@pytest.mark.usefixtures("db_engine")
def test_derivation_statement_count_does_not_grow_with_lines(
    db_engine: Engine,
) -> None:
    """One batch of recipes costs the same number of SQL statements
    whether its recipes have few or many lines: the reference data is
    loaded once and derivation happens in memory."""

    def _add_recipe_with_lines(url: str, line_count: int, session: Session) -> None:
        """A recipe with `line_count` plain lines (no terms, so they
        derive as unconverted)."""
        page = Page(url=url, site="bbc_good_food")
        session.add(page)
        session.flush()
        recipe = Recipe(
            page_id=page.id, name=url, data={}, data_sha256=url, extractor="json_ld"
        )
        session.add(recipe)
        session.flush()
        for position in range(line_count):
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id, position=position, raw_text=f"line {position}"
                )
            )

    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        with get_session() as session:
            for n in range(3):
                _add_recipe_with_lines(f"https://x/{n}", 2, session)
        statements.clear()
        with get_session() as session:
            derive_all_nutrition(session)
        three_recipes = len(statements)

        with get_session() as session:
            for n in range(3, 9):
                _add_recipe_with_lines(f"https://x/{n}", 4, session)
        statements.clear()
        with get_session() as session:
            derive_all_nutrition(session)
        six_recipes = len(statements)
    finally:
        event.remove(db_engine, "before_cursor_execute", record)

    assert three_recipes > 0
    # 3 recipes with 2 lines each vs 6 recipes with 4 lines each: the
    # same statement count, whatever the line count.
    assert six_recipes == three_recipes


@pytest.mark.usefixtures("db_engine")
def test_seed_digest_changes_with_an_accepted_suggestion(
    tmp_path: Path,
) -> None:
    """The digest covers seed_data.py plus the accepted ledger entries:
    a pending suggestion changes nothing, accepting it does."""
    with suggestions.use_ledger(tmp_path / "suggestions.json"):
        before = seed_module.seed_digest()
        suggestions.save(
            [
                Suggestion(
                    input="audit",
                    kind="alias",
                    subject="coriander leaf",
                    proposal=AliasProposal(term="coriander leaf", target="cilantro"),
                    evidence={"reason": "same food"},
                    model={"llm": "test"},
                )
            ]
        )
        assert seed_module.seed_digest() == before
        entries = suggestions.load()
        suggestions.save(
            [entry.model_copy(update={"status": "accepted"}) for entry in entries]
        )
        accepted = seed_module.seed_digest()
        assert accepted != before
        # And it is the sha256 prefix over the file bytes plus the
        # accepted entries' JSON.
        digest = hashlib.sha256(
            (Path(seed_module.__file__).parent / "seed_data.py").read_bytes()
        )
        digest.update(
            json.dumps(
                [
                    entry.model_dump(mode="json")
                    for entry in suggestions.load()
                    if entry.status == "accepted"
                ],
                sort_keys=True,
            ).encode()
        )
        assert accepted == digest.hexdigest()[:12]


@pytest.mark.usefixtures("db_engine")
def test_coverage_report_lists_unreferenced_ingredients(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ingredients with a confirmed nutrition reference are not
    listed; unreferenced ones are grouped by site."""
    with get_session() as session:
        source = FoodSource(
            name="fdc",
            version="test",
            licence="public domain",
            citation="test",
        )
        session.add(source)
        session.flush()
        food = RefFood(source_id=source.id, source_food_id="171287", description="Egg")
        session.add(food)
        session.flush()
        egg = Ingredient(name="egg")
        water = Ingredient(name="water")
        session.add_all([egg, water])
        recipe = _add_recipe(session, "https://x/x", "X")
        session.add(
            IngredientFoodRef(
                ingredient_id=egg.id,
                ref_food_id=food.id,
                source_id=food.source_id,
                role="nutrition",
                status="confirmed",
                origin="seed",
            )
        )
        egg_term = IngredientTerm(
            term="egg", ingredient_id=egg.id, variant=None, resolution="seed"
        )
        water_term = IngredientTerm(
            term="water", ingredient_id=water.id, variant=None, resolution="seed"
        )
        session.add_all([egg_term, water_term])
        session.flush()
        foods = ((egg_term, 2.0), (egg_term, 1.0), (water_term, 250.0))
        for position, (term, quantity) in enumerate(foods):
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    position=position,
                    raw_text=term.term,
                    term_id=term.id,
                    quantity=quantity,
                    dimension="count" if term is egg_term else "volume",
                    base_unit="piece" if term is egg_term else "ml",
                )
            )

    with get_session() as session:
        coverage_report(session)
    out = capsys.readouterr().out
    assert "bbc_good_food:" in out
    assert "water" in out
    assert "egg" not in out
