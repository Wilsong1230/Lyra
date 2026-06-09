"""Contract tests for lyra_core.interface — Phase 0.

These tests assert shape and construction only. No behavior is tested yet.
The reserved seams (external_affect, predicted/actual, affect timescales)
must all be constructable so downstream phases can rely on them.
"""
from __future__ import annotations

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


# ── CognitiveCore ─────────────────────────────────────────────────────────────

def test_tick_returns_correct_types():
    core = CognitiveCore()
    obs = Observation(kind=ObservationKind.sensory, source="test", content="x")
    intents, affect = core.tick([obs])
    assert isinstance(intents, list)
    assert isinstance(affect, AffectState)


def test_tick_returns_empty_intents_phase0():
    core = CognitiveCore()
    intents, _ = core.tick([])
    assert intents == []


def test_tick_returns_neutral_affect_phase0():
    core = CognitiveCore()
    _, affect = core.tick([])
    assert affect.valence == pytest.approx(0.0)
    assert affect.arousal == pytest.approx(0.0)


def test_tick_ignores_observations_phase0():
    """Phase 0 stub: any observations produce the same neutral output."""
    core = CognitiveCore()
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
    intents, affect = core.tick(observations)
    assert intents == []
    assert affect.valence == pytest.approx(0.0)
    assert affect.arousal == pytest.approx(0.0)


# ── CognitiveCore.introspect() ────────────────────────────────────────────────

def test_introspect_returns_affect_state():
    core = CognitiveCore()
    result = core.introspect()
    assert isinstance(result, AffectState)


def test_introspect_returns_neutral_affect_phase0():
    core = CognitiveCore()
    state = core.introspect()
    assert state.valence == pytest.approx(0.0)
    assert state.arousal == pytest.approx(0.0)


def test_introspect_is_non_mutating():
    """introspect() must never change what tick() or a subsequent introspect() returns.

    Sequence: introspect → tick([]) → introspect.
    All three must yield neutral, equivalent affect — the read-only port
    must not advance the core or alter its internal state.
    """
    core = CognitiveCore()
    before = core.introspect()
    _, after_tick = core.tick([])
    after = core.introspect()

    assert before.valence == pytest.approx(0.0)
    assert after_tick.valence == pytest.approx(0.0)
    assert after.valence == pytest.approx(0.0)
    assert before.arousal == pytest.approx(after.arousal)


# ── Harness ───────────────────────────────────────────────────────────────────

from lyra_core.harness import Harness, Script, TickRecord, failures, outcome, sensory


def test_run_produces_one_record_per_tick():
    h = Harness()
    script: Script = [
        [sensory("frame 0")],
        [sensory("frame 1"), sensory("frame 2")],
        [],  # empty tick is valid
    ]
    trace = h.run(script)
    assert len(trace) == 3


def test_tick_records_have_sequential_indices():
    h = Harness()
    trace = h.run([[sensory("a")], [sensory("b")], [sensory("c")]])
    assert [r.index for r in trace] == [0, 1, 2]


def test_tick_record_observations_match_input():
    h = Harness()
    obs0 = sensory("hello", source="vision")
    obs1 = outcome("done", predicted="fast", actual="slow")
    script: Script = [[obs0], [obs1]]
    trace = h.run(script)
    assert trace[0].observations == [obs0]
    assert trace[1].observations == [obs1]


def test_tick_record_intents_empty_against_stub():
    h = Harness()
    trace = h.run([[sensory("x")], [sensory("y")]])
    assert all(r.intents == [] for r in trace)


def test_tick_record_affect_neutral_against_stub():
    h = Harness()
    trace = h.run([[sensory("x")], [sensory("y")], []])
    for r in trace:
        assert isinstance(r.affect, AffectState)
        assert r.affect.valence == pytest.approx(0.0)
        assert r.affect.arousal == pytest.approx(0.0)


def test_run_empty_script_returns_empty_trace():
    h = Harness()
    assert h.run([]) == []


def test_harness_constructs_own_core_when_none_given():
    """Harness() with no argument must build its own CognitiveCore and run."""
    h = Harness()
    trace = h.run([[sensory("self-constructed core")]])
    assert len(trace) == 1


def test_harness_accepts_injected_core():
    core = CognitiveCore()
    h = Harness(core=core)
    trace = h.run([[sensory("injected")]])
    assert len(trace) == 1


def test_failures_scaffold_produces_correct_tick_count():
    script = failures(5)
    assert len(script) == 5


def test_failures_scaffold_each_tick_has_one_action_outcome_observation():
    from lyra_core.interface import ObservationKind
    script = failures(5)
    for batch in script:
        assert len(batch) == 1
        assert batch[0].kind == ObservationKind.action_outcome


def test_failures_scaffold_runs_cleanly_through_stub():
    """Phase 0: failures(n) produces n neutral records against the stub.

    # Phase 3: assert affect degrades here — frustration must accumulate
    # across repeated action_outcome failures and eventually override a drive.
    """
    h = Harness()
    trace = h.run(failures(5))
    assert len(trace) == 5
    for r in trace:
        assert r.affect.valence == pytest.approx(0.0)
        # Phase 3: assert r.affect.valence < 0.0 after n failures


# ── CognitiveCore gate wiring (Phase 2.2) ────────────────────────────────────

from lyra_core.gate import GateDecision


def test_tick_with_gate_wiring_still_returns_empty_list():
    """Gate pass-through must not change the empty-intent Phase 0 behavior."""
    core = CognitiveCore()
    intents, affect = core.tick([])
    assert intents == []
    assert affect.valence == pytest.approx(0.0)


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
