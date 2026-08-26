"""lyra_core.drives — boredom-gated-by-competence and relational drives.

Two drives, each emitting pressure and an AffectPush that the affect engine
(Phase 3.6) will consume.  Nothing reads drives yet; Phase 3.3 wires them.

BoredomDrive: self-directed.  Idle time → pressure rises.  Relief comes only
  from engagement at the learnable edge (competence-increasing activity).  A
  random non-learning action provides zero relief — the gating is explicit.

RelationalDrive: recurrence signal.  Tracks flagged recurring problems.
  Satisfaction requires a problem to CEASE RECURRING over time — not task
  completion, not user approval.  No approval inlet exists by design.

CompetenceTracker: proxy substrate for BoredomDrive.  Tracks recent prediction
  errors and exposes `at_learnable_edge` — True when error is high but reducing.
  This is a first-pass proxy (documented as such); the seam exists to replace
  with a richer signal later.

  Reserved seam: curiosity_weight (currently flat = 1.0) is the hook where
  relational weight and consolidated interests will modulate which uncertainties
  are worth reducing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


# ── AffectPush ────────────────────────────────────────────────────────────────

@dataclass
class AffectPush:
    """Signed affect delta that a drive emits each tick.

    Phase 3.6 feeds this into AffectEngine.update().
    Positive valence_delta = pleasant; negative = unpleasant.
    """
    valence_delta: float = 0.0
    arousal_delta: float = 0.0


# ── CompetenceTracker ─────────────────────────────────────────────────────────

_BORING_THRESHOLD = 0.1    # errors below this → boring (nothing to learn)
_MIN_IMPROVEMENT  = 0.1    # required drop from earlier→recent to count as reduction


class CompetenceTracker:
    """First-pass proxy for the prediction-error learnable-edge signal.

    Observations are raw error magnitudes (float).  The predicted/actual strings
    on Observation are Phase 0 placeholders; callers compute magnitude before
    passing here.

    at_learnable_edge == True when:
      1. Enough observations have accumulated (>= window).
      2. Earlier errors were significant (mean >= BORING_THRESHOLD).
      3. Recent errors are meaningfully lower (reduced by >= MIN_IMPROVEMENT).

    Seam: curiosity_weight (default 1.0) reserved for relational / interest
    modulation.  Flat now; Phase ? fills it in.
    """

    def __init__(self, window: int = 6, curiosity_weight: float = 1.0) -> None:
        if window < 2 or window % 2 != 0:
            raise ValueError("window must be an even integer >= 2")
        self._window = window
        self._curiosity_weight = curiosity_weight   # seam — unused until Phase ?
        self._errors: deque[float] = deque(maxlen=window)

    def observe_error(self, error: float) -> None:
        self._errors.append(error)

    @property
    def at_learnable_edge(self) -> bool:
        if len(self._errors) < self._window:
            return False
        errors = list(self._errors)
        half = self._window // 2
        earlier_mean = sum(errors[:half]) / half
        recent_mean  = sum(errors[half:]) / half
        return (
            earlier_mean >= _BORING_THRESHOLD
            and recent_mean < earlier_mean - _MIN_IMPROVEMENT
        )


# ── BoredomDrive ──────────────────────────────────────────────────────────────

_BOREDOM_VALENCE_SCALE = 0.2
_BOREDOM_AROUSAL_SCALE = 0.1


class BoredomDrive:
    """Self-directed drive.  Idle time inflates pressure; only learnable-edge
    engagement deflates it.  Disengagement is never a refuge.
    """

    def __init__(
        self,
        competence: CompetenceTracker,
        idle_rate: float = 0.1,
        relief_rate: float = 0.5,
    ) -> None:
        self._competence = competence
        self._idle_rate = idle_rate
        self._relief_rate = relief_rate
        self._pressure: float = 0.0

    def update(self, dt: float, engaged: bool = False) -> None:
        """Advance by dt.  Relief only when engaged at the learnable edge."""
        if engaged and self._competence.at_learnable_edge:
            self._pressure = max(0.0, self._pressure - self._relief_rate * dt)
        else:
            self._pressure += self._idle_rate * dt

    @property
    def pressure(self) -> float:
        return self._pressure

    @property
    def affect_push(self) -> AffectPush:
        return AffectPush(
            valence_delta=-self._pressure * _BOREDOM_VALENCE_SCALE,
            arousal_delta=self._pressure  * _BOREDOM_AROUSAL_SCALE,
        )


# ── RelationalDrive ───────────────────────────────────────────────────────────

_RELATIONAL_VALENCE_SCALE = 0.2
_SATISFACTION_BONUS = 0.05   # brief positive push when a problem has resolved


class RelationalDrive:
    """Recurrence-signal drive.

    Satisfaction = flagged problem ceasing to recur over time.
    NOT task completion.  NOT user approval.  No such inlets exist — that
    absence is load-bearing (anti-people-pleaser boundary).

    mark_problem() is the injectable source seam; lyra-memory will feed it in
    Phase 3.6.  observe_recurrence() is called when the problem is seen again.
    """

    def __init__(self, satisfaction_window: float = 5.0) -> None:
        self._satisfaction_window = satisfaction_window
        self._elapsed: float = 0.0
        # problem_id → elapsed time when it last recurred
        self._last_recurrence: dict[str, float] = {}

    def mark_problem(self, problem_id: str) -> None:
        """Flag a problem as worth tracking.  No pressure until it recurs."""
        # The injectable source seam.  Tracking begins on first recurrence.

    def observe_recurrence(self, problem_id: str) -> None:
        """Record that this problem recurred at the current elapsed time."""
        self._last_recurrence[problem_id] = self._elapsed

    def update(self, dt: float) -> None:
        """Advance internal clock.  Pressure decays as time passes without recurrence."""
        self._elapsed += dt

    @property
    def pressure(self) -> float:
        """Mean problem pressure across all observed recurring problems.

        Individual problem pressure = 1 - clamp(time_since_recurrence / window, 0, 1).
        Zero when all problems have stopped recurring for at least satisfaction_window.
        """
        if not self._last_recurrence:
            return 0.0
        total = sum(
            max(0.0, 1.0 - (self._elapsed - t) / self._satisfaction_window)
            for t in self._last_recurrence.values()
        )
        return total / len(self._last_recurrence)

    @property
    def affect_push(self) -> AffectPush:
        """Negative while unsatisfied; small positive on satisfaction; zero if no problems."""
        if not self._last_recurrence:
            return AffectPush()
        p = self.pressure
        if p > 0:
            return AffectPush(valence_delta=-p * _RELATIONAL_VALENCE_SCALE)
        return AffectPush(valence_delta=_SATISFACTION_BONUS)
