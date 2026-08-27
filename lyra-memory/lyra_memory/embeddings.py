"""lyra_memory.embeddings — MiniLM, ~10ms, injectable.

The backend is swappable at runtime so the store can be exercised without
downloading a model. `embed` reads the backend at CALL time, so a substitution
takes effect even in modules that did `from lyra_memory.embeddings import embed`.
"""
from __future__ import annotations
import asyncio
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING

from lyra_memory.config import EMBED_MODEL, EMBED_DIM

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: "SentenceTransformer | None" = None
_embedder: Callable[[str], bytes] | None = None


def _get_model() -> "SentenceTransformer":
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _embed_sync(text: str) -> bytes:
    vec = _get_model().encode(text, normalize_embeddings=True)
    assert len(vec) == EMBED_DIM, f"model returned {len(vec)}-dim vector, expected {EMBED_DIM}"
    return struct.pack(f"{EMBED_DIM}f", *vec)


def set_embedder(fn: Callable[[str], bytes] | None) -> None:
    """Install (or clear, with None) an alternative synchronous embedder.

    Must return EMBED_DIM float32s, L2-normalised — retrieval converts sqlite-vec
    L2 distance to cosine similarity assuming unit vectors.
    """
    global _embedder
    _embedder = fn


async def embed(text: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embedder or _embed_sync, text)


def hash_embedder(text: str) -> bytes:
    """Deterministic offline substitute for MiniLM. FOR TESTS AND CI ONLY.

    Hashed bag-of-words projected into EMBED_DIM and L2-normalised. It is not
    semantically good — it has no notion of paraphrase — but it is deterministic,
    needs no download, and puts texts sharing tokens closer together, which is
    the only property the store's own tests depend on. Install it with
    set_embedder(hash_embedder).

    Never install this in a real run: the vectors it produces are not comparable
    with MiniLM's, so a store written under one and read under the other
    retrieves nothing.
    """
    import hashlib
    import math
    import re

    vec = [0.0] * EMBED_DIM
    for tok in re.findall(r"\w+", text.lower()) or ["\x00"]:
        h = hashlib.sha256(tok.encode()).digest()
        vec[int.from_bytes(h[:4], "big") % EMBED_DIM] += 1.0 if h[4] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return struct.pack(f"{EMBED_DIM}f", *[v / norm for v in vec])
