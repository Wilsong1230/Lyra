"""lyra_core.runtime — perception + dreaming loops in one process.

MemorySink is the ONLY place the perception loop meets memory.  It threads
obs.source through so WorkingMemory's low-salience weight applies to
passive sense sources ('ears', 'wakeword').

Runtime lifecycle:
  start : memory (DB + dreaming loop) first, then perception loop
  stop  : perception loop first (no new arrivals), then memory teardown

CognitiveCore.tick() is intentionally NOT wired here.  The injectable Sink
is what keeps Phase 2 distinct from Phase 3: Sink → MemorySystem only.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from lyra_core.interface import Observation
from lyra_core.perception import PerceptionLoop, Poller
from lyra_core.senses import poll_ambient, poll_wakeword
from lyra_memory import MemorySystem

_DEFAULT_POLL_SECONDS: float = 2.0


class MemorySink:
    """Translates a list[Observation] into MemorySystem.add_observation calls.

    Passes obs.source so WorkingMemory applies _SENSE_IMPORTANCE for
    sources 'ears' and 'wakeword' (Step 1.2 low-salience seam).
    """

    def __init__(self, memory: MemorySystem) -> None:
        self._memory = memory

    async def __call__(self, observations: list[Observation]) -> None:
        for obs in observations:
            await self._memory.add_observation(obs.content, source=obs.source)


class Runtime:
    """Runs PerceptionLoop + DreamingLoop in the same event loop.

    Shutdown order is intentional: stop perception (ingestion) before
    stopping memory (storage) so no observation can arrive mid-teardown.
    """

    def __init__(
        self,
        pollers: list[Poller] | None = None,
        poll_seconds: float = _DEFAULT_POLL_SECONDS,
        db_path: Path | None = None,
    ) -> None:
        self._pollers: list[Poller] = (
            pollers if pollers is not None else [poll_ambient, poll_wakeword]
        )
        self._poll_seconds = poll_seconds
        self._db_path = db_path
        self._memory: MemorySystem | None = None
        self._perception_loop: PerceptionLoop | None = None

    async def start(self) -> None:
        self._memory = MemorySystem(db_path=self._db_path)
        await self._memory.start()  # raises clearly if CORE_PROMPT unset or DB fails

        sink = MemorySink(self._memory)
        self._perception_loop = PerceptionLoop(
            sink=sink,
            pollers=self._pollers,
            poll_seconds=self._poll_seconds,
        )
        self._perception_loop.start()
        print(f"[{datetime.now().isoformat()}] [Runtime] started")

    async def stop(self) -> None:
        if self._perception_loop is not None:
            await self._perception_loop.stop()
        if self._memory is not None:
            await self._memory.stop()
        print(f"[{datetime.now().isoformat()}] [Runtime] stopped")

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Start, wait for stop event, then tear down in correct order."""
        if stop is None:
            stop = asyncio.Event()
        await self.start()
        await stop.wait()
        await self.stop()
