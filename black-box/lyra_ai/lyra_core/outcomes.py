"""lyra_core.outcomes — the return arrow of the developmental loop.

An action is only informative if its result can be observed. This component
watches what happens AFTER an intent executes and turns it into the
action_outcome Observation that CognitiveCore._ingest_outcome already expects.

WHAT IS BEING MEASURED
──────────────────────
Unprompted speech is selected by BoredomDrive. What boredom expects to gain
is ENGAGEMENT — engagement at the learnable edge is the only thing that
relieves it. So:

    predicted = "engagement"    (always — it is what the drive wanted)
    actual    = "engagement"    if a sensory observation arrived within
                                `window_seconds` of speaking
              = "silence"       if the window closed with nothing

CognitiveCore derives success = (predicted == actual), so success means "she
spoke and something came back."

WHY THE INPUT CHANNEL IS LOAD-BEARING
─────────────────────────────────────
This measurement is only truthful if the process that spoke can actually hear
a reply. With every poller disabled the runtime has no input at all, `actual`
would be "silence" forever, and the consolidator would write "abandons under
frustration" as Lyra's first trait — authoring a personality out of process
topology rather than letting one emerge. The wakeword poller is enabled for
exactly this reason. Disabling every sense again silently re-breaks the
signal; it does not merely reduce it.

Outcome observations are kind=action_outcome and are therefore NOT counted as
engagement themselves — an outcome can never satisfy a later pending one.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from lyra_core.interface import Intent, IntentKind, Observation, ObservationKind

ENGAGEMENT = "engagement"
SILENCE = "silence"

# How long Lyra waits for something to come back before calling it silence.
# Long enough for a human to notice and answer; short enough that the outcome
# still belongs to the utterance that caused it.
_DEFAULT_WINDOW_SECONDS: float = 30.0


@dataclass
class _Pending:
    """One executed utterance awaiting a verdict."""
    intent: Intent
    spoke_at: float
    engaged: bool = False


class OutcomeTracker:
    """Pending-outcome bookkeeping. Pure except for the injected clock.

    record_spoken() is called with intents that ACTUALLY executed (not merely
    approved). observe() is fed each cycle's observations. poll() is a Poller:
    it drains whichever outcomes have resolved and emits them as Observations.
    """

    def __init__(
        self,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._clock = clock
        self._pending: list[_Pending] = []
        self._seq: int = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def record_spoken(self, intent: Intent) -> None:
        """Begin watching for a response to an utterance that just executed."""
        if intent.kind is not IntentKind.speak:
            return
        self._pending.append(_Pending(intent=intent, spoke_at=self._clock()))

    def observe(self, observations: list[Observation]) -> None:
        """Note arrivals. Any sensory observation counts as engagement.

        action_outcome observations are excluded by construction, so the
        tracker can never satisfy its own pending entries.
        """
        if not self._pending:
            return
        if not any(o.kind is ObservationKind.sensory for o in observations):
            return
        for p in self._pending:
            p.engaged = True

    async def poll(self) -> list[Observation]:
        """Poller: emit outcomes for utterances that have resolved.

        Resolves early on engagement; otherwise waits out the full window.
        """
        now = self._clock()
        due: list[_Pending] = []
        still: list[_Pending] = []
        for p in self._pending:
            if p.engaged or (now - p.spoke_at) >= self._window:
                due.append(p)
            else:
                still.append(p)
        self._pending = still
        return [self._to_observation(p) for p in due]

    def _to_observation(self, p: _Pending) -> Observation:
        # The sequence number keeps content unique. PerceptionLoop dedups on
        # (source, content), and two identical outcomes are two real events —
        # unlike a repeated sense reading, which dedup exists to collapse.
        self._seq += 1
        actual = ENGAGEMENT if p.engaged else SILENCE
        reason = p.intent.payload.get("reason", "")
        print(
            f"[{datetime.now().isoformat()}] [outcomes] #{self._seq} "
            f"speech({reason}) -> {actual}"
        )
        return Observation(
            kind=ObservationKind.action_outcome,
            source="outcome",
            content=f"[{self._seq}] unprompted speech ({reason}) -> {actual}",
            predicted=ENGAGEMENT,
            actual=actual,
        )
