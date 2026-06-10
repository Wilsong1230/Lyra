"""lyra_core.runtime — perception + cognitive core in one process.

CoreSink is the ONLY place the perception loop meets the core.  It hands
each poll cycle's surviving observations to CognitiveCore.tick(), which
threads obs.source through to memory (WorkingMemory's low-salience weight
applies to passive sense sources 'ears', 'wakeword') and advances drives
and affect.

Runtime lifecycle:
  start : core (owned MemorySystem + dreaming loop, affect restore) first,
          then perception loop
  stop  : perception loop first (no new arrivals), then core teardown
          (affect persisted, memory stopped)
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from lyra_core.interface import CognitiveCore, Observation
from lyra_core.perception import PerceptionLoop, Poller
from lyra_core.senses import poll_ambient, poll_wakeword
from lyra_memory import MemorySystem

_DEFAULT_POLL_SECONDS: float = 2.0


class CoreSink:
    """Translates a list[Observation] into a single CognitiveCore.tick() call.

    Each poll cycle's surviving observations become one tick; dt is the
    perception loop's poll interval.
    """

    def __init__(self, core: CognitiveCore, dt: float = _DEFAULT_POLL_SECONDS) -> None:
        self._core = core
        self._dt = dt

    async def __call__(self, observations: list[Observation]) -> None:
        await self._core.tick(observations, self._dt)


class Runtime:
    """Runs PerceptionLoop + CognitiveCore (and its owned DreamingLoop) in the
    same event loop.

    Shutdown order is intentional: stop perception (ingestion) before
    stopping the core (memory storage + affect persistence) so no
    observation can arrive mid-teardown.
    """

    def __init__(
        self,
        pollers: list[Poller] | None = None,
        poll_seconds: float = _DEFAULT_POLL_SECONDS,
        db_path: Path | None = None,
        core: CognitiveCore | None = None,
    ) -> None:
        self._pollers: list[Poller] = (
            pollers if pollers is not None else [poll_ambient, poll_wakeword]
        )
        self._poll_seconds = poll_seconds
        self._core = core if core is not None else CognitiveCore(memory=MemorySystem(db_path=db_path))
        self._perception_loop: PerceptionLoop | None = None

    @property
    def core(self) -> CognitiveCore:
        return self._core

    async def start(self) -> None:
        await self._core.start()  # raises clearly if CORE_PROMPT unset or DB fails

        sink = CoreSink(self._core, dt=self._poll_seconds)
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
        await self._core.stop()
        print(f"[{datetime.now().isoformat()}] [Runtime] stopped")

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Start, wait for stop event, then tear down in correct order."""
        if stop is None:
            stop = asyncio.Event()
        await self.start()
        await stop.wait()
        await self.stop()
