"""Test-time embedding backend — the same selection lyra-memory's tests make.

The daemon's turn path writes every turn as an atom, and an atom write
embeds. When `all-MiniLM-L6-v2` is not already cached (no network to
huggingface.co, or none at all), select the package's deterministic
offline stand-in so the mechanics stay verifiable. A machine with the model
cached runs the real thing and this file changes nothing.
"""
from __future__ import annotations

import os

from lyra_memory.config import EMBED_MODEL


def _real_model_is_cached() -> bool:
    previous = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(EMBED_MODEL)
        return True
    except Exception:
        return False
    finally:
        if previous is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous


if os.environ.get("LYRA_EMBED_BACKEND", "minilm").lower() != "hashed" and not _real_model_is_cached():
    os.environ["LYRA_EMBED_BACKEND"] = "hashed"
