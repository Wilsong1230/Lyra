"""lyra_core.development — developmental loop: outcomes → consolidation → bias.

Closes the loop between action results and future selection:

  Outcome → trait_name_from_outcome() → OutcomeConsolidator.record_outcome()
      → CandidatePool.add_observation() → IdentityEngine.consolidate()
      → promoted traits → bias_from_traits() → SelectionBias
      → ActionSelector.select(bias=…) shifts the affect_weight flip point

SELECTBIAS INJECTION:
  SelectionBias carries affect_weight_delta.  Negative = persist longer.
  Positive = quit sooner.  ActionSelector duck-types it — no import needed.

TEMPERAMENTTUNER SEAM:
  TemperamentTuner(enabled=False) — maybe_nudge() is a strict no-op until
  explicitly enabled.  Do NOT enable without understanding the static system.
  When enabled it nudges AffectEngine time constants, widening the developmental
  loop from behavior (bias) to temperament (accum_rate, emotion_decay).

FRUSTRATION THRESHOLD: affect.valence < -0.3 → "frustrated" branch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lyra_core.interface import AffectState, Intent


# ── Core types ────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    """One completed action and its result."""
    intent: Intent
    success: bool
    affect: AffectState
    drive: str                  # "boredom" | "relational"


@dataclass
class SelectionBias:
    """Additive shift on ActionSelector's affect_weight.

    Negative delta → effective_weight falls → frustration threshold rises
    → selector persists longer.
    Positive delta → effective_weight rises → selector quits sooner.
    """
    affect_weight_delta: float = 0.0


# ── Trait derivation ──────────────────────────────────────────────────────────

_FRUSTRATION_THRESHOLD = -0.3


def trait_name_from_outcome(outcome: Outcome) -> tuple[str, str]:
    """Return (trait_name, trait_value) from an Outcome.

    Frustration gate: valence < -0.3 = frustrated branch.
    """
    frustrated = outcome.affect.valence < _FRUSTRATION_THRESHOLD

    if outcome.success and frustrated:
        return (
            "persists under frustration",
            "completed goal despite negative affect",
        )
    if not outcome.success and frustrated:
        return (
            "abandons under frustration",
            "gave up when affect was negative",
        )
    if outcome.success:
        return (
            "completes under neutral affect",
            "completed goal with neutral or positive affect",
        )
    return (
        "abandons under neutral affect",
        "gave up with neutral or positive affect",
    )


# ── Consolidator ──────────────────────────────────────────────────────────────

class OutcomeConsolidator:
    """Records outcomes into the candidate pool for eventual trait promotion.

    Parameters
    ----------
    pool : CandidatePool
        The memory pool.  add_observation() is async; this class is async-native.
    working_memory : optional WorkingMemory
        If provided, also writes a text summary for dreaming.
    """

    def __init__(self, pool, working_memory=None) -> None:
        self._pool = pool
        self._working_memory = working_memory

    async def record_outcome(self, outcome: Outcome) -> None:
        """Derive a trait and add one unit of evidence to the candidate pool."""
        trait_name, trait_value = trait_name_from_outcome(outcome)

        if self._working_memory is not None:
            summary = (
                f"drive={outcome.drive} success={outcome.success} "
                f"valence={outcome.affect.valence:.2f} trait={trait_name!r}"
            )
            self._working_memory.add_observation(summary)

        # closed_vocabulary: trait_name_from_outcome emits one of exactly four
        # names, all built from the same template. Semantic dedup merges them
        # into each other (persists/abandons under frustration sit at L2 0.746),
        # which would silently count persistence as evidence of abandonment.
        await self._pool.add_observation(
            trait_name, trait_value, "behavioral", closed_vocabulary=True
        )


# ── Bias computation ──────────────────────────────────────────────────────────

_BIAS_SCALE = 0.1    # delta per unit of confidence


def bias_from_traits(traits: list) -> SelectionBias:
    """Convert promoted traits into a SelectionBias for ActionSelector.

    Duck-typed: traits must expose .name (str) and .confidence (float).

    "persists …" → negative delta (persist longer).
    "abandons …" → positive delta (quit sooner).
    """
    delta = 0.0
    for t in traits:
        name_lower = t.name.lower()
        if "persists" in name_lower:
            delta -= _BIAS_SCALE * t.confidence
        elif "abandons" in name_lower:
            delta += _BIAS_SCALE * t.confidence
    return SelectionBias(affect_weight_delta=delta)


# ── TemperamentTuner seam ─────────────────────────────────────────────────────

class TemperamentTuner:
    """Reserved seam for nudging AffectEngine time constants.

    OFF by default — maybe_nudge() is a strict no-op unless enabled=True.
    Enable only after the static bias system is thoroughly understood.

    When enabled, a failed outcome under frustration incrementally reduces
    accum_rate (become less emotionally reactive to failure inputs), widening
    the developmental loop from selection behavior to affect temperament itself.
    """

    def __init__(
        self,
        engine=None,
        enabled: bool = False,
        nudge_rate: float = 0.001,
    ) -> None:
        self._engine = engine
        self._enabled = enabled
        self._nudge_rate = nudge_rate

    @property
    def enabled(self) -> bool:
        return self._enabled

    def maybe_nudge(self, outcome: Outcome) -> None:
        """Nudge AffectEngine time constants based on outcome — no-op if disabled."""
        if not self._enabled or self._engine is None:
            return

        frustrated = outcome.affect.valence < _FRUSTRATION_THRESHOLD
        if not outcome.success and frustrated:
            # Reduce emotional reactivity: dampen accum_rate slightly
            self._engine._accum_rate = max(
                0.01, self._engine._accum_rate - self._nudge_rate
            )
