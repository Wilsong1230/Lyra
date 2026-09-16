"""lyra_core.affect — two-timescale affect dynamics, exactly integrated.

AffectEngine holds a live AffectState:
  emotion     — fast; responds strongly/quickly to inputs; decays toward mood
  mood        — medium; slow running average; drifts toward recent emotion
  temperament — the time constants (accum_rate, emotion_decay, mood_drift).
                Fixed at construction/restore; update() never advances it.
                Measured (CP-A.1, docs/AFFECT_CHARACTERIZATION.md): not a
                third dynamic timescale, so "three timescales" above is
                register language, not this module's behavior.

Dynamics, per axis (valence and arousal are independent copies of the same
2x2 linear system):
  emotion' = forcing + emotion_decay * (mood - emotion)
  mood'    = mood_drift * (emotion - mood)
where forcing = accum_rate * input, held constant over one update() step.

CP-A.1 measured that integrating this pair as two independent single-variable
relaxations (each exact in isolation, each using the OTHER variable's
pre-step value — a Jacobi/operator-split scheme) does not agree across tick
rates: at dt=60 with emotion_decay=2.0/mood_drift=0.2 the pair nearly swaps
every step instead of converging. CP-A.2 replaces that split with the exact
solution of the coupled 2x2 system over one step — see _step() — which is
provably tick-rate invariant (docs/AFFECT_CHARACTERIZATION.md, "agreement
check"): running the same wall-clock span at any dt produces the same
terminal state to floating-point precision, because it is not a
discretization of the ODE, it is that ODE's own solution evaluated at t=dt.
"""
from __future__ import annotations

import math

from lyra_core.interface import AffectState, AffectVector

# Floating-point tolerance for encouragement-timer expiry checks.
_ENCOURAGE_EPS = 1e-9


class AffectEngine:
    """Live affect substrate: emotion and mood, exactly coupled; temperament static."""

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
        valence_accum_rate = self._accum_rate
        if self._encourage_remaining > _ENCOURAGE_EPS and valence_input < 0.0:
            valence_accum_rate *= max(0.0, 1.0 - self._encourage_strength)

        self._emotion_v, self._mood_v = self._step(
            self._emotion_v, self._mood_v, valence_accum_rate * valence_input, dt,
        )
        self._emotion_a, self._mood_a = self._step(
            self._emotion_a, self._mood_a, self._accum_rate * arousal_input, dt,
        )

        self._encourage_remaining = max(0.0, self._encourage_remaining - dt)

    def _step(self, e0: float, m0: float, forcing: float, dt: float) -> tuple[float, float]:
        """Exact solution at t=dt of e'=forcing+k_e(m-e), m'=k_m(e-m).

        Decompose into the sum S = k_m*e + k_e*m and the difference D = e-m.
        S has no restoring term of its own — k_m*e' + k_e*m' = k_m*forcing
        exactly, regardless of e or m — so S is conserved when forcing=0 and
        otherwise integrates forcing directly: S(dt) = S0 + k_m*forcing*dt.
        D decouples into a single ordinary relaxation, D' = forcing - r*D
        where r = k_e+k_m (rate 1/tau_e + 1/tau_m, per the register
        correction), whose exact solution relaxes D toward its forced
        offset forcing/r at fraction (1 - e^(-r·dt)) — the same
        never-overshoots-at-any-dt closed form CP-A already used for a
        single variable, now applied to the coupled pair's own difference
        channel instead of to each variable against the other's stale
        value. e and m are then read back off (S, D) algebraically; no
        substep, no operator split, exact for any dt > 0 including a
        single tick spanning the whole gap.
        """
        k_e, k_m = self._emotion_decay, self._mood_drift
        r = k_e + k_m
        relax = 1.0 - math.exp(-r * dt)

        d0 = e0 - m0
        s0 = k_m * e0 + k_e * m0

        d1 = d0 * (1.0 - relax) + (forcing / r) * relax
        s1 = s0 + k_m * forcing * dt

        e1 = (s1 + k_e * d1) / r
        m1 = (s1 - k_m * d1) / r
        return e1, m1

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
