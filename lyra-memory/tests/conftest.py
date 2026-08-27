"""Test-time embedding backend.

The real embedder is `all-MiniLM-L6-v2`, downloaded from huggingface.co on
first use. Where that host is unreachable — an offline machine, or an egress
policy that denies it — every test touching `embed()` fails on a network
error rather than on anything about the code under test.

So when the real model is not already cached, this selects the package's
deterministic offline stand-in (`LYRA_EMBED_BACKEND=hashed`) before anything
imports the embedder. The *mechanics* — vec insert and KNN wiring, similarity
floors, dedupe, budgets, transaction boundaries, FTS — stay verifiable.

It is a stand-in, not a substitute. It is random indexing over word and
character n-grams: texts sharing surface forms come out close, texts that do
not come out far apart. It has no idea that "analytical thinking and numbers"
is about mathematics. Tests whose claim is genuinely about *meaning* must
request the `real_embeddings` fixture, which skips them when the real model is
unavailable — a fake must never turn a semantic assertion green.

A machine with the model cached runs the real thing and this file changes
nothing.
"""
from __future__ import annotations

import os

import pytest

from lyra_memory.config import EMBED_MODEL


def _real_model_is_cached() -> bool:
    """True only if the model can be loaded with no network call.

    HF_HUB_OFFLINE stops this from hanging on retries against a blocked host.
    """
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


# Must be decided before lyra_memory.embeddings is imported anywhere.
REAL_EMBEDDINGS_AVAILABLE = (
    os.environ.get("LYRA_EMBED_BACKEND", "minilm").lower() != "hashed"
    and _real_model_is_cached()
)
if not REAL_EMBEDDINGS_AVAILABLE:
    os.environ["LYRA_EMBED_BACKEND"] = "hashed"


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
