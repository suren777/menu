"""Embedding tests: never download a model.

The fake embedder gives deterministic, deliberately un-normalised
hash vectors, so the tests prove `embed` normalises, caches per
model and keeps order without any model ever loading.
"""

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from menu.ingest import embeddings
from menu.ingest.embeddings import embed, nearest

if TYPE_CHECKING:
    import pytest

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "synonym_pairs.json"

# The plan's eight ground-truth pairs, (a, b, label).
PLAN_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("icing sugar", "confectioners' sugar", "same"),
    ("aubergine", "eggplant", "same"),
    ("rocket", "arugula", "same"),
    ("coriander leaf", "cilantro", "same"),
    ("whole milk", "milk", "variant"),
    ("coriander", "ground coriander", "different"),
    ("baking soda", "baking powder", "different"),
    ("caster sugar", "icing sugar", "different"),
)


class FakeEmbedder:
    """Deterministic hash vectors, NOT normalised: embed must do that."""

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


def test_embed_normalises_and_keeps_order(tmp_path: Path) -> None:
    vectors = embed(
        ["butter", "milk", "butter"],
        embedder=FakeEmbedder(),
        cache_dir=tmp_path,
    )
    assert vectors.dtype == np.float32
    assert vectors.shape == (3, 8)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert np.array_equal(vectors[0], vectors[2])  # duplicates agree
    assert not np.array_equal(vectors[0], vectors[1])  # order kept


def test_embed_uses_the_cache(tmp_path: Path) -> None:
    fake = FakeEmbedder()
    embed(["butter", "milk"], embedder=fake, cache_dir=tmp_path)
    assert fake.calls == [["butter", "milk"]]

    # Same texts again, in a different order: nothing new is embedded.
    embed(["milk", "butter"], embedder=fake, cache_dir=tmp_path)
    assert fake.calls == [["butter", "milk"]]

    # Only the missing text is embedded.
    embed(["butter", "eggs"], embedder=fake, cache_dir=tmp_path)
    assert fake.calls == [["butter", "milk"], ["eggs"]]


def test_cache_is_per_model(tmp_path: Path) -> None:
    first = FakeEmbedder("fake/model-a")
    second = FakeEmbedder("fake/model-b")
    embed(["butter"], embedder=first, cache_dir=tmp_path)
    embed(["butter"], embedder=second, cache_dir=tmp_path)
    assert first.calls == [["butter"]]
    assert second.calls == [["butter"]]  # model-a's cache not reused
    assert sorted(p.name for p in tmp_path.glob("*.npz")) == [
        "fake__model-a.npz",
        "fake__model-b.npz",
    ]


def test_nearest_topk_and_floor() -> None:
    # Unit vectors at 0, 60 and 90 degrees from the query axis; the
    # dot products below are exact in float32.
    corpus = np.array([[1.0, 0.0], [0.5, 3**0.5 / 2], [0.0, 1.0]], dtype=np.float32)
    query = np.array([[1.0, 0.0]], dtype=np.float32)

    hits = nearest(query, corpus, k=2)
    assert hits == [[(0, 1.0), (1, 0.5)]]

    assert nearest(query, corpus, k=5, floor=0.6) == [[(0, 1.0)]]

    both = nearest(np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32), corpus, k=3)
    assert [index for index, _ in both[0]] == [0, 1, 2]
    assert [index for index, _ in both[1]] == [2, 1, 0]


def test_fastembedder_loads_lazily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []

    class FakeTextEmbedding:
        def __init__(self, model_name: str, **_kwargs: object) -> None:
            created.append(model_name)

        def embed(self, texts: list[str]) -> Any:
            return iter([np.ones(8, dtype=np.float32) for _ in texts])

    monkeypatch.setattr(embeddings, "TextEmbedding", FakeTextEmbedding)
    embedder = embeddings.FastEmbedder(cache_dir=tmp_path / "models")
    assert created == []  # constructing loads nothing

    vectors = embed(["butter"], embedder=embedder, cache_dir=tmp_path)
    assert created == [embeddings.DEFAULT_MODEL]
    assert vectors.shape == (1, 8)


def test_synonym_fixture() -> None:
    pairs: list[dict[str, str]] = json.loads(FIXTURE_PATH.read_text())
    assert len(pairs) >= 20  # the plan's eight plus at least 12 more
    for pair in pairs:
        assert set(pair) == {"a", "b", "label"}
        assert pair["label"] in {"same", "variant", "different"}
    labelled = {(pair["a"], pair["b"], pair["label"]) for pair in pairs}
    for expected in PLAN_PAIRS:
        assert expected in labelled
