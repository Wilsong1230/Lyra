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

from lyra_core.actuators import SpeechActuator
from lyra_core.interface import CognitiveCore, Observation
from lyra_core.outcomes import OutcomeTracker
from lyra_core.perception import PerceptionLoop, Poller
from lyra_core.senses import poll_wakeword
from lyra_memory import MemorySystem

_DEFAULT_POLL_SECONDS: float = 2.0


class CoreSink:
    """Translates a list[Observation] into a single CognitiveCore.tick() call.

    Each poll cycle's surviving observations become one tick; dt is the
    perception loop's poll interval.
    """

    def __init__(
        self,
        core: CognitiveCore,
        dt: float = _DEFAULT_POLL_SECONDS,
        actuator: "SpeechActuator | None" = None,
        outcomes: OutcomeTracker | None = None,
    ) -> None:
        self._core = core
        self._dt = dt
        self._actuator = actuator
        self._outcomes = outcomes

    async def __call__(self, observations: list[Observation]) -> None:
        # Arrivals are noted BEFORE the actuator runs, so an utterance cannot
        # be satisfied by observations from the cycle it was spoken in.
        if self._outcomes is not None:
            self._outcomes.observe(observations)

        # tick() returns gate-APPROVED intents. Binding the result is what
        # makes the loop a loop; discarding it left the core with no output.
        intents, _affect = await self._core.tick(observations, self._dt)

        if self._actuator is not None:
            # execute() returns what ACTUALLY happened, not what was approved.
            # Only executed utterances start an outcome watch.
            spoken = await self._actuator.execute(intents)
            if self._outcomes is not None:
                for intent in spoken:
                    self._outcomes.record_spoken(intent)


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
        actuator: SpeechActuator | None = None,
        outcomes: OutcomeTracker | None = None,
    ) -> None:
        # Ambient audio stays OFF (deferred until two-pool retrieval keeps
        # low-salience input out of KNN competition). Wakeword is ON because
        # the outcome path needs SOME input channel to be truthful — see the
        # module docstring in lyra_core.outcomes.
        self._pollers: list[Poller] = (
            pollers if pollers is not None else [poll_wakeword]
        )
        self._poll_seconds = poll_seconds
        self._core = core if core is not None else CognitiveCore(memory=MemorySystem(db_path=db_path))
        self._actuator = actuator if actuator is not None else SpeechActuator()
        self._outcomes = outcomes if outcomes is not None else OutcomeTracker()
        self._perception_loop: PerceptionLoop | None = None

    @property
    def core(self) -> CognitiveCore:
        return self._core

    @property
    def actuator(self) -> SpeechActuator:
        return self._actuator

    @property
    def outcomes(self) -> OutcomeTracker:
        return self._outcomes

    async def start(self) -> None:
        await self._core.start()  # raises clearly if CORE_PROMPT unset or DB fails

        sink = CoreSink(
            self._core,
            dt=self._poll_seconds,
            actuator=self._actuator,
            outcomes=self._outcomes,
        )
        # The outcome tracker is a Poller like any sense — it just reports on
        # Lyra's own actions instead of the world. Appended even when pollers
        # are injected: it is part of the loop, not part of the sensorium.
        self._perception_loop = PerceptionLoop(
            sink=sink,
            pollers=[*self._pollers, self._outcomes.poll],
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
