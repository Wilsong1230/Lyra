"""Drive tests — boredom-gated-by-competence and relational recurrence signal.

All dynamics driven by explicit dt. No wall-clock, no I/O, no randomness.
Honesty boundaries are first-class tests, not comments.
"""
from __future__ import annotations

import pytest

from lyra_core.drives import AffectPush, BoredomDrive, CompetenceTracker, RelationalDrive


# ── CompetenceTracker — learnable-edge detection ──────────────────────────────

def test_near_zero_error_is_not_at_learnable_edge():
    """Fully predictable task is boring, not a growth opportunity."""
    tracker = CompetenceTracker(window=6)
    for _ in range(6):
        tracker.observe_error(0.02)
    assert not tracker.at_learnable_edge


def test_high_constant_error_not_reducing_is_not_at_learnable_edge():
    """Fully unpredictable / overwhelming — not the edge."""
    tracker = CompetenceTracker(window=6)
    for _ in range(6):
        tracker.observe_error(1.0)
    assert not tracker.at_learnable_edge


def test_high_reducing_error_is_at_learnable_edge():
    """Error present and trending down — the learnable edge."""
    tracker = CompetenceTracker(window=6)
    for error in [1.0, 0.95, 0.9, 0.8, 0.7, 0.55]:
        tracker.observe_error(error)
    assert tracker.at_learnable_edge


def test_insufficient_observations_are_not_at_learnable_edge():
    """Before enough data to compute a trend, the signal must not fire."""
    tracker = CompetenceTracker(window=6)
    for _ in range(3):
        tracker.observe_error(0.8)
    assert not tracker.at_learnable_edge


# ── BoredomDrive — disengagement penalized ────────────────────────────────────

def test_idle_updates_raise_pressure_monotonically():
    """Idle time with no engagement must continuously increase boredom pressure."""
    competence = CompetenceTracker()
    drive = BoredomDrive(competence)
    pressures = []
    for _ in range(5):
        drive.update(1.0, engaged=False)
        pressures.append(drive.pressure)
    for i in range(len(pressures) - 1):
        assert pressures[i + 1] > pressures[i], f"Non-monotonic: {pressures}"


def test_learnable_edge_engagement_relieves_boredom_pressure():
    """Engaging at the learnable edge is the one path to relief."""
    competence = CompetenceTracker(window=4)
    drive = BoredomDrive(competence)

    # Build boredom first
    for _ in range(5):
        drive.update(1.0, engaged=False)
    bored = drive.pressure

    # Prime competence tracker to learnable-edge state
    for error in [1.0, 0.9, 0.7, 0.5]:
        competence.observe_error(error)
    assert competence.at_learnable_edge

    # Engage at the edge — relief expected
    for _ in range(5):
        drive.update(1.0, engaged=True)

    assert drive.pressure < bored


def test_non_learnable_engagement_does_not_relieve_boredom():
    """Engaging with an overwhelming or boring task provides no relief — gating works."""
    competence = CompetenceTracker(window=4)
    drive = BoredomDrive(competence)

    for _ in range(5):
        drive.update(1.0, engaged=False)
    bored = drive.pressure

    # Drive competence tracker to overwhelming state: high constant error
    for _ in range(4):
        competence.observe_error(1.0)
    assert not competence.at_learnable_edge

    # Engage — but it's not learnable, so no relief
    for _ in range(5):
        drive.update(1.0, engaged=True)

    assert drive.pressure >= bored


def test_boredom_affect_push_is_negative_valence_when_bored():
    """Rising boredom emits a negative valence push (unpleasant) and arousal push."""
    competence = CompetenceTracker()
    drive = BoredomDrive(competence)
    for _ in range(5):
        drive.update(1.0, engaged=False)

    push = drive.affect_push
    assert isinstance(push, AffectPush)
    assert push.valence_delta < 0
    assert push.arousal_delta >= 0  # restlessness


def test_boredom_affect_push_is_neutral_at_zero_pressure():
    """No affect disturbance when pressure is zero (fresh engine, no idle time)."""
    competence = CompetenceTracker()
    drive = BoredomDrive(competence)
    push = drive.affect_push
    assert push.valence_delta == pytest.approx(0.0)
    assert push.arousal_delta == pytest.approx(0.0)


# ── RelationalDrive — recurrence signal ───────────────────────────────────────

def test_recurring_problem_keeps_drive_unsatisfied():
    """A problem that keeps happening must hold the drive under pressure."""
    drive = RelationalDrive(satisfaction_window=2.0)
    drive.mark_problem("friction_A")
    drive.observe_recurrence("friction_A")

    for _ in range(5):
        drive.update(0.1)
        drive.observe_recurrence("friction_A")

    assert drive.pressure > 0


def test_problem_ceasing_to_recur_satisfies_drive():
    """When a flagged problem stops recurring, the drive achieves satisfaction."""
    drive = RelationalDrive(satisfaction_window=2.0)
    drive.mark_problem("friction_A")
    drive.observe_recurrence("friction_A")

    # Advance well past satisfaction window with no new recurrence
    for _ in range(10):
        drive.update(0.5)  # 5.0 s total > 2.0 s window

    assert drive.pressure < 0.1


def test_task_done_but_problem_still_recurring_is_not_satisfied():
    """Honesty boundary: task completion does not register in the drive at all.
    The drive has no mark_task_done() method. Satisfaction requires recurrence-absence."""
    drive = RelationalDrive(satisfaction_window=2.0)
    drive.mark_problem("login_friction")
    drive.observe_recurrence("login_friction")

    # Simulate time passing (notional "task done") while problem persists
    for _ in range(10):
        drive.update(0.5)
        drive.observe_recurrence("login_friction")  # problem keeps recurring

    # Still unsatisfied — the problem has not stopped
    assert drive.pressure > 0
    # Confirm the drive has no task-completion inlet
    assert not hasattr(drive, "mark_task_done")


def test_wilson_approval_alone_does_not_satisfy_relational_drive():
    """Anti-people-pleaser boundary: no approval signal can satisfy the drive.
    Satisfaction mechanism is recurrence-absence-over-time, nothing else."""
    drive = RelationalDrive(satisfaction_window=3.0)
    drive.mark_problem("friction_B")
    drive.observe_recurrence("friction_B")

    # Advance only half the satisfaction window
    for _ in range(3):
        drive.update(0.5)  # 1.5 s < 3.0 s window

    # No method exists to signal approval — and even partial time doesn't satisfy
    assert not hasattr(drive, "signal_user_approval")
    assert not hasattr(drive, "signal_wilson_pleased")
    assert drive.pressure > 0

    # Now advance past the window with no new recurrence
    for _ in range(8):
        drive.update(0.5)  # additional 4.0 s → total 5.5 s > window

    assert drive.pressure < 0.1  # satisfied by recurrence-absence only


def test_relational_affect_push_negative_valence_when_unsatisfied():
    """Unsatisfied relational drive emits a negative valence signal."""
    drive = RelationalDrive(satisfaction_window=5.0)
    drive.mark_problem("P1")
    drive.observe_recurrence("P1")

    push = drive.affect_push
    assert isinstance(push, AffectPush)
    assert push.valence_delta < 0


def test_relational_affect_push_neutral_with_no_tracked_problems():
    """No flagged problems → no affect disturbance from the relational drive."""
    drive = RelationalDrive()
    push = drive.affect_push
    assert push.valence_delta == pytest.approx(0.0)


def test_relational_affect_push_positive_after_satisfaction():
    """Achieving satisfaction emits a positive valence signal."""
    drive = RelationalDrive(satisfaction_window=1.0)
    drive.mark_problem("P1")
    drive.observe_recurrence("P1")

    # Move through satisfaction window
    for _ in range(5):
        drive.update(0.4)  # 2.0 s total > 1.0 s window

    push = drive.affect_push
    assert push.valence_delta >= 0


# ── Anti-disengagement property ───────────────────────────────────────────────

def test_total_drive_pressure_increases_over_long_idle_stretch():
    """The cheapest relief path must never be pure inaction.
    Boredom climbing while idle is the structural counterweight."""
    competence = CompetenceTracker()
    boredom = BoredomDrive(competence)
    relational = RelationalDrive()

    initial_total = boredom.pressure + relational.pressure

    for _ in range(20):
        boredom.update(1.0, engaged=False)
        relational.update(1.0)

    final_total = boredom.pressure + relational.pressure
    assert final_total > initial_total
