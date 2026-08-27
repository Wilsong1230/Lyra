"""Shared test doubles for lyra_core.

WHY THIS EXISTS. Memory failures are loud: `_ingest_sensory` has no try/except,
so a CognitiveCore ticking against an unstarted MemorySystem now raises instead
of silently discarding every turn. That is the point — it is the exact condition
that produced 653 turns, 0 episodes, and no symptom anywhere.

Tests that are about affect, drives, gating or selection rather than about
memory therefore need an explicit stand-in rather than an accidental one.
NullMemory is that stand-in: it accepts writes and drops them ON PURPOSE, which
is a different statement from a store that drops them by accident.
"""
from __future__ import annotations

import pytest

from lyra_memory.embeddings import hash_embedder, set_embedder


@pytest.fixture(autouse=True)
def deterministic_embeddings():
    """Offline, deterministic vectors.

    The real embedder downloads all-MiniLM-L6-v2 on first use, which makes the
    suite depend on network access. MemorySystem.start() pre-warms it and no
    longer swallows the failure — an embedder that cannot load means every atom
    write will fail, which is not something to discover at the first turn.
    """
    set_embedder(hash_embedder)
    yield
    set_embedder(None)


class _EmptyWorkingMemory:
    def get_items(self):
        return []

    def add_turn(self, role, content):
        return None

    def add_observation(self, content, source=None):
        return None

    def add_reflection(self, content):
        return None


class _EmptyIdentityEngine:
    async def get_top_traits(self, limit=10):
        return []

    async def get_context_traits(self, limit=10):
        return []


class _EmptyStore:
    """Every retrieval block's source, empty. Keeps build_context on its real
    code path rather than short-circuiting it with a MagicMock."""

    async def for_query(self, query):
        return []

    async def for_injection(self, limit=4):
        return []

    async def recent(self, limit=10, **kw):
        return []

    async def write(self, **kw):
        return 0


class NullMemory:
    """Accepts ingest and records nothing. Deliberately inert, not broken.

    Satisfies the full build_context contract with empty stores, so assembly
    runs for real and returns nothing — which is a different assertion from
    assembly having been stubbed out.
    """

    def __init__(self, candidate_pool=None) -> None:
        self.candidate_pool = candidate_pool
        self.db = None
        self.working_memory = _EmptyWorkingMemory()
        self.identity_engine = _EmptyIdentityEngine()
        self.structured_state = None
        self.outcomes = None
        self.facts = _EmptyStore()
        self.commitments = _EmptyStore()
        self.atom_store = _EmptyStore()
        self.context_log = _EmptyStore()
        self.turns: list[tuple[str, str]] = []
        self.observations: list[tuple[str, str]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def add_turn(self, role: str, content: str, *, source: str = "cli") -> int:
        self.turns.append((role, content))
        return len(self.turns)

    async def add_observation(self, content: str, source: str | None = None, **kw) -> int:
        self.observations.append((source or "system", content))
        return len(self.observations)

    async def record_outcome(self, predicted: str, actual: str, **kw) -> int:
        return 0
