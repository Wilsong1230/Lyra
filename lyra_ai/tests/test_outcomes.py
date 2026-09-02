"""Tests for lyra_core.outcomes — the return arrow of the developmental loop."""
from __future__ import annotations

import asyncio

from lyra_core.actuators import SpeechActuator
from lyra_core.outcomes import ENGAGEMENT, SILENCE, OutcomeTracker
from lyra_core.interface import (
    AffectState,
    AffectVector,
    Intent,
    IntentKind,
    Observation,
    ObservationKind,
)


def _speak(reason: str = "boredom") -> Intent:
    return Intent(kind=IntentKind.speak, payload={"reason": reason})


def _sensory(content: str = "wake word detected") -> Observation:
    return Observation(kind=ObservationKind.sensory, source="wakeword", content=content)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ── verdicts ──────────────────────────────────────────────────────────────────

def test_engagement_within_window_resolves_as_success():
    clock = _Clock()
    tr = OutcomeTracker(window_seconds=30.0, clock=clock)
    tr.record_spoken(_speak())

    clock.advance(5.0)
    tr.observe([_sensory()])
    out = asyncio.run(tr.poll())

    assert len(out) == 1
    assert out[0].predicted == ENGAGEMENT
    assert out[0].actual == ENGAGEMENT
    assert out[0].kind is ObservationKind.action_outcome


def test_silence_past_window_resolves_as_failure():
    clock = _Clock()
    tr = OutcomeTracker(window_seconds=30.0, clock=clock)
    tr.record_spoken(_speak())

    clock.advance(31.0)
    out = asyncio.run(tr.poll())

    assert len(out) == 1
    assert out[0].predicted == ENGAGEMENT
    assert out[0].actual == SILENCE


def test_pending_inside_window_emits_nothing_yet():
    clock = _Clock()
    tr = OutcomeTracker(window_seconds=30.0, clock=clock)
    tr.record_spoken(_speak())

    clock.advance(10.0)
    assert asyncio.run(tr.poll()) == []
    assert tr.pending_count == 1


def test_engagement_resolves_early_without_waiting_out_window():
    clock = _Clock()
    tr = OutcomeTracker(window_seconds=30.0, clock=clock)
    tr.record_spoken(_speak())
    tr.observe([_sensory()])

    out = asyncio.run(tr.poll())  # no clock advance at all
    assert len(out) == 1
    assert out[0].actual == ENGAGEMENT


# ── the self-satisfaction trap ────────────────────────────────────────────────

def test_action_outcome_observations_do_not_count_as_engagement():
    """An emitted outcome must never satisfy a later pending utterance —
    otherwise the loop feeds itself and every outcome reads as success."""
    clock = _Clock()
    tr = OutcomeTracker(window_seconds=30.0, clock=clock)
    tr.record_spoken(_speak())

    prior_outcome = Observation(
        kind=ObservationKind.action_outcome,
        source="outcome",
        content="[1] unprompted speech (boredom) -> silence",
        predicted=ENGAGEMENT,
        actual=SILENCE,
    )
    tr.observe([prior_outcome])
    clock.advance(31.0)
    out = asyncio.run(tr.poll())

    assert out[0].actual == SILENCE, "an outcome must not count as engagement"


def test_only_speak_intents_are_tracked():
    tr = OutcomeTracker()
    tr.record_spoken(Intent(kind=IntentKind.look, payload={"reason": "curiosity"}))
    tr.record_spoken(Intent(kind=IntentKind.noop, payload={}))
    assert tr.pending_count == 0


def test_emitted_content_is_unique_so_dedup_does_not_collapse_outcomes():
    """PerceptionLoop dedups on (source, content). Two identical outcomes are
    two real events and must both survive."""
    clock = _Clock()
    tr = OutcomeTracker(window_seconds=1.0, clock=clock)

    tr.record_spoken(_speak())
    clock.advance(2.0)
    first = asyncio.run(tr.poll())

    tr.record_spoken(_speak())
    clock.advance(2.0)
    second = asyncio.run(tr.poll())

    assert first[0].content != second[0].content


# ── end to end through CognitiveCore ──────────────────────────────────────────

class _RecordingPool:
    def __init__(self) -> None:
        self.added: list[tuple] = []

    async def add_observation(self, name, value, category, closed_vocabulary=False):
        self.added.append((name, value, category, closed_vocabulary))


class _FakeMemory:
    def __init__(self) -> None:
        self.candidate_pool = _RecordingPool()
        self.working_memory = None
        self.identity_engine = None
        self.structured_state = None

    async def start(self): pass
    async def stop(self): pass
    async def add_turn(self, *a, **k): pass
    async def add_observation(self, *a, **k): pass


def test_outcome_observation_reaches_consolidator_and_writes_a_candidate():
    """The whole return arrow: an outcome Observation fed to tick() must run
    OutcomeConsolidator and land one row in the candidate pool."""
    from lyra_core.interface import CognitiveCore

    async def _run():
        mem = _FakeMemory()
        core = CognitiveCore(memory=mem)
        tr = OutcomeTracker(window_seconds=0.0)

        tr.record_spoken(_speak())
        outs = await tr.poll()
        assert len(outs) == 1

        await core.tick(outs, 0.2)
        return mem.candidate_pool.added

    added = asyncio.run(_run())
    assert len(added) == 1, "OutcomeConsolidator must have written one candidate"
    name, value, category, closed = added[0]
    assert "abandons" in name or "completes" in name or "persists" in name
    assert category == "behavioral"
    assert closed is True, (
        "developmental trait names must dedup by exact name — semantic dedup "
        "merges 'persists under frustration' into 'abandons under frustration'"
    )
