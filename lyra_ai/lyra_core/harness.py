"""lyra_core.harness — test rig for driving CognitiveCore in isolation.

No services, no HTTP, no memory daemon (unless injected). Feed scripted
observation sequences across multiple ticks, collect outputs into an
inspectable trace. Inject a real or partially-faked core via Harness(core=...).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from lyra_core.interface import (
    AffectState,
    CognitiveCore,
    Intent,
    Observation,
    ObservationKind,
)

# ── Types ─────────────────────────────────────────────────────────────────────

@dataclass
class TickRecord:
    """Everything produced and consumed in one tick."""
    observations: list[Observation]
    intents:      list[Intent]
    affect:       AffectState
    index:        int


# One inner list of observations per tick; a tick may receive zero, one, or many.
Script = list[list[Observation]]


# ── Harness ───────────────────────────────────────────────────────────────────

class Harness:
    """Drives a CognitiveCore through a scripted observation sequence.

    Usage:
        h = Harness()
        trace = await h.run([
            [sensory("screen shows code")],
            [outcome("command ran", predicted="exit 0", actual="exit 1")],
        ])
        assert isinstance(trace[0].affect.valence, float)

    Inject a real (or partially-faked) core to assert emergent affect changes.
        h = Harness(core=real_core)
    """

    def __init__(self, core: CognitiveCore | None = None) -> None:
        self._core = core if core is not None else CognitiveCore()

    async def run(self, script: Script, dt: float = 0.1) -> list[TickRecord]:
        """Execute each batch in script through the core, return the full trace."""
        trace: list[TickRecord] = []
        for index, batch in enumerate(script):
            intents, affect = await self._core.tick(batch, dt)
            trace.append(TickRecord(
                observations=list(batch),
                intents=intents,
                affect=affect,
                index=index,
            ))
        return trace


# ── Builder helpers ───────────────────────────────────────────────────────────

def sensory(content: str, source: str = "test") -> Observation:
    """Convenience constructor for a sensory observation."""
    return Observation(
        kind=ObservationKind.sensory,
        source=source,
        content=content,
    )


def outcome(
    content: str,
    *,
    predicted: str | None = None,
    actual: str | None = None,
    source: str = "test",
) -> Observation:
    """Convenience constructor for an action_outcome observation.

    predicted/actual stay None-able for Phase 0; Phase 3 fills them in
    when the competence drive needs a numeric prediction-error magnitude.
    """
    return Observation(
        kind=ObservationKind.action_outcome,
        source=source,
        content=content,
        predicted=predicted,
        actual=actual,
    )


def failures(n: int) -> Script:
    """Frustration test sequence.

    Returns a Script of n single-observation ticks, each an action_outcome
    representing a failed action. Against a live core, repeated failures
    route into RelationalDrive and accumulate negative affect, eventually
    flipping ActionSelector from persist to abandon.
    """
    return [
        [outcome(f"action failed (attempt {i + 1})", predicted="success", actual="failure")]
        for i in range(n)
    ]
