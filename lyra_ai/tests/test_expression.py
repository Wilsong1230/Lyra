"""Tests for lyra_core.expression — Layer 3 prose hints.

prose_hint(affect) translates the live AffectState into a per-turn style
directive. It must modulate HOW Lyra speaks, never narrate that she has
feelings — the anti-decoration sweep is the firewall for that rule.
"""
from __future__ import annotations

from lyra_core.expression import prose_hint
from lyra_core.interface import AffectState, AffectVector


def _affect(valence: float, arousal: float) -> AffectState:
    return AffectState(emotion=AffectVector(valence=valence, arousal=arousal))


# ── Region mapping ───────────────────────────────────────────────────────────

def test_strong_negative_high_arousal_yields_terse_direct_hint():
    hint = prose_hint(_affect(valence=-0.6, arousal=0.6))
    assert hint == "Keep responses brief and direct. Don't soften or elaborate."


def test_strong_negative_low_arousal_yields_plain_hint():
    hint = prose_hint(_affect(valence=-0.6, arousal=0.0))
    assert hint == "Respond plainly and with little elaboration."


def test_strong_positive_high_arousal_yields_expansive_hint():
    hint = prose_hint(_affect(valence=0.6, arousal=0.6))
    assert hint == "It's fine to be expansive and to ask questions."


def test_strong_positive_low_arousal_yields_no_hint():
    assert prose_hint(_affect(valence=0.6, arousal=0.0)) == ""


def test_near_neutral_yields_no_hint():
    assert prose_hint(_affect(valence=0.0, arousal=0.0)) == ""


def test_near_neutral_with_small_arousal_yields_no_hint():
    assert prose_hint(_affect(valence=0.01, arousal=0.05)) == ""


# ── Mood blending ────────────────────────────────────────────────────────────

def test_mood_blends_with_emotion():
    """Emotion alone is just under threshold, but mood pushes it over."""
    affect = AffectState(
        emotion=AffectVector(valence=-0.2, arousal=0.2),
        mood=AffectVector(valence=-0.2, arousal=0.2),
    )
    assert prose_hint(affect) == "Keep responses brief and direct. Don't soften or elaborate."


def test_missing_mood_does_not_crash():
    """AffectState.mood is a reserved seam and may legitimately be None."""
    affect = AffectState(emotion=AffectVector(valence=-0.6, arousal=0.6), mood=None)
    assert prose_hint(affect) == "Keep responses brief and direct. Don't soften or elaborate."


# ── Read-only ────────────────────────────────────────────────────────────────

def test_prose_hint_does_not_mutate_affect():
    affect = AffectState(
        emotion=AffectVector(valence=-0.6, arousal=0.6),
        mood=AffectVector(valence=0.1, arousal=-0.1),
    )
    before = (affect.emotion.valence, affect.emotion.arousal, affect.mood.valence, affect.mood.arousal)
    prose_hint(affect)
    after = (affect.emotion.valence, affect.emotion.arousal, affect.mood.valence, affect.mood.arousal)
    assert before == after


# ── Anti-decoration firewall ─────────────────────────────────────────────────

_BANNED_PHRASES = [
    "you are frustrated", "you're frustrated",
    "you are angry", "you're angry",
    "you are sad", "you're sad",
    "you are happy", "you're happy",
    "you feel",
    "your mood",
    "your emotion",
    "express your",
    "mention your feelings",
]


def test_prose_hint_never_names_emotions_across_affect_sweep():
    """No region's hint may instruct Lyra to narrate or label her own state —
    style directives only, across the full input space."""
    sweep = [-1.0, -0.6, -0.3, -0.15, 0.0, 0.15, 0.3, 0.6, 1.0]
    for valence in sweep:
        for arousal in sweep:
            hint = prose_hint(_affect(valence, arousal))
            lowered = hint.lower()
            for phrase in _BANNED_PHRASES:
                assert phrase not in lowered, (
                    f"banned phrase {phrase!r} found in hint for "
                    f"valence={valence}, arousal={arousal}: {hint!r}"
                )
