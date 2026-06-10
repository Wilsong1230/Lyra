"""lyra_core.expression — Layer 3: affect colors HOW Lyra speaks.

prose_hint() translates the live AffectState into a short, per-turn style
directive appended to the system prompt. It modulates delivery — brevity,
elaboration, energy — and never WHAT she says about her own state.

HARD RULE: a hint must never name an emotion as hers ("you are frustrated",
"you feel..."), and must never instruct her to mention her mood. Naming
states produces narration instead of having them shape behavior — that is
the decorative failure this module exists to avoid.
"""
from __future__ import annotations

from lyra_core.interface import AffectState

# Region thresholds — tunable in Phase 4 observation.
# VALENCE matches development._FRUSTRATION_THRESHOLD's magnitude (the
# existing convention for "strong" affect). AROUSAL is set lower because
# drive-driven arousal pushes are typically smaller than valence pushes.
_VALENCE_THRESHOLD = 0.3
_AROUSAL_THRESHOLD = 0.15

_NEGATIVE_HIGH_AROUSAL_HINT = "Keep responses brief and direct. Don't soften or elaborate."
_NEGATIVE_LOW_AROUSAL_HINT = "Respond plainly and with little elaboration."
_POSITIVE_HIGH_AROUSAL_HINT = "It's fine to be expansive and to ask questions."


def _blend(affect: AffectState) -> tuple[float, float]:
    """Combine the fast (emotion) and medium (mood) timescales into one
    per-turn signal. Emotion carries the moment; mood is the backdrop it's
    spoken against. mood is a reserved seam and may be None."""
    valence = affect.emotion.valence
    arousal = affect.emotion.arousal
    if affect.mood is not None:
        valence += affect.mood.valence
        arousal += affect.mood.arousal
    return valence, arousal


def prose_hint(affect: AffectState) -> str:
    """Style directive for the current AffectState, or "" near neutral.

    Regions (by blended valence/arousal):
      strong negative + high arousal -> terse, direct
      strong negative + low arousal  -> plain, unelaborated
      strong positive + high arousal -> expansive, curious
      everything else (including near-neutral) -> "" (no hint)
    """
    valence, arousal = _blend(affect)

    if valence <= -_VALENCE_THRESHOLD:
        if arousal >= _AROUSAL_THRESHOLD:
            return _NEGATIVE_HIGH_AROUSAL_HINT
        return _NEGATIVE_LOW_AROUSAL_HINT

    if valence >= _VALENCE_THRESHOLD and arousal >= _AROUSAL_THRESHOLD:
        return _POSITIVE_HIGH_AROUSAL_HINT

    return ""
