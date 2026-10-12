"""Embedding eval tests: a deterministic fake embedder, never a model."""

import hashlib
import json
import math
import sys
from typing import TYPE_CHECKING

import numpy as np
import pytest
from sqlalchemy.exc import OperationalError

from menu.db.database import FoodSource, RefFood
from menu.ingest import embeddings_eval
from menu.ingest.embeddings import embed
from menu.ingest.embeddings_eval import ScoredPair, accuracy_at, best_threshold

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


class FakeEmbedder:
    """Deterministic hash vectors — the one from test_embeddings.py."""

    model_id: str

    def __init__(self, model_id: str = "fake/model", dim: int = 8) -> None:
        self.model_id = model_id
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        vectors = np.empty((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
            vectors[row] = np.random.default_rng(seed).standard_normal(self.dim)
        return vectors


@pytest.fixture
def fake_embed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """embeddings_eval embeds through the fake, cached under tmp_path:
    no model download, nothing written to the real .cache."""
    fake = FakeEmbedder()

    def _embed(texts: list[str], **_kwargs: object) -> np.ndarray:
        return embed(texts, embedder=fake, cache_dir=tmp_path)

    monkeypatch.setattr(embeddings_eval, "embed", _embed)


def test_accuracy_at_counts_label_agreement() -> None:
    scored: list[ScoredPair] = [
        ("a", "b", "same", 0.9),
        ("c", "d", "different", 0.1),
    ]
    assert accuracy_at(scored, 0.5) == 1.0
    assert accuracy_at(scored, 0.95) == 0.5  # the positive pair drops below


def test_best_threshold_lowest_threshold_wins_a_tie() -> None:
    # 0.1, 0.4 and 0.8 all separate the pairs at 2/3; the lowest — the
    # most inclusive operating point — wins.
    scored: list[ScoredPair] = [
        ("a", "b", "same", 0.8),
        ("c", "d", "different", 0.4),
        ("e", "f", "same", 0.1),
    ]
    assert best_threshold(scored) == (0.1, 2 / 3)


def test_best_threshold_inf_covers_the_all_negative_end() -> None:
    # every threshold at or below the top score misclassifies the top
    # pair; only +inf calls everything negative.
    scored: list[ScoredPair] = [
        ("a", "b", "different", 0.9),
        ("c", "d", "different", 0.1),
    ]
    assert best_threshold(scored) == (math.inf, 1.0)


@pytest.mark.usefixtures("fake_embed")
def test_pair_scores_follows_the_fixture_order() -> None:
    scored = embeddings_eval.pair_scores("fake/model")
    fixture: list[dict[str, str]] = json.loads(embeddings_eval.FIXTURE_PATH.read_text())
    assert [(a, b, label) for a, b, label, _ in scored] == [
        (pair["a"], pair["b"], pair["label"]) for pair in fixture
    ]
    # cosine similarity of normalised vectors stays in [-1, 1]
    assert all(-1.0 <= score <= 1.0 for *_, score in scored)


@pytest.mark.usefixtures("fake_embed")
def test_report_pairs_prints_the_sweep(capsys: pytest.CaptureFixture[str]) -> None:
    embeddings_eval.report_pairs("fake/model")
    out = capsys.readouterr().out
    assert out.startswith("Synonym pairs (fake/model")
    assert "best threshold" in out
    assert "icing sugar / confectioners' sugar" in out  # the first fixture pair


def _seed_fdc_egg(session: Session) -> None:
    source = FoodSource(name="fdc", version="v", licence="l", citation="c")
    session.add(source)
    session.flush()
    session.add(
        RefFood(
            source_id=source.id,
            source_food_id="171287",
            description="Egg, whole, raw, fresh",
            category="Dairy and Egg Products",
        )
    )
    session.commit()  # _ref_rows reads through its own session


def test_ref_rows_reads_the_test_database(session: Session) -> None:
    _seed_fdc_egg(session)
    rows = embeddings_eval._ref_rows()
    assert [tuple(row) for row in rows] == [
        ("fdc", "171287", "Egg, whole, raw, fresh", "Dairy and Egg Products")
    ]


def test_ref_rows_treats_a_missing_database_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ref tables to read (no database at all): empty, not a crash."""

    class NoDatabase:
        def __enter__(self) -> NoDatabase:
            raise OperationalError("SELECT 1", {}, Exception("no database"))

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(embeddings_eval, "get_ro_session", NoDatabase)
    assert embeddings_eval._ref_rows() == []


@pytest.mark.usefixtures("db_engine")
def test_report_recall_skips_when_the_ref_tables_are_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    embeddings_eval.report_recall("fake/model")
    assert "Recall half skipped" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_embed")
def test_report_recall_scores_the_seeded_foods(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """egg's own ref row is a hit, a wrong id is a miss, and a source
    with no imported foods misses wholesale; the two roles per food
    count once."""
    _seed_fdc_egg(session)
    monkeypatch.setattr(
        embeddings_eval,
        "FOOD_REFS",
        {
            "egg": [("fdc", "171287", "conversion"), ("fdc", "171287", "nutrition")],
            "milk": [("fdc", "999", "nutrition")],
            "butter": [("cnf", "35", "nutrition")],
        },
    )

    embeddings_eval.report_recall("fake/model")

    out = capsys.readouterr().out
    assert "Recall@5 on seeded FOOD_REFS (fake/model):" in out
    assert "  fdc: 1/2 = 0.500" in out
    assert "  cnf: 0/1 = 0.000" in out
    assert "  miss: milk -> fdc 999" in out


@pytest.mark.usefixtures("fake_embed", "db_engine")
def test_main_runs_both_halves(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["menu-embed-eval", "--model", "fake/model"])
    embeddings_eval.main()
    out = capsys.readouterr().out
    assert "Synonym pairs (fake/model" in out
    assert "Recall half skipped" in out  # the test database's ref tables are empty
