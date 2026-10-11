"""Local text embeddings: texts -> L2-normalised float32 vectors.

Wraps fastembed's TextEmbedding behind the repo's own API (as
ingredients.py wraps the parser), so nothing else imports fastembed.

Model: BAAI/bge-small-en-v1.5 (384 dims, ONNX runtime, no torch).
Its weights are MIT-licensed — the model card states "The released
models can be used for commercial purposes free of charge" — so it
passes the reference-data bar: commercial use, no share-alike.

Weights download once into `.cache/models/` (fastembed's cache_dir);
vectors are cached per model in `.cache/embeddings/<model id>.npz`.
After that everything runs offline, and nothing calls the network at
ingest time. `embed` is the only entry point the rest of the code
uses; `nearest` is a numpy dot product, no vector store.
"""

from typing import TYPE_CHECKING, Protocol

import numpy as np
from fastembed import TextEmbedding

from menu.ingest.fetch import DEFAULT_CACHE_DIR

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
MODELS_DIR = DEFAULT_CACHE_DIR / "models"
"""Where model weights are downloaded once (fastembed's cache_dir)."""
EMBEDDINGS_DIR = DEFAULT_CACHE_DIR / "embeddings"
"""Where per-model vector caches live, next to the page cache."""


class Embedder(Protocol):
    """Anything that turns texts into vectors, one row per text."""

    model_id: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class FastEmbedder:
    """Embedder on fastembed's ONNX runtime.

    The model loads on the first `embed`, so importing this module
    and constructing an embedder stay cheap.
    """

    model_id: str

    def __init__(
        self, model_id: str = DEFAULT_MODEL, cache_dir: Path = MODELS_DIR
    ) -> None:
        self.model_id = model_id
        self._cache_dir = cache_dir
        self._model: TextEmbedding | None = None

    def _load(self) -> TextEmbedding:
        if self._model is None:
            # No lock — menu's ingest is single-process and
            # single-threaded; add one if that ever changes.
            self._model = TextEmbedding(
                model_name=self.model_id, cache_dir=str(self._cache_dir)
            )
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), np.float32)
        vectors = np.asarray(
            list(self._load().embed(texts)), dtype=np.float32
        ).reshape(len(texts), -1)
        return vectors


def _normalised(vectors: np.ndarray) -> np.ndarray:
    """L2-normalise rows; a zero row stays zero instead of NaN."""
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0.0, 1.0, norms)


def _cache_path(cache_dir: Path, model_id: str) -> Path:
    return cache_dir / f"{model_id.replace('/', '__')}.npz"


def embed(
    texts: list[str],
    *,
    embedder: Embedder | None = None,
    cache_dir: Path = EMBEDDINGS_DIR,
) -> np.ndarray:
    """L2-normalised float32 vectors for `texts`, in input order.

    The only entry point the rest of the code uses. Vectors are
    cached per model in `<cache_dir>/<model id>.npz` (arrays `texts`
    and `vectors`); only texts missing from the cache are embedded,
    and the file is rewritten atomically (tmp file + replace).
    Empty input returns an empty array without loading any model.
    """
    if not texts:
        return np.zeros((0, 0), np.float32)
    worker = FastEmbedder() if embedder is None else embedder
    path = _cache_path(cache_dir, worker.model_id)
    cached_texts: list[str] = []
    vectors_by_text: dict[str, np.ndarray] = {}
    if path.exists():
        with np.load(path) as data:
            cached_texts = [str(text) for text in data["texts"].tolist()]
            cached_vectors = data["vectors"]
            vectors_by_text = dict(
                zip(cached_texts, cached_vectors, strict=True)
            )

    missing = list(
        dict.fromkeys(text for text in texts if text not in vectors_by_text)
    )
    if missing:
        new_vectors = _normalised(worker.embed(missing))
        vectors_by_text.update(zip(missing, new_vectors, strict=True))
        cached_texts += missing
        path.parent.mkdir(parents=True, exist_ok=True)
        # np.savez appends .npz to a bare file name, so the tmp file
        # carries the suffix; replace() is atomic on the same volume.
        tmp = path.with_suffix(".tmp.npz")
        with tmp.open("wb") as file:
            np.savez(
                file,
                texts=np.array(cached_texts),
                vectors=np.stack(
                    [vectors_by_text[text] for text in cached_texts]
                ).astype(np.float32),
            )
        tmp.replace(path)

    return np.stack([vectors_by_text[text] for text in texts]).astype(
        np.float32
    )


def nearest(
    queries: np.ndarray, corpus: np.ndarray, *, k: int = 5, floor: float | None = None
) -> list[list[tuple[int, float]]]:
    """Cosine top-k per query: (corpus index, score), best first.

    Rows must be L2-normalised (embed's output is), so the dot
    product is the cosine similarity. Scores below `floor` are
    dropped; no vector store, one matrix product and an argsort.
    """
    scores = np.asarray(queries, dtype=np.float32) @ np.asarray(
        corpus, dtype=np.float32
    ).T
    results: list[list[tuple[int, float]]] = []
    for row in scores:
        # Stable sort on the negated scores: ties keep the lower
        # corpus index first.
        order = np.argsort(-row, kind="stable")[:k]
        hits = [(int(index), float(row[index])) for index in order]
        if floor is not None:
            hits = [hit for hit in hits if hit[1] >= floor]
        results.append(hits)
    return results
