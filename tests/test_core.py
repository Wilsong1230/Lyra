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
