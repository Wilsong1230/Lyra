"""lyra_core.action_selection — affect-aware intent selection.

ActionSelector converts drive pressures + current AffectState into Intents.
It does NOT call the HarmGate — that chokepoint lives in tick() (Phase 3.6).
The selector only ever proposes intents in ALLOWED_KINDS, so gate + selector
are never in conflict, but the gate is not bypassed: tick() still runs every
intent through it.

THE FEEDBACK ARROW
──────────────────
Each active drive proposes persisting at its goal with strength equal to its
pressure value.  Accumulated negative affect (frustration) acts as a
counter-weight.  The comparison is explicit and tunable:

    frustration_score = max(0, -affect.valence) * affect_weight
    persist if and only if:  drive_pressure > frustration_score

When frustration_score ≥ drive_pressure the selection FLIPS: the drive's
preferred intent is dropped in favour of noop (abandon / redirect).

affect_weight is the single tuning lever.  Higher values mean affect can
override weaker drives; lower values make the selector more drive-determined.
The flip point is therefore:

    |valence| = drive_pressure / affect_weight

Because AffectEngine.emotion decays toward AffectEngine.mood (Phase 3.1),
the speed at which frustration accumulates and the point at which it flips
the selection are set by temperament — fast-accumulate/slow-decay
temperaments flip earlier than slow/fast ones (see test_action_selection.py).

Drive → intent mapping (within ALLOWED_KINDS only):
  "boredom"    → IntentKind.look  (engage / pursue curiosity)
  "boredom" ≥ speak_threshold
               → IntentKind.speak (reach out — unprompted speech)
  "relational" → IntentKind.speak (address recurring friction)
  (nothing)    → IntentKind.noop  (abandon / wait)

WHY BOREDOM ALSO SPEAKS
───────────────────────
Relational was the only producer of IntentKind.speak, and relational pressure
is zero until observe_recurrence() fires — which only happens on an
action_outcome failure, which only exists once actions execute. Speech was
therefore unreachable, and unreachable by a cycle: speaking needed an outcome,
and the outcome needed speech.

Boredom breaks the cycle because it accumulates unconditionally against an
idle world. Sustained boredom escalating from looking to reaching out is also
the honest reading of the drive: the world offers nothing to engage with, so
Lyra opens a channel herself. Relational→speak is unchanged and remains a
distinct signal (addressing recurring friction, not filling emptiness).
"""
from __future__ import annotations

from lyra_core.interface import AffectState, Intent, IntentKind


class ActionSelector:
    """Converts (drive_pressures, affect) → list[Intent].

    Parameters
    ----------
    affect_weight : float
        Scales how strongly negative valence counterweights drive pressure.
        Default 1.0 means frustration magnitude == drive pressure → flip.
    """

    def __init__(self, affect_weight: float = 1.0, speak_threshold: float = 1.0) -> None:
        self._affect_weight = affect_weight
        self._speak_threshold = speak_threshold

    def select(
        self,
        drive_pressures: dict[str, float],
        affect: AffectState,
        bias=None,
    ) -> list[Intent]:
        """Return the chosen Intent list.

        Reads drive pressures and affect values passed in — does NOT reach
        into drive or affect engine internals.  Deterministic given inputs.

        bias: optional duck-typed SelectionBias; uses .affect_weight_delta to
        shift the effective affect_weight (negative = more persistent, positive
        = quits easier).  Clamped to 0.0 at the low end.
        """
        effective_weight = self._affect_weight
        if bias is not None:
            effective_weight = max(0.0, effective_weight + bias.affect_weight_delta)
        # Frustration: how much accumulated negative affect pushes back
        frustration = max(0.0, -affect.valence) * effective_weight

        chosen: list[Intent] = []

        boredom = drive_pressures.get("boredom", 0.0)
        if boredom > 0.0 and boredom > frustration:
            # Drive wins: pursue curiosity / engage with task
            chosen.append(Intent(kind=IntentKind.look, payload={"reason": "curiosity"}))
            # Sustained boredom escalates from looking to reaching out.
            if boredom >= self._speak_threshold:
                chosen.append(Intent(kind=IntentKind.speak, payload={"reason": "boredom"}))

        relational = drive_pressures.get("relational", 0.0)
        if relational > 0.0 and relational > frustration:
            # Drive wins: address recurring friction
            chosen.append(Intent(kind=IntentKind.speak, payload={"reason": "friction"}))

        if not chosen:
            # All drives overridden or no drives active: abandon / wait
            chosen.append(Intent(kind=IntentKind.noop, payload={}))

        return chosen
