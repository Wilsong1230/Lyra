"""lyra_core.perception — sink-agnostic perception loop.

Polls injected async callables (Pollers), deduplicates results, and pushes
surviving Observations to an injected async callable (Sink). Knows nothing
about MemorySystem or CognitiveCore — those are injected by the runtime.

Dedup key = (source, content).  ts is intentionally excluded: the same
real-world detection arrives on consecutive polls with a later ts each time,
and we want those repeats dropped, not passed through.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime

from lyra_core.interface import Observation

# ── Public types ──────────────────────────────────────────────────────────────

Sink = Callable[[list[Observation]], Awaitable[None]]
Poller = Callable[[], Awaitable[list[Observation]]]

_DedupKey = tuple[str, str]  # (source, content)

# ── Loop ──────────────────────────────────────────────────────────────────────

class PerceptionLoop:
    """Runs on its own clock; polls injected Pollers each cycle; deduplicates;
    delivers surviving Observations to the Sink.

    Mirrors DreamingLoop's lifecycle discipline:
    - start() is idempotent (no-op if the task is already running)
    - stop() cancels and awaits the task, swallowing CancelledError
    - One failing Poller is logged-and-skipped; the others still run
    - One bad cycle is logged-and-continued; the loop never dies from a
      non-cancellation exception
    """

    def __init__(
        self,
        sink: Sink,
        pollers: list[Poller],
        poll_seconds: float = 2.0,
        dedup_window: int = 128,
    ) -> None:
        self._sink = sink
        self._pollers = pollers
        self._poll_seconds = poll_seconds
        self._seen: deque[_DedupKey] = deque(maxlen=dedup_window)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        print(f"[{datetime.now().isoformat()}] [PerceptionLoop] started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        print(f"[{datetime.now().isoformat()}] [PerceptionLoop] stopped")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] [PerceptionLoop] cycle error: {e}")

    async def _poll_once(self) -> None:
        gathered: list[Observation] = []
        for poller in self._pollers:
            try:
                obs = await poller()
                gathered.extend(obs)
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] [PerceptionLoop] poller error: {e}")

        # dedup: snapshot current window, then filter gathered observations
        current_seen: set[_DedupKey] = set(self._seen)
        survivors: list[Observation] = []
        for obs in gathered:
            key: _DedupKey = (obs.source, obs.content)
            if key not in current_seen:
                survivors.append(obs)
                current_seen.add(key)
                self._seen.append(key)  # bounded deque may evict the oldest key

        # The sink is called EVERY cycle, including with an empty list. A cycle
        # is "time passed", not "something was perceived" — drives advance on
        # elapsed time, and boredom in particular only accumulates while
        # nothing arrives. Gating this on `if survivors:` meant an idle runtime
        # never ticked the core at all, so boredom stayed at zero forever and
        # no drive could ever produce an intent. CognitiveCore.tick() is built
        # for this: it derives `engaged = len(observations) > 0`, a value that
        # was otherwise unreachable.
        await self._sink(survivors)
