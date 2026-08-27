"""lyra_memory — turn as substrate, everything else derived.

    hot path        append atom + vec + fts. one transaction. <50ms.
    cold passes     segmentation, salience, dream, entities, facts, commitments
    retrieval       three paths, one merge, pinned order, byte budgets

FAILURE POLICY IS LOUD. There is no try/except around ingest or context
assembly. Fail-open makes a broken store and an empty store behaviourally
identical, and for an emergence thesis that is undetectable from transcripts —
verified empirically at 653 turns, 0 episodes, and no symptom anywhere. The
schema version is asserted at startup rather than repaired.

Cold passes are the exception and are allowed to fail: nothing they write is
load-bearing, and a crashed pass leaves the store correct.
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from lyra_memory import config
from lyra_memory.config import DB_PATH, DREAM_IDLE_SECONDS, DREAM_POLL_SECONDS
from lyra_memory.atoms import AtomStore, is_atom_worthy
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.commitments import CommitmentStore
from lyra_memory.context_log import ContextLog
from lyra_memory.db import init_db
from lyra_memory.dreaming_loop import DreamingLoop
from lyra_memory.entities import EntityStore
from lyra_memory.facts import FactStore
from lyra_memory.identity_engine import IdentityEngine
from lyra_memory.outcomes import OutcomeStore, SalienceScorer
from lyra_memory.sessions import Segmenter
from lyra_memory.structured_state import StructuredState
from lyra_memory.working_memory import WorkingMemory

__all__ = ["MemorySystem"]

# The CLI speaks in roles; the store speaks in speakers. One mapping, here.
_ROLE_TO_SPEAKER = {"user": "wilson", "wilson": "wilson", "lyra": "lyra", "assistant": "lyra", "system": "system"}


class MemorySystem:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DB_PATH
        self.db: aiosqlite.Connection | None = None
        self.structured_state: StructuredState | None = None
        self.working_memory: WorkingMemory | None = None
        self.atom_store: AtomStore | None = None
        self.facts: FactStore | None = None
        self.commitments: CommitmentStore | None = None
        self.entities: EntityStore | None = None
        self.outcomes: OutcomeStore | None = None
        self.sessions: Segmenter | None = None
        self.salience: SalienceScorer | None = None
        self.context_log: ContextLog | None = None
        self.candidate_pool: CandidatePool | None = None
        self.dreaming_loop: DreamingLoop | None = None
        self.identity_engine: IdentityEngine | None = None

    async def start(
        self,
        idle_seconds: int = DREAM_IDLE_SECONDS,
        poll_seconds: int = DREAM_POLL_SECONDS,
        *,
        run_dream_loop: bool = True,
    ) -> None:
        if not config.CORE_PROMPT:
            raise ValueError(
                "CORE_PROMPT is not set — define it in config.py before starting MemorySystem"
            )

        # init_db asserts the schema version and that every required table
        # exists. A mismatch raises here, at start, rather than surfacing as an
        # assistant that quietly remembers nothing.
        self.db = await init_db(self._db_path)

        self.structured_state = StructuredState(self.db)
        self.working_memory = WorkingMemory()
        self.atom_store = AtomStore(self.db)
        self.facts = FactStore(self.db)
        self.commitments = CommitmentStore(self.db)
        self.entities = EntityStore(self.db)
        self.outcomes = OutcomeStore(self.db)
        self.sessions = Segmenter(self.db)
        self.salience = SalienceScorer(self.db)
        self.context_log = ContextLog(self.db)
        self.candidate_pool = CandidatePool(self.db)
        self.identity_engine = IdentityEngine(self.db, self.candidate_pool)
        self.dreaming_loop = DreamingLoop(
            self.db, self.working_memory, self.candidate_pool, self.identity_engine,
            segmenter=self.sessions, commitments=self.commitments,
            entities=self.entities, salience=self.salience, db_path=self._db_path,
        )

        if run_dream_loop:
            self.dreaming_loop.start(idle_seconds=idle_seconds, poll_seconds=poll_seconds)

        # Pre-warm the embedding model so the first assembled prompt doesn't pay
        # the model-loading penalty inside a caller timeout.
        from lyra_memory.embeddings import embed
        await embed("warmup")

    async def stop(self) -> None:
        if self.dreaming_loop is not None:
            await self.dreaming_loop.stop()
        if self.db is not None:
            await self.db.close()
            self.db = None

    # ── hot path ─────────────────────────────────────────────────────────────

    def _require_started(self) -> None:
        if self.working_memory is None or self.atom_store is None:
            raise RuntimeError("MemorySystem has not been started — call await mem.start() first")

    async def add_turn(self, role: str, content: str, *, source: str = "cli") -> int:
        """One conversational turn. Appends; never enriches.

        Raises on failure. A dropped turn is silent data loss and the failure
        policy exists precisely to make it audible.
        """
        self._require_started()
        speaker = _ROLE_TO_SPEAKER.get(role)
        if speaker is None:
            raise ValueError(f"unknown role {role!r}; expected one of {sorted(_ROLE_TO_SPEAKER)}")
        # The deque speaks in roles, the store in speakers. Normalise both from
        # the one mapping so a caller saying "assistant" and one saying "lyra"
        # cannot land in different buckets.
        self.working_memory.add_turn("user" if speaker == "wilson" else "lyra", content)
        return await self.atom_store.append(speaker, source, content)

    async def add_observation(
        self,
        content: str,
        source: str | None = None,
        *,
        category_change: bool = False,
        run_id: int | None = None,
    ) -> int | None:
        """A perceived event.

        Ambient and wakeword are TELEMETRY: they reach working memory but not
        the substrate unless this is a category change. Vision is an event and
        always gets an atom — but the atom is her DESCRIPTION of the frame, not
        the frame, so the caller puts the frame in `runs` and passes run_id.
        """
        self._require_started()
        source = source or "system"
        self.working_memory.add_observation(content, source=source)
        if not is_atom_worthy(source, category_change=category_change):
            return None
        return await self.atom_store.append("system", source, content, run_id=run_id)

    async def add_reflection(self, content: str) -> int:
        self._require_started()
        self.working_memory.add_reflection(content)
        return await self.atom_store.append("lyra", "reflection", content)

    async def record_outcome(
        self,
        predicted: str,
        actual: str,
        *,
        intent_atom_id: int | None = None,
        valence: float | None = None,
        environment: str | None = None,
    ) -> int:
        """Persist a resolved attempt and keep its link to the atom that caused it."""
        self._require_started()
        return await self.outcomes.record(
            predicted, actual, intent_atom_id=intent_atom_id,
            valence=valence, environment=environment,
        )

    # ── cold passes, on demand ───────────────────────────────────────────────

    async def run_cold_passes(self) -> dict:
        """Segmentation -> entities -> salience. Each independently testable
        against a frozen `atoms` table; none of it is load-bearing."""
        session_ids = await self.sessions.run()
        linked = await self.entities.run()
        scored = await self.salience.run()
        return {"sessions": session_ids, "entity_links": linked, "atoms_scored": scored}
