from __future__ import annotations
import asyncio
import hashlib
import os
import re
import struct
import sys
from typing import TYPE_CHECKING

from lyra_memory.config import EMBED_MODEL, EMBED_DIM

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

# Which embedder this process uses. `minilm` is the real one and the default.
#
# `hashed` is a deterministic offline stand-in for machines that cannot reach
# huggingface.co. It is NOT a substitute: it is random indexing over word and
# character n-grams, so it knows that "questions" and "question" are related
# and has no idea that "analytical thinking" is about mathematics.
#
# Two guards stop it being used by accident. It announces itself on stderr
# every time it loads, and the store stamps the embedder id into schema_meta
# and refuses to open under a different one — a vec table holding vectors from
# two different embedding spaces is silently, unfixably wrong.
_HASHED_ID = "hashed-random-indexing-v1"
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _backend() -> str:
    """Read at call time, not import time.

    Importing `lyra_memory.config` pulls in the package `__init__`, which
    imports this module — so anything selecting a backend by environment
    variable would be too late if this were captured at import.
    """
    return os.environ.get("LYRA_EMBED_BACKEND", "minilm").strip().lower()


def backend_id() -> str:
    """Identifies the embedding space these vectors live in."""
    return _HASHED_ID if _backend() == "hashed" else EMBED_MODEL


class _RandomIndexingEmbedder:
    """Deterministic offline stand-in with a SentenceTransformer's interface."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self._dim = dim
        self._cache: dict[str, list[float]] = {}

    def _feature_vector(self, feature: str) -> list[float]:
        cached = self._cache.get(feature)
        if cached is None:
            import numpy as np

            seed = int.from_bytes(hashlib.sha256(feature.encode()).digest()[:8], "big")
            cached = np.random.default_rng(seed).standard_normal(self._dim).tolist()
            self._cache[feature] = cached
        return cached

    def _features(self, text: str) -> list[str]:
        tokens = _TOKEN_RE.findall(text.lower())
        features = [f"w:{t}" for t in tokens]
        for token in tokens:
            padded = f"^{token}$"
            features += [f"c:{padded[i:i + 4]}" for i in range(max(len(padded) - 3, 1))]
        return features or ["w:"]

    def encode(self, text: str, normalize_embeddings: bool = True, **_: object):
        import numpy as np

        vec = np.zeros(self._dim, dtype=np.float64)
        for feature in self._features(text):
            vec += np.asarray(self._feature_vector(feature))
        if normalize_embeddings:
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec = vec / norm
        return vec.astype(np.float32)


_model: "SentenceTransformer | _RandomIndexingEmbedder | None" = None


def _get_model():
    global _model
    if _model is None:
        if _backend() == "hashed":
            print(
                f"[embeddings] LYRA_EMBED_BACKEND=hashed — using the offline "
                f"stand-in ({_HASHED_ID}), NOT {EMBED_MODEL}. Semantic "
                f"retrieval is surface-level only.",
                file=sys.stderr,
            )
            _model = _RandomIndexingEmbedder()
        else:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _embed_sync(text: str) -> bytes:
    vec = _get_model().encode(text, normalize_embeddings=True)
    assert len(vec) == EMBED_DIM, f"model returned {len(vec)}-dim vector, expected {EMBED_DIM}"
    return struct.pack(f"{EMBED_DIM}f", *vec)


async def embed(text: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embed_sync, text)
