"""Test fixtures.

The real embedder downloads all-MiniLM-L6-v2 on first use. Tests install a
deterministic substitute instead: hashed bag-of-words projected into EMBED_DIM
and L2-normalised. It is not semantically good, but it IS deterministic and it
puts texts that share tokens closer together, which is the only property the
retrieval tests actually assert on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lyra_memory import MemorySystem
from lyra_memory.db import init_db
from lyra_memory.embeddings import hash_embedder, set_embedder


@pytest.fixture(autouse=True)
def deterministic_embeddings():
    set_embedder(hash_embedder)
    yield
    set_embedder(None)


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def conn(tmp_db_path: Path):
    c = await init_db(tmp_db_path)
    yield c
    await c.close()


@pytest.fixture
async def memory(tmp_db_path: Path):
    """A started MemorySystem with the dream loop OFF.

    Cold passes are driven explicitly in tests so each is exercised against a
    frozen `atoms` table, which is how the spec says they should be testable.
    """
    m = MemorySystem(db_path=tmp_db_path)
    await m.start(run_dream_loop=False)
    yield m
    await m.stop()
