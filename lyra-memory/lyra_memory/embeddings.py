from __future__ import annotations
import asyncio
import struct
from typing import TYPE_CHECKING

from lyra_memory.config import EMBED_MODEL, EMBED_DIM

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: "SentenceTransformer | None" = None


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


async def embed(text: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embed_sync, text)
