"""Affect engine tests — three-timescale dynamics.

All tests drive AffectEngine.update() with scripted inputs and explicit dt.
No wall-clock, no I/O, no randomness.
"""
from __future__ import annotations

import math

import pytest

from lyra_core.affect import AffectEngine


# ── Neutral start ─────────────────────────────────────────────────────────────

def test_neutral_start_valence_and_arousal_are_zero():
    engine = AffectEngine()
    assert engine.state.valence == pytest.approx(0.0)
    assert engine.state.arousal == pytest.approx(0.0)


def test_neutral_start_no_drift_under_zero_input():
    """A fresh engine with no input must stay neutral across many updates."""
    engine = AffectEngine()
    for _ in range(20):
        engine.update(0.1)
    assert engine.state.valence == pytest.approx(0.0)
    assert engine.state.arousal == pytest.approx(0.0)


# ── Fast response + decay toward mood ─────────────────────────────────────────

def test_negative_burst_drops_emotion_sharply():
    engine = AffectEngine()
    for _ in range(5):
        engine.update(0.1, valence_input=-1.0)
    assert engine.state.emotion.valence < -0.1


def test_emotion_decays_toward_mood_trajectory():
    """Trajectory shape: emotion moves monotonically toward mood, never past zero."""
    engine = AffectEngine()

    for _ in range(5):
        engine.update(0.1, valence_input=-1.0)

    after_burst_v = engine.state.emotion.valence

    # Capture trajectory across 5 zero-input steps
    trajectory = []
    for _ in range(5):
        engine.update(0.1)
        trajectory.append(engine.state.emotion.valence)

    # Trajectory is monotonically increasing (less negative each step)
    for i in range(len(trajectory) - 1):
        assert trajectory[i + 1] > trajectory[i], \
            f"Non-monotonic at step {i}: {trajectory}"

    # All intermediate values are between the burst value and zero
    # — they track toward mood (which is also negative), not toward zero
    for v in trajectory:
        assert v < 0, f"Crossed zero during decay: {v}"
    assert trajectory[0] > after_burst_v  # net upward movement started


def test_emotion_converges_to_mood_not_zero_after_long_recovery():
    """After a burst + long zero-input run, emotion rests near mood, not near 0."""
    engine = AffectEngine()

    for _ in range(5):
        engine.update(0.1, valence_input=-1.0)

    for _ in range(200):
        engine.update(0.1)

    final = engine.state
    assert final.mood.valence < -0.01, "Mood should retain negative history"
    # emotion and mood converged to the same negative value
    assert final.emotion.valence == pytest.approx(final.mood.valence, abs=0.02)


# ── Mood is slower than emotion ───────────────────────────────────────────────

def test_after_first_update_emotion_moved_more_than_mood():
    engine = AffectEngine()
    engine.update(0.1, valence_input=-1.0)
    state = engine.state
    assert abs(state.emotion.valence) > abs(state.mood.valence)


def test_mood_lags_emotion_under_sustained_input():
    """Sustained negative input: emotion moves faster and farther than mood."""
    engine = AffectEngine()
    for _ in range(10):
        engine.update(0.1, valence_input=-1.0)

    state = engine.state
    assert state.emotion.valence < state.mood.valence  # emotion more negative
    assert state.mood.valence < 0                       # mood also negative (lagging)
    assert abs(state.emotion.valence) > abs(state.mood.valence) * 2


# ── Temperament is the lever ──────────────────────────────────────────────────

def test_different_temperaments_same_inputs_different_final_states():
    """Short-fused vs easygoing: same input sequence → measurably different outcomes."""
    short_fused = AffectEngine(accum_rate=3.0, emotion_decay=0.5, mood_drift=0.1)
    easygoing   = AffectEngine(accum_rate=0.3, emotion_decay=5.0, mood_drift=0.1)

    dt = 0.1
    # Identical input sequence: 5 negative inputs then 5 zero-input recovery steps
    for _ in range(5):
        short_fused.update(dt, valence_input=-1.0)
        easygoing.update(dt, valence_input=-1.0)
    for _ in range(5):
        short_fused.update(dt)
        easygoing.update(dt)

    sf = short_fused.state
    eg = easygoing.state

    # Emotions diverged — not approximately equal
    assert sf.emotion.valence != pytest.approx(eg.emotion.valence, abs=0.05)
    # Short-fused accumulated more and recovered slower → more negative
    assert sf.emotion.valence < eg.emotion.valence


def test_temperament_neutral_default_has_no_built_in_bias():
    """Default temperament must start neutral — no persistence or quitting bias."""
    engine = AffectEngine()
    # State exposed via AffectState.temperament: the time-constant snapshot
    t = engine.state.temperament
    assert t is not None
    # accum_rate and emotion_decay stored in temperament.valence / .arousal
    assert t.valence > 0   # accumulation rate > 0 (engine responds to input)
    assert t.arousal > 0   # decay rate > 0 (engine recovers from input)


# ── Determinism ───────────────────────────────────────────────────────────────

def test_identical_inputs_produce_identical_final_state():
    def run() -> object:
        e = AffectEngine()
        e.update(0.1, valence_input=-0.5, arousal_input=0.3)
        e.update(0.2, valence_input=0.0)
        e.update(0.1, valence_input=0.8, arousal_input=-0.2)
        return e.state

    s1, s2 = run(), run()
    assert s1.emotion.valence == pytest.approx(s2.emotion.valence)
    assert s1.emotion.arousal == pytest.approx(s2.emotion.arousal)
    assert s1.mood.valence    == pytest.approx(s2.mood.valence)
    assert s1.mood.arousal    == pytest.approx(s2.mood.arousal)


# ── Persistence ───────────────────────────────────────────────────────────────

def test_serialize_restore_identical_state():
    engine = AffectEngine()
    for _ in range(5):
        engine.update(0.1, valence_input=-0.7, arousal_input=0.4)

    before = engine.state
    restored = AffectEngine.from_dict(engine.to_dict())
    after = restored.state

    assert after.emotion.valence == pytest.approx(before.emotion.valence)
    assert after.emotion.arousal == pytest.approx(before.emotion.arousal)
    assert after.mood.valence    == pytest.approx(before.mood.valence)
    assert after.mood.arousal    == pytest.approx(before.mood.arousal)


def test_restore_produces_identical_subsequent_trajectory():
    engine = AffectEngine()
    for _ in range(5):
        engine.update(0.1, valence_input=-0.7)

    restored = AffectEngine.from_dict(engine.to_dict())

    for _ in range(3):
        engine.update(0.1, valence_input=0.2)
        restored.update(0.1, valence_input=0.2)

    assert engine.state.emotion.valence == pytest.approx(restored.state.emotion.valence)
    assert engine.state.mood.valence    == pytest.approx(restored.state.mood.valence)


# ── State introspection ───────────────────────────────────────────────────────

def test_state_returns_affect_state_with_all_timescales_populated():
    """AffectEngine.state must return AffectState with emotion, mood, temperament set."""
    from lyra_core.interface import AffectState, AffectVector
    engine = AffectEngine()
    state = engine.state
    assert isinstance(state, AffectState)
    assert isinstance(state.emotion, AffectVector)
    assert isinstance(state.mood, AffectVector)
    assert state.temperament is not None


# ── Encouragement channel ──────────────────────────────────────────────────────
#
# encourage() temporarily slows the ACCUMULATION of negative valence input.
# It is not a reward: it never injects valence, never touches mood directly,
# and never alters temperament. It only shallows the downward slope while
# active, then expires.

def test_encourage_alone_does_not_raise_valence():
    """NO REWARD: encourage() + zero-input updates → valence stays ~0."""
    engine = AffectEngine()
    engine.encourage(strength=0.8, duration=30.0)
    for _ in range(20):
        engine.update(0.1)
    assert engine.state.emotion.valence == pytest.approx(0.0)
    assert engine.state.mood.valence == pytest.approx(0.0)


def test_encouragement_slows_negative_accumulation():
    """SLOWED ACCUMULATION: same negative-input sequence, encouraged engine ends
    LESS negative than baseline, and is never boosted upward at any step."""
    dt = 0.1
    encouraged = AffectEngine()
    encouraged.encourage(strength=0.5, duration=30.0)
    baseline = AffectEngine()

    prev_encouraged_v = encouraged.state.emotion.valence
    for _ in range(20):
        encouraged.update(dt, valence_input=-1.0)
        baseline.update(dt, valence_input=-1.0)

        encouraged_v = encouraged.state.emotion.valence
        baseline_v = baseline.state.emotion.valence

        # Never boosted upward: encouragement only shallows the downward slope.
        assert encouraged_v <= prev_encouraged_v, \
            f"Encouraged valence rose at this step: {prev_encouraged_v} -> {encouraged_v}"
        prev_encouraged_v = encouraged_v

        # Encouraged engine is less negative (or equal) than baseline at every step.
        assert encouraged_v >= baseline_v

    # And strictly less negative by the end.
    assert encouraged.state.emotion.valence > baseline.state.emotion.valence


def test_encouragement_expires_after_duration_then_full_rate_resumes():
    """EXPIRY: once `duration` of update() dt has elapsed, accumulation
    returns to the engine's normal (un-encouraged) rate."""
    dt = 0.1
    engine = AffectEngine()  # default accum_rate=1.0, emotion_decay=2.0
    engine.encourage(strength=0.5, duration=1.0)

    # Consume exactly `duration` of dt at the reduced rate.
    for _ in range(10):
        engine.update(dt, valence_input=-1.0)

    ev, mv = engine.state.emotion.valence, engine.state.mood.valence

    engine.update(dt, valence_input=-1.0)
    ev_after = engine.state.emotion.valence

    # Decay is integrated exactly, not by an explicit Euler step: the state
    # moves a fraction (1 - e^(-decay·dt)) of the way toward mood. The
    # discrimination this test exists for — full rate vs half rate — is
    # unaffected; only the decay term's form changed.
    relax = 1.0 - math.exp(-2.0 * dt)
    full_rate_expected = ev + 1.0 * (-1.0) * dt + (mv - ev) * relax
    half_rate_expected = ev + 0.5 * (-1.0) * dt + (mv - ev) * relax

    assert ev_after == pytest.approx(full_rate_expected)
    assert ev_after != pytest.approx(half_rate_expected)


def test_encouragement_no_effect_with_positive_or_neutral_input():
    """NO EFFECT ABSENT PRESSURE: encourage() then positive/neutral inputs
    produce a trajectory identical to an unencouraged engine."""
    dt = 0.1
    encouraged = AffectEngine()
    encouraged.encourage(strength=0.5, duration=30.0)
    baseline = AffectEngine()

    for v in [0.0, 0.5, 0.0, 1.0, 0.0]:
        encouraged.update(dt, valence_input=v)
        baseline.update(dt, valence_input=v)

    assert encouraged.state.emotion.valence == pytest.approx(baseline.state.emotion.valence)
    assert encouraged.state.mood.valence == pytest.approx(baseline.state.mood.valence)


def test_encourage_strength_above_one_floors_multiplier_at_zero():
    """strength > 1.0 → multiplier floors at 0, never reverses the sign of input."""
    dt = 0.1
    engine = AffectEngine()
    engine.encourage(strength=1.5, duration=30.0)
    engine.update(dt, valence_input=-1.0)
    # multiplier = max(0, 1 - 1.5) = 0 → no contribution from valence_input
    assert engine.state.emotion.valence == pytest.approx(0.0)


def test_serialize_restore_preserves_active_encouragement():
    """Persistence: an in-progress encouragement survives to_dict()/from_dict()
    and continues to apply identically on the restored engine."""
    dt = 0.1
    engine = AffectEngine()
    engine.encourage(strength=0.5, duration=30.0)
    for _ in range(3):
        engine.update(dt, valence_input=-1.0)

    restored = AffectEngine.from_dict(engine.to_dict())

    for _ in range(3):
        engine.update(dt, valence_input=-1.0)
        restored.update(dt, valence_input=-1.0)

    assert engine.state.emotion.valence == pytest.approx(restored.state.emotion.valence)
    assert engine.state.mood.valence == pytest.approx(restored.state.mood.valence)
