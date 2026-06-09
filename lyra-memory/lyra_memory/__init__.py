from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

from lyra_memory import config
from lyra_memory.config import DB_PATH, DREAM_IDLE_SECONDS, DREAM_POLL_SECONDS
from lyra_memory.db import init_db
from lyra_memory.structured_state import StructuredState
from lyra_memory.working_memory import WorkingMemory
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.dreaming_loop import DreamingLoop
from lyra_memory.identity_engine import IdentityEngine


class MemorySystem:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DB_PATH
        self.db: aiosqlite.Connection | None = None
        self.structured_state: StructuredState | None = None
        self.working_memory: WorkingMemory | None = None
        self.candidate_pool: CandidatePool | None = None
        self.dreaming_loop: DreamingLoop | None = None
        self.identity_engine: IdentityEngine | None = None

    async def start(
        self,
        idle_seconds: int = DREAM_IDLE_SECONDS,
        poll_seconds: int = DREAM_POLL_SECONDS,
    ) -> None:
        if not config.CORE_PROMPT:
            raise ValueError(
                "CORE_PROMPT is not set — define it in config.py before starting MemorySystem"
            )

        try:
            self.db = await init_db(self._db_path)

            self.structured_state = StructuredState(self.db)
            self.working_memory = WorkingMemory()
            self.candidate_pool = CandidatePool(self.db)
            self.identity_engine = IdentityEngine(self.db, self.candidate_pool)
            self.dreaming_loop = DreamingLoop(self.db, self.working_memory, self.candidate_pool, self.identity_engine)

            self.dreaming_loop.start(idle_seconds=idle_seconds, poll_seconds=poll_seconds)

            # Pre-warm the embedding model so the first build_system_prompt() call
            # doesn't pay the model-loading penalty inside a tight caller timeout.
            from lyra_memory.embeddings import embed
            try:
                await embed("warmup")
            except Exception:
                pass
        except Exception:
            if self.db is not None:
                await self.db.close()
                self.db = None
            raise

    async def stop(self) -> None:
        if self.dreaming_loop is not None:
            await self.dreaming_loop.stop()
        if self.db is not None:
            await self.db.close()

    async def add_turn(self, role: str, content: str) -> None:
        if self.working_memory is None:
            raise RuntimeError("MemorySystem has not been started — call await mem.start() first")
        self.working_memory.add_turn(role, content)

    async def add_observation(self, content: str) -> None:
        if self.working_memory is None:
            raise RuntimeError("MemorySystem has not been started — call await mem.start() first")
        self.working_memory.add_observation(content)

    async def add_reflection(self, content: str) -> None:
        if self.working_memory is None:
            raise RuntimeError("MemorySystem has not been started — call await mem.start() first")
        self.working_memory.add_reflection(content)
