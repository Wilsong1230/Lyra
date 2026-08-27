"""Test-time embedding backend.

The real embedder is `all-MiniLM-L6-v2`, downloaded from huggingface.co on
first use. Where that host is unreachable (an offline machine, or an egress
policy that denies it), every test that touches `embed()` fails on a network
error rather than on anything about the code under test.

This installs a deterministic offline stand-in so the *mechanics* — vec
insert/KNN wiring, similarity floors, dedupe, budgets, transaction
boundaries, FTS — stay verifiable without the network.

It is a stand-in, not a substitute. It is random indexing over word and
character n-grams: texts that share surface forms come out close, texts that
do not come out far apart. It has no idea that "analytical thinking and
numbers" is about mathematics. Tests whose claim is genuinely about
*meaning* must request the `real_embeddings` fixture, which skips them when
the real model is unavailable — a fake must never be allowed to turn a
semantic assertion green.

Nothing here touches the production path: `lyra_memory.embeddings` still
loads MiniLM at runtime.
"""
from __future__ import annotations

import hashlib
import os
import re

import numpy as np
import pytest

import lyra_memory.embeddings as _embeddings
from lyra_memory.config import EMBED_DIM, EMBED_MODEL

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class _RandomIndexingEmbedder:
    """Deterministic offline stand-in for a SentenceTransformer.

    Each token and character 4-gram is hashed to a fixed pseudo-random unit
    vector; a text is the normalized sum of its features. Same text in, same
    bytes out, on any machine, with no model download.
    """

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self._dim = dim
        self._cache: dict[str, np.ndarray] = {}

    def _feature_vector(self, feature: str) -> np.ndarray:
        cached = self._cache.get(feature)
        if cached is None:
            seed = int.from_bytes(hashlib.sha256(feature.encode()).digest()[:8], "big")
            cached = np.random.default_rng(seed).standard_normal(self._dim).astype(np.float32)
            self._cache[feature] = cached
        return cached

    def _features(self, text: str) -> list[str]:
        lowered = text.lower()
        tokens = _TOKEN_RE.findall(lowered)
        features = [f"w:{t}" for t in tokens]
        # Character n-grams give partial credit for morphology
        # ("question"/"questions"), which whole-word hashing alone would miss.
        for token in tokens:
            padded = f"^{token}$"
            features += [f"c:{padded[i:i + 4]}" for i in range(max(len(padded) - 3, 1))]
        return features or ["w:"]

    def encode(self, text: str, normalize_embeddings: bool = True, **_: object) -> np.ndarray:
        vec = np.zeros(self._dim, dtype=np.float32)
        for feature in self._features(text):
            vec += self._feature_vector(feature)
        if normalize_embeddings:
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec = vec / norm
        return vec.astype(np.float32)


def _real_model_or_none():
    """Load the real model only if it is already cached locally.

    HF_HUB_OFFLINE stops this from making a network call that would hang on
    retries when the host is blocked; a machine with a warm cache still gets
    the real embedder and runs the full suite.
    """
    previous = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(EMBED_MODEL)
    except Exception:
        return None
    finally:
        if previous is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous


_REAL_MODEL = _real_model_or_none()
REAL_EMBEDDINGS_AVAILABLE = _REAL_MODEL is not None

# Install whichever backend we have before any test imports run. The real one
# is preferred wherever it is present, so this file is a no-op on a machine
# with the model cached.
_embeddings._model = _REAL_MODEL if REAL_EMBEDDINGS_AVAILABLE else _RandomIndexingEmbedder()


def pytest_report_header(config) -> str:
    if REAL_EMBEDDINGS_AVAILABLE:
        return f"embeddings: real ({EMBED_MODEL})"
    return (
        f"embeddings: OFFLINE STAND-IN — {EMBED_MODEL} is not cached and "
        "huggingface.co is unreachable. Mechanics are verified; tests whose "
        "claim is about meaning are skipped, not faked."
    )


@pytest.fixture
def real_embeddings():
    """Skip a test whose assertion genuinely depends on MiniLM's semantics."""
    if not REAL_EMBEDDINGS_AVAILABLE:
        pytest.skip(
            f"needs real {EMBED_MODEL} semantics; model not cached and "
            "huggingface.co is blocked by egress policy"
        )
