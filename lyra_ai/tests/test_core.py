"""Contract tests for lyra_core.interface.

Observation/Intent/AffectVector/AffectState tests assert shape and
construction. CognitiveCore and Harness tests drive the live cognitive loop:
tick() is async (asyncio.run() wrappers, matching test_runtime.py), ingests
observations, advances drives + affect, and selects gated intents.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from lyra_core.interface import (
    AffectState,
    AffectVector,
    CognitiveCore,
    Intent,
    IntentKind,
    Observation,
    ObservationKind,
)


# ── Observation ───────────────────────────────────────────────────────────────

def test_sensory_observation_minimal():
    obs = Observation(
        kind=ObservationKind.sensory,
        source="vision",
        content="a terminal window",
    )
    assert obs.kind == ObservationKind.sensory
    assert obs.source == "vision"
    assert obs.content == "a terminal window"
    assert isinstance(obs.ts, float)
    assert obs.ts > 0


def test_observation_reserved_prediction_slots_default_none():
    obs = Observation(kind=ObservationKind.action_outcome, source="self", content="done")
    assert obs.predicted is None
    assert obs.actual is None


def test_action_outcome_observation_carries_prediction_pairing():
    """Reserved competence-drive seam: expected vs actual outcome."""
    obs = Observation(
        kind=ObservationKind.action_outcome,
        source="motor",
        content="arm moved",
        predicted="arm reaches within 5 cm",
        actual="arm reached within 3 cm",
    )
    assert obs.predicted == "arm reaches within 5 cm"
    assert obs.actual == "arm reached within 3 cm"


def test_external_affect_observation_reserved_kind_is_constructable():
    """Reserved empathy seam — kind accepted now, unused until later."""
    obs = Observation(
        kind=ObservationKind.external_affect,
        source="conversation",
        content="Wilson seems tense",
    )
    assert obs.kind == ObservationKind.external_affect


def test_external_affect_observation_carry_affect_hint():
    """Reserved empathy slot: sensed valence/arousal of Wilson."""
    obs = Observation(
        kind=ObservationKind.external_affect,
        source="conversation",
        content="Wilson seems tense",
        affect_hint={"valence": -0.5, "arousal": 0.8},
    )
    assert obs.affect_hint == {"valence": -0.5, "arousal": 0.8}


def test_observation_affect_hint_defaults_none():
    obs = Observation(kind=ObservationKind.sensory, source="ears", content="quiet")
    assert obs.affect_hint is None


def test_observation_accepts_explicit_ts():
    t = time.time() - 10.0
    obs = Observation(kind=ObservationKind.sensory, source="self", content="x", ts=t)
    assert obs.ts == pytest.approx(t)


# ── Intent ────────────────────────────────────────────────────────────────────

def test_intent_construction():
    intent = Intent(kind=IntentKind.speak, payload={"text": "hello"})
    assert intent.kind == IntentKind.speak
    assert intent.payload == {"text": "hello"}
    assert isinstance(intent.ts, float)


def test_all_intent_kinds_are_constructable():
    """Every IntentKind must be constructable — reserved kinds included."""
    for kind in IntentKind:
        intent = Intent(kind=kind, payload={})
        assert intent.kind == kind


def test_intent_noop_has_empty_payload():
    intent = Intent(kind=IntentKind.noop, payload={})
    assert intent.payload == {}


# ── AffectVector ──────────────────────────────────────────────────────────────

def test_affect_vector_neutral_default():
    v = AffectVector()
    assert v.valence == 0.0
    assert v.arousal == 0.0
    assert v.control is None  # third axis reserved


def test_affect_vector_control_axis_reserved_but_settable():
    v = AffectVector(valence=0.2, arousal=0.4, control=0.6)
    assert v.control == pytest.approx(0.6)


# ── AffectState ───────────────────────────────────────────────────────────────

def test_affect_state_neutral_default():
    state = AffectState()
    assert state.valence == pytest.approx(0.0)
    assert state.arousal == pytest.approx(0.0)
    assert state.control is None


def test_affect_state_valence_arousal_delegate_to_emotion():
    """Top-level scalars must reflect the emotion vector, not be stored separately."""
    emotion = AffectVector(valence=0.7, arousal=0.3)
    state = AffectState(emotion=emotion)
    assert state.valence == pytest.approx(0.7)
    assert state.arousal == pytest.approx(0.3)


def test_affect_state_timescale_structure_phase0():
    """Phase 0: emotion populated, mood and temperament reserved seams (None)."""
    state = AffectState()
    assert isinstance(state.emotion, AffectVector)
    assert state.mood is None          # reserved seam
    assert state.temperament is None   # reserved seam


def test_affect_state_mood_and_temperament_accept_affect_vector():
    """Reserved seams must accept AffectVector when activated in later phases."""
    state = AffectState(
        emotion=AffectVector(valence=0.5, arousal=0.3),
        mood=AffectVector(valence=0.1, arousal=0.0),
        temperament=AffectVector(valence=0.0, arousal=0.2),
    )
    assert state.mood is not None
    assert state.temperament is not None


# ── CognitiveCore.tick() ───────────────────────────────────────────────────────

def test_tick_returns_correct_types():
    async def _run():
        core = CognitiveCore()
        obs = Observation(kind=ObservationKind.sensory, source="test", content="x")
        return await core.tick([obs])

    intents, affect = asyncio.run(_run())
    assert isinstance(intents, list)
    assert isinstance(affect, AffectState)


def test_tick_with_no_observations_emits_look_intent_from_boredom():
    """An idle tick: boredom pressure (0.01) outweighs the near-zero
    frustration from one tick's worth of idle drift, so the selector emits
    a look intent."""
    async def _run():
        core = CognitiveCore()
        return await core.tick([])

    intents, _ = asyncio.run(_run())
    assert len(intents) == 1
    assert intents[0].kind == IntentKind.look


def test_tick_with_no_observations_affect_stays_near_neutral():
    """One idle tick nudges affect slightly negative (boredom's own push) but
    stays bounded — not the exact 0.0 of the old stub, but small."""
    async def _run():
        core = CognitiveCore()
        return await core.tick([])

    _, affect = asyncio.run(_run())
    assert abs(affect.valence) < 0.01
    assert abs(affect.arousal) < 0.01


def test_tick_observations_without_signal_match_empty_tick():
    """Observations that carry no competence/memory signal — a sensory obs
    from a non-conversation source (memory not started, so it's dropped),
    an action_outcome with no predicted/actual, and the reserved
    external_affect kind — leave the core in the same state as an empty tick."""
    observations = [
        Observation(kind=ObservationKind.sensory, source="vision", content="screen"),
        Observation(kind=ObservationKind.action_outcome, source="self", content="done"),
        Observation(
            kind=ObservationKind.external_affect,
            source="conversation",
            content="Wilson smiles",
            affect_hint={"valence": 0.8, "arousal": 0.3},
        ),
    ]

    async def _run():
        with_obs = await CognitiveCore().tick(observations)
        without_obs = await CognitiveCore().tick([])
        return with_obs, without_obs

    (intents_a, affect_a), (intents_b, affect_b) = asyncio.run(_run())
    assert [(i.kind, i.payload) for i in intents_a] == [(i.kind, i.payload) for i in intents_b]
    assert affect_a.valence == pytest.approx(affect_b.valence)
    assert affect_a.arousal == pytest.approx(affect_b.arousal)


# ── Ingest routing: source threading ──────────────────────────────────────────

class _RecordingMemory:
    """Fake memory whose add_turn/add_observation record calls (no errors)."""

    def __init__(self) -> None:
        self.turns: list[tuple[str, str]] = []
        self.observations: list[tuple[str, str | None]] = []
        self.candidate_pool = None
        self.identity_engine = None
        self.structured_state = None

    async def add_turn(self, role: str, content: str) -> None:
        self.turns.append((role, content))

    async def add_observation(self, content: str, source: str | None = None) -> None:
        self.observations.append((content, source))


def test_ingest_routes_conversation_source_to_add_turn_user():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="conversation", content="hello")

    asyncio.run(core.tick([obs]))

    assert memory.turns == [("user", "hello")]
    assert memory.observations == []


def test_ingest_routes_lyra_source_to_add_turn_lyra():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="lyra", content="hi there")

    asyncio.run(core.tick([obs]))

    assert memory.turns == [("lyra", "hi there")]
    assert memory.observations == []


def test_ingest_routes_other_sources_to_add_observation_with_source():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="ears", content="ambient sound: rain")

    asyncio.run(core.tick([obs]))

    assert memory.observations == [("ambient sound: rain", "ears")]
    assert memory.turns == []


# ── CognitiveCore.introspect() ────────────────────────────────────────────────

def test_introspect_returns_affect_state():
    core = CognitiveCore()
    result = core.introspect()
    assert isinstance(result, AffectState)


def test_introspect_returns_neutral_affect_before_any_tick():
    core = CognitiveCore()
    state = core.introspect()
    assert state.valence == pytest.approx(0.0)
    assert state.arousal == pytest.approx(0.0)


def test_introspect_is_non_mutating():
    """introspect() must never change what tick() or a subsequent introspect()
    returns.

    Sequence: introspect (neutral, pre-tick) -> tick([]) (legitimately moves
    affect) -> introspect (must match the tick's result exactly) -> introspect
    again (must be idempotent)."""
    async def _run():
        core = CognitiveCore()
        before = core.introspect()
        _, after_tick = await core.tick([])
        after = core.introspect()
        again = core.introspect()
        return before, after_tick, after, again

    before, after_tick, after, again = asyncio.run(_run())

    assert before.valence == pytest.approx(0.0)
    assert before.arousal == pytest.approx(0.0)

    assert after.valence == pytest.approx(after_tick.valence)
    assert after.arousal == pytest.approx(after_tick.arousal)

    assert again.valence == pytest.approx(after.valence)
    assert again.arousal == pytest.approx(after.arousal)


# ── Harness ───────────────────────────────────────────────────────────────────

from lyra_core.gate import ALLOWED_KINDS, GateDecision
from lyra_core.harness import Harness, Script, TickRecord, failures, outcome, sensory


def test_run_produces_one_record_per_tick():
    async def _run():
        h = Harness()
        script: Script = [
            [sensory("frame 0")],
            [sensory("frame 1"), sensory("frame 2")],
            [],  # empty tick is valid
        ]
        return await h.run(script)

    trace = asyncio.run(_run())
    assert len(trace) == 3


def test_tick_records_have_sequential_indices():
    async def _run():
        h = Harness()
        return await h.run([[sensory("a")], [sensory("b")], [sensory("c")]])

    trace = asyncio.run(_run())
    assert [r.index for r in trace] == [0, 1, 2]


def test_tick_record_observations_match_input():
    obs0 = sensory("hello", source="vision")
    obs1 = outcome("done", predicted="fast", actual="slow")
    script: Script = [[obs0], [obs1]]

    async def _run():
        h = Harness()
        return await h.run(script)

    trace = asyncio.run(_run())
    assert trace[0].observations == [obs0]
    assert trace[1].observations == [obs1]


def test_tick_record_intents_are_allowed_kinds():
    """Every intent the live core proposes must be within ALLOWED_KINDS —
    the selector never has a path to a blocked kind."""
    async def _run():
        h = Harness()
        return await h.run([[sensory("x")], [sensory("y")]])

    trace = asyncio.run(_run())
    for r in trace:
        for i in r.intents:
            assert i.kind in ALLOWED_KINDS


def test_tick_record_affect_is_bounded_and_typed():
    async def _run():
        h = Harness()
        return await h.run([[sensory("x")], [sensory("y")], []])

    trace = asyncio.run(_run())
    for r in trace:
        assert isinstance(r.affect, AffectState)
        assert abs(r.affect.valence) < 0.1
        assert abs(r.affect.arousal) < 0.1


def test_run_empty_script_returns_empty_trace():
    async def _run():
        return await Harness().run([])

    assert asyncio.run(_run()) == []


def test_harness_constructs_own_core_when_none_given():
    """Harness() with no argument must build its own CognitiveCore and run."""
    async def _run():
        h = Harness()
        return await h.run([[sensory("self-constructed core")]])

    trace = asyncio.run(_run())
    assert len(trace) == 1


def test_harness_accepts_injected_core():
    async def _run():
        core = CognitiveCore()
        h = Harness(core=core)
        return await h.run([[sensory("injected")]])

    trace = asyncio.run(_run())
    assert len(trace) == 1


def test_failures_scaffold_produces_correct_tick_count():
    script = failures(5)
    assert len(script) == 5


def test_failures_scaffold_each_tick_has_one_action_outcome_observation():
    script = failures(5)
    for batch in script:
        assert len(batch) == 1
        assert batch[0].kind == ObservationKind.action_outcome


# ── Full-loop: failures -> frustration -> flip -> outcome -> candidate ────────

class _RecordingPool:
    """Fake CandidatePool — records what the consolidator sends it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def add_observation(self, trait_name: str, trait_value: str, category: str,
                              closed_vocabulary: bool = False) -> None:
        self.calls.append((trait_name, trait_value, category))


class _FakeMemory:
    """Minimal memory stand-in: only candidate_pool is populated.

    working_memory/identity_engine stay None, so ingest is a no-op and no
    bias is computed — isolates the affect/drive/selector/consolidator loop.
    """

    def __init__(self, pool: _RecordingPool) -> None:
        self.candidate_pool = pool
        self.working_memory = None
        self.identity_engine = None
        self.structured_state = None


def test_failures_full_loop_accumulates_frustration_overrides_drive_and_records_outcomes():
    """The live loop, end to end, against failures(10):
      - frustration accumulates: valence trends increasingly negative each tick
      - the flip: frustration overrides the boredom-driven 'look' intent
      - every intent stays within the allowed set
      - each failure outcome is recorded into the candidate pool
    """
    pool = _RecordingPool()
    core = CognitiveCore(memory=_FakeMemory(pool))

    async def _run():
        h = Harness(core=core)
        return await h.run(failures(10))

    trace = asyncio.run(_run())

    # Frustration accumulates.
    valences = [r.affect.valence for r in trace]
    for i in range(len(valences) - 1):
        assert valences[i + 1] < valences[i], f"valence did not worsen at step {i}: {valences}"

    # The flip: boredom's 'look' intent is overridden by frustration on at
    # least one tick.
    assert any(
        IntentKind.look not in [i.kind for i in r.intents]
        for r in trace
    ), "boredom drive was never overridden by frustration"

    # Gate boundary holds throughout.
    for r in trace:
        for i in r.intents:
            assert i.kind in ALLOWED_KINDS

    # Outcome -> candidate: every failure was recorded for trait consolidation.
    assert len(pool.calls) == 10
    assert all(category == "behavioral" for _, _, category in pool.calls)


# ── CognitiveCore gate wiring ─────────────────────────────────────────────────

def test_tick_intents_all_pass_gate_check():
    """Gate pass-through: tick()'s output already satisfies the gate — every
    intent it returns is in ALLOWED_KINDS."""
    async def _run():
        core = CognitiveCore()
        return await core.tick([])

    intents, affect = asyncio.run(_run())
    for i in intents:
        assert i.kind in ALLOWED_KINDS
    assert abs(affect.valence) < 0.01


def test_gate_intents_passes_allowed_kinds_through():
    core = CognitiveCore()
    speak = Intent(kind=IntentKind.speak, payload={"text": "hello"})
    noop = Intent(kind=IntentKind.noop, payload={})
    result = core._gate_intents([speak, noop])
    assert result == [speak, noop]


def test_gate_intents_drops_blocked_kind():
    core = CognitiveCore()
    research = Intent(kind=IntentKind.research, payload={})
    assert core._gate_intents([research]) == []


def test_gate_intents_mixed_list_keeps_allowed_drops_blocked():
    core = CognitiveCore()
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    research = Intent(kind=IntentKind.research, payload={})
    noop = Intent(kind=IntentKind.noop, payload={})
    result = core._gate_intents([speak, research, noop])
    assert result == [speak, noop]


def test_gate_intents_logs_blocked_intent(capsys):
    core = CognitiveCore()
    research = Intent(kind=IntentKind.research, payload={})
    core._gate_intents([research])
    captured = capsys.readouterr()
    assert "[gate] BLOCKED" in captured.out
    assert "research" in captured.out


def test_gate_intents_allowed_intent_produces_no_log(capsys):
    core = CognitiveCore()
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    core._gate_intents([speak])
    captured = capsys.readouterr()
    assert captured.out == ""


def test_default_gate_blocks_research():
    """Safe-by-default: no-arg CognitiveCore uses a real HarmGate and drops research."""
    core = CognitiveCore()
    research = Intent(kind=IntentKind.research, payload={})
    assert core._gate_intents([research]) == []


def test_injected_gate_that_blocks_all_returns_empty():
    """Injectable gate: a stub that blocks everything produces empty output."""
    class _BlockAll:
        def check(self, intent):
            return GateDecision(allowed=False, reason="stub: block all")

    core = CognitiveCore(gate=_BlockAll())
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    noop = Intent(kind=IntentKind.noop, payload={})
    assert core._gate_intents([speak, noop]) == []


def test_injected_gate_that_allows_all_passes_everything_through():
    """Injectable gate: a stub that allows everything lets all intents pass."""
    class _AllowAll:
        def check(self, intent):
            return GateDecision(allowed=True)

    core = CognitiveCore(gate=_AllowAll())
    research = Intent(kind=IntentKind.research, payload={})
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    assert core._gate_intents([research, speak]) == [research, speak]


def test_introspect_unaffected_by_gate_wiring():
    """introspect() is read-only; gate must not be involved."""
    core = CognitiveCore()
    state = core.introspect()
    assert state.valence == pytest.approx(0.0)
    assert state.arousal == pytest.approx(0.0)
