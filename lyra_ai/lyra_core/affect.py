"""lyra_core.affect — three-timescale affect dynamics.

AffectEngine holds a live AffectState with three timescales:
  emotion     — fast; responds strongly/quickly to inputs; decays toward mood
  mood        — medium; slow running average; drifts toward recent emotion
  temperament — the time constants themselves (~fixed in Phase 3.1)

Dynamics (simultaneous Euler step, applied to both valence and arousal axes):
  Δemotion = accum_rate * input * dt  +  emotion_decay * (mood - emotion) * dt
  Δmood    = mood_drift * (emotion - mood) * dt

Public valence/arousal delegate to emotion (Phase 0 surface preserved).
"""
from __future__ import annotations

from lyra_core.interface import AffectState, AffectVector

# Floating-point tolerance for encouragement-timer expiry checks.
_ENCOURAGE_EPS = 1e-9


class AffectEngine:
    """Live affect substrate with three-timescale dynamics."""

    def __init__(
        self,
        accum_rate: float = 1.0,
        emotion_decay: float = 2.0,
        mood_drift: float = 0.2,
    ) -> None:
        self._accum_rate = accum_rate
        self._emotion_decay = emotion_decay
        self._mood_drift = mood_drift

        self._emotion_v: float = 0.0
        self._emotion_a: float = 0.0
        self._mood_v: float = 0.0
        self._mood_a: float = 0.0

        # Encouragement channel: temporarily slows accumulation of NEGATIVE
        # valence input only. Never injects valence, never touches mood or
        # temperament directly. Decays over `duration` of update() dt.
        self._encourage_strength: float = 0.0
        self._encourage_remaining: float = 0.0

    # ── Encouragement ─────────────────────────────────────────────────────────

    def encourage(self, strength: float = 0.5, duration: float = 30.0) -> None:
        """Temporarily shallow the accumulation of negative valence input.

        Applies a multiplier of max(0, 1 - strength) to the accumulation rate
        for negative valence_input only, for the next `duration` of update()
        dt. Positive valence input and arousal are unaffected. This is not a
        reward: it never raises valence directly.
        """
        self._encourage_strength = strength
        self._encourage_remaining = duration

    # ── Public update ─────────────────────────────────────────────────────────

    def update(
        self,
        dt: float,
        valence_input: float = 0.0,
        arousal_input: float = 0.0,
    ) -> None:
        """Advance affect by dt given optional external inputs.

        Pure, deterministic — no I/O, no randomness.  Control axis reserved.
        """
        ev, ea = self._emotion_v, self._emotion_a
        mv, ma = self._mood_v, self._mood_a

        valence_accum_rate = self._accum_rate
        if self._encourage_remaining > _ENCOURAGE_EPS and valence_input < 0.0:
            valence_accum_rate *= max(0.0, 1.0 - self._encourage_strength)

        # Emotion: accumulate input, then decay toward mood
        self._emotion_v = ev + valence_accum_rate * valence_input * dt \
                             + self._emotion_decay * (mv - ev) * dt
        self._emotion_a = ea + self._accum_rate * arousal_input * dt \
                             + self._emotion_decay * (ma - ea) * dt

        # Mood: drift toward emotion (uses old emotion values)
        self._mood_v = mv + self._mood_drift * (ev - mv) * dt
        self._mood_a = ma + self._mood_drift * (ea - ma) * dt

        self._encourage_remaining = max(0.0, self._encourage_remaining - dt)

    # ── State introspection ───────────────────────────────────────────────────

    @property
    def state(self) -> AffectState:
        """Current AffectState snapshot with all three timescales populated."""
        return AffectState(
            emotion=AffectVector(valence=self._emotion_v, arousal=self._emotion_a),
            mood=AffectVector(valence=self._mood_v, arousal=self._mood_a),
            # temperament: valence=accum_rate, arousal=emotion_decay, control=mood_drift
            temperament=AffectVector(
                valence=self._accum_rate,
                arousal=self._emotion_decay,
                control=self._mood_drift,
            ),
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "accum_rate":    self._accum_rate,
            "emotion_decay": self._emotion_decay,
            "mood_drift":    self._mood_drift,
            "emotion_v":     self._emotion_v,
            "emotion_a":     self._emotion_a,
            "mood_v":        self._mood_v,
            "mood_a":        self._mood_a,
            "encourage_strength":  self._encourage_strength,
            "encourage_remaining": self._encourage_remaining,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AffectEngine:
        engine = cls(
            accum_rate=d["accum_rate"],
            emotion_decay=d["emotion_decay"],
            mood_drift=d["mood_drift"],
        )
        engine._emotion_v = d["emotion_v"]
        engine._emotion_a = d["emotion_a"]
        engine._mood_v = d["mood_v"]
        engine._mood_a = d["mood_a"]
        engine._encourage_strength = d.get("encourage_strength", 0.0)
        engine._encourage_remaining = d.get("encourage_remaining", 0.0)
        return engine
