"""Resolve tests: re-resolve re-pointing terms and regramming lines,
and orphan pruning."""

import json
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from menu.db.connection import get_session
from menu.db.database import (
    FoodSource,
    Ingredient,
    IngredientConversion,
    IngredientFoodRef,
    IngredientTerm,
    RecipeIngredient,
    RefFood,
)
from menu.db.ingredients import lines as lines_module
from menu.db.ingredients import repository, resolve
from menu.db.ingredients import seed as seed_module
from menu.db.ingredients import suggestions as suggestions_module
from menu.db.ingredients.seed import seed_ingredient_data

from .helpers import add_recipe, add_term, store_lines

if TYPE_CHECKING:
    from pathlib import Path

    from menu.ingest.ingredients import ParsedLine


def _write_suggestions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any]
) -> None:
    """Point the suggestions ledger at a temp file holding one entry."""
    path = tmp_path / "suggestions.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")
    monkeypatch.setattr(suggestions_module, "SUGGESTIONS_PATH", path)


def _alias(term: str, target: str, variant: str | None = None) -> dict[str, Any]:
    """An accepted alias suggestion."""
    return {
        "input": "queue",
        "kind": "alias",
        "subject": term,
        "proposal": {"term": term, "target": target, "variant": variant},
        "evidence": {"reason": "test"},
        "model": {"llm": "fake/model"},
        "status": "accepted",
    }


def _effective_names() -> list[str | None]:
    """Each primary line's effective ingredient name, in id order."""
    with get_session() as session:
        names: list[str | None] = []
        for line, term in session.execute(
            repository.lines_with_terms_select(
                RecipeIngredient.alternative_of.is_(None)
            ).order_by(RecipeIngredient.id)
        ):
            effective = repository.effective_ingredient_id(line.needs_review, term)
            ingredient = (
                session.get(Ingredient, effective) if effective is not None else None
            )
            names.append(ingredient.name if ingredient is not None else None)
        return names


@pytest.mark.usefixtures("db_engine")
def test_removing_a_density_seed_regrams_the_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a DENSITIES_G_PER_ML entry: re-seeding deletes its
    row and re-resolve regrams the line — to NULL, milk having no
    reference to fall back on here."""
    with get_session() as session:
        seed_ingredient_data(session)
    recipe_id = add_recipe("https://x/1")
    store_lines(recipe_id, {"recipeIngredient": ["240ml milk"]})
    with get_session() as session:
        line = session.scalars(select(RecipeIngredient)).one()
        assert line.grams == pytest.approx(240 * 1.03)
        assert line.conversion_id is not None

    monkeypatch.setattr(
        seed_module,
        "DENSITIES_G_PER_ML",
        {
            name: value
            for name, value in seed_module.DENSITIES_G_PER_ML.items()
            if name != "milk"
        },
    )
    with get_session() as session:
        resolve.re_resolve(session)

    with get_session() as session:
        milk_id = session.scalar(select(Ingredient.id).where(Ingredient.name == "milk"))
        assert milk_id is not None
        rows = session.scalars(
            select(IngredientConversion).where(
                IngredientConversion.ingredient_id == milk_id
            )
        ).all()
        assert rows == []
        line = session.scalars(select(RecipeIngredient)).one()
        assert line.grams is None
        assert line.grams_source is None
        assert line.conversion_id is None


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_repoints_terms_without_reparse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The everyday command after a seed change: a stored line's auto
    term is re-pointed at the canonical ingredient — with the parser
    unusable — and the line's grams are recomputed from the stored
    columns. Every line sharing the term moves together."""
    basbousa_id = add_recipe("https://x/basbousa")
    shokupan_id = add_recipe("https://x/shokupan")
    store_lines(basbousa_id, {"recipeIngredient": ["240ml warm milk"]})
    store_lines(shokupan_id, {"recipeIngredient": ["500ml warm milk"]})

    with get_session() as session:
        lines = session.scalars(select(RecipeIngredient)).all()
        assert len(lines) == 2
        terms = {session.get(IngredientTerm, line.term_id) for line in lines}
        assert len(terms) == 1  # both lines share one term
        (term,) = terms
        assert term is not None
        assert term.resolution == "auto"
        old_effective = term.ingredient_id
        assert old_effective is not None

    # Seed the variant and make the parser unusable: re-resolve must
    # work from the stored columns alone.
    monkeypatch.setattr(
        seed_module,
        "ALIASES",
        [*seed_module.ALIASES, ("warm milk", "milk", "warm")],
    )

    def boom(*_args: object, **_kwargs: object) -> ParsedLine:
        raise AssertionError("re-resolve must not parse")

    monkeypatch.setattr(lines_module, "parse_line", boom)

    with get_session() as session:
        regrammed, pruned = resolve.re_resolve(session)[1:]

    assert regrammed == 2  # both lines' effective ingredient changed
    assert pruned == 1  # the orphaned auto concept "warm milk"
    with get_session() as session:
        milk = session.scalars(
            select(Ingredient).where(Ingredient.name == "milk")
        ).one()
        for line in session.scalars(select(RecipeIngredient)).all():
            term = session.get(IngredientTerm, line.term_id)
            assert term is not None
            assert (term.term, term.variant, term.resolution) == (
                "warm milk",
                "warm",
                "seed",
            )
            effective = repository.effective_ingredient_id(line.needs_review, term)
            assert effective == milk.id
            assert effective != old_effective
            # Grams recomputed from the stored columns through milk's
            # seeded density (1.03 g/ml) — no parser involved.
            expected = 240.0 if line.recipe_id == basbousa_id else 500.0
            assert line.grams == pytest.approx(expected * 1.03)
            assert line.grams_source == "seed"


@pytest.mark.usefixtures("db_engine")
def test_accepted_alias_repoints_lines_on_re_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted alias suggestion acts exactly like a hand ALIASES
    entry: re-resolve re-points the stored auto term's lines — the
    parser is never called."""
    store_lines(add_recipe("https://x/1"), {"recipeIngredient": ["240ml oat milk"]})
    _write_suggestions(
        tmp_path,
        monkeypatch,
        _alias("oat milk", "milk", "oat"),
    )

    def boom(*_args: object, **_kwargs: object) -> ParsedLine:
        raise AssertionError("re-resolve must not parse")

    monkeypatch.setattr(lines_module, "parse_line", boom)

    with get_session() as session:
        regrammed, pruned = resolve.re_resolve(session)[1:]

    assert regrammed == 1
    assert pruned == 1  # the orphaned auto concept "oat milk"
    assert _effective_names() == ["milk"]


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_keeps_an_accepted_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted alias is a curated decision: a second re-resolve
    does not revert the term to the heuristics as stale."""
    store_lines(add_recipe("https://x/1"), {"recipeIngredient": ["240ml oat milk"]})
    _write_suggestions(tmp_path, monkeypatch, _alias("oat milk", "milk", "oat"))

    with get_session() as session:
        resolve.re_resolve(session)
    with get_session() as session:
        resolve.re_resolve(session)

    assert _effective_names() == ["milk"]
    with get_session() as session:
        term = session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "oat milk")
        ).one()
        assert term.resolution == "seed"


@pytest.mark.usefixtures("db_engine")
def test_prune_orphan_ingredients() -> None:
    """An unused auto term goes, and an auto concept nothing points at
    and no curated reference keeps goes with it; a concept a line
    resolves through stays."""
    recipe_id = add_recipe("https://x/1")

    with get_session() as session:
        used = Ingredient(name="milk")
        orphan = Ingredient(name="warm milk")
        session.add_all([used, orphan])
        session.flush()
        used_term = add_term(session, "milk", used, resolution="auto")
        add_term(session, "warm milk", orphan, resolution="auto")
        session.add(
            RecipeIngredient(
                recipe_id=recipe_id,
                position=0,
                raw_text="250ml milk",
                term_id=used_term.id,
                quantity=250.0,
                dimension="volume",
                base_unit="ml",
            )
        )
        orphan_id = orphan.id

    with get_session() as session:
        assert resolve.prune_orphan_ingredients(session) == 1

    with get_session() as session:
        assert session.get(Ingredient, orphan_id) is None
        assert (
            session.scalars(
                select(IngredientTerm).where(IngredientTerm.term == "warm milk")
            ).first()
            is None
        )
        assert len(session.scalars(select(Ingredient)).all()) == 1


@pytest.mark.usefixtures("db_engine")
def test_prune_drops_parser_candidates_but_keeps_curated_refs() -> None:
    """With foreign keys enforced, an orphan's parser candidate goes
    with it; an ingredient with a curated reference is a decision in
    seed.py and is never pruned."""
    with get_session() as session:
        source = FoodSource(name="fdc", version="1", licence="CC0", citation="x")
        session.add(source)
        session.flush()
        food = RefFood(source_id=source.id, source_food_id="1", description="Milk")
        orphan = Ingredient(name="warm milk", origin="auto")
        curated = Ingredient(name="milk", origin="seed")
        session.add_all([food, orphan, curated])
        session.flush()
        session.add_all(
            [
                IngredientFoodRef(
                    ingredient_id=orphan.id,
                    ref_food_id=food.id,
                    source_id=source.id,
                    status="candidate",
                    origin="parser",
                ),
                IngredientFoodRef(
                    ingredient_id=curated.id,
                    ref_food_id=food.id,
                    source_id=source.id,
                    role="nutrition",
                    status="confirmed",
                    origin="seed",
                ),
            ]
        )
        orphan_id, curated_id = orphan.id, curated.id

    with get_session() as session:
        assert resolve.prune_orphan_ingredients(session) == 1

    with get_session() as session:
        assert session.get(Ingredient, orphan_id) is None
        assert session.get(Ingredient, curated_id) is not None


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_regrams_when_only_a_density_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seed change can move grams without moving the ingredient."""
    store_lines(add_recipe("https://x/1"), {"recipeIngredient": ["240ml oat milk"]})
    with get_session() as session:
        assert session.scalars(select(RecipeIngredient.grams)).one() is None

    monkeypatch.setattr(
        seed_module,
        "DENSITIES_G_PER_ML",
        {**seed_module.DENSITIES_G_PER_ML, "oat milk": 1.03},
    )
    with get_session() as session:
        resolve.re_resolve(session)

    assert _effective_names() == ["oat milk"]
    with get_session() as session:
        grams = session.scalars(select(RecipeIngredient.grams)).one()
    assert grams == pytest.approx(240 * 1.03)


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_lets_a_later_seed_rescue_a_flagged_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_lines(
        add_recipe("https://x/cake"),
        {"recipeIngredient": ["pink and yellow food colouring gels"]},
    )
    assert _effective_names() == [None]

    monkeypatch.setattr(
        seed_module,
        "ALIASES",
        [
            *seed_module.ALIASES,
            ("pink food colouring gel", "food colouring gel", None),
        ],
    )
    with get_session() as session:
        resolve.re_resolve(session)

    assert _effective_names() == ["food colouring gel"]


@pytest.mark.usefixtures("db_engine")
def test_alias_beats_a_same_named_seed_concept() -> None:
    """ "warm milk" is an ALIASES entry for milk; seeding a density for
    a "warm milk" concept must not steal the term."""
    with get_session() as session:
        seed_ingredient_data(session)
        concept = Ingredient(name="warm milk", origin="seed")
        session.add(concept)
        session.flush()
        seed_module._seed_term("warm milk", concept.id, None, session, repoint=False)
        term = session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "warm milk")
        ).one()
        milk = session.scalars(
            select(Ingredient).where(Ingredient.name == "milk")
        ).one()
        assert term.ingredient_id == milk.id


@pytest.mark.usefixtures("db_engine")
def test_re_resolve_follows_alias_edits_and_removals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-pointing a seeded alias moves its lines; deleting it from
    seed.py hands the term back to the heuristics."""
    original = list(seed_module.ALIASES)
    store_lines(add_recipe("https://x/1"), {"recipeIngredient": ["240ml oat milk"]})

    monkeypatch.setattr(
        seed_module, "ALIASES", [*original, ("oat milk", "milk", "oat")]
    )
    with get_session() as session:
        resolve.re_resolve(session)
    assert _effective_names() == ["milk"]

    monkeypatch.setattr(
        seed_module, "ALIASES", [*original, ("oat milk", "cream", None)]
    )
    with get_session() as session:
        resolve.re_resolve(session)
    assert _effective_names() == ["cream"]

    monkeypatch.setattr(seed_module, "ALIASES", original)
    with get_session() as session:
        resolve.re_resolve(session)
    assert _effective_names() == ["oat milk"]
    with get_session() as session:
        term = session.scalars(
            select(IngredientTerm).where(IngredientTerm.term == "oat milk")
        ).one()
        assert term.resolution == "auto"
