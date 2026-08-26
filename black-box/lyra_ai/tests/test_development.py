"""Phase 3.6 — developmental loop: outcomes → consolidation → selection bias.

Tests are grouped into four concerns:
  1. OutcomeConsolidator: a relieved drive produces a candidate in the pool.
  2. Promotion: repeated similar outcomes accrue evidence and promote via
     the existing 5/15/50 machinery (proves the new material feeds the engine).
  3. Trait-biased selection: persistence-leaning bias shifts the flip point.
  4. Full-circle failures(n): failures → frustration → flip → outcome → candidate.
  5. TemperamentTuner seam is OFF by default.

All async tests use aiosqlite via lyra_memory.db.init_db().
asyncio_mode = "auto" is set in pyproject.toml; no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import pytest
import aiosqlite

from lyra_core.action_selection import ActionSelector
from lyra_core.affect import AffectEngine
from lyra_core.development import (
    Outcome,
    OutcomeConsolidator,
    SelectionBias,
    TemperamentTuner,
    bias_from_traits,
    trait_name_from_outcome,
)
from lyra_core.interface import AffectState, AffectVector, Intent, IntentKind
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.db import init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "test_lyra.db"
    conn = await init_db(path)
    yield conn
    await conn.close()


@pytest.fixture
async def pool(db):
    return CandidatePool(db)


def _make_affect(valence: float = 0.0, arousal: float = 0.0) -> AffectState:
    return AffectState(emotion=AffectVector(valence=valence, arousal=arousal))


def _look_intent() -> Intent:
    return Intent(kind=IntentKind.look, payload={"reason": "curiosity"})


def _speak_intent() -> Intent:
    return Intent(kind=IntentKind.speak, payload={"reason": "friction"})


# ── 1. OutcomeConsolidator: relieved drive → candidate added ─────────────────

async def test_successful_boredom_outcome_adds_candidate(pool):
    """A relieved boredom drive (success=True) must add evidence to the pool."""
    consolidator = OutcomeConsolidator(pool)
    outcome = Outcome(
        intent=_look_intent(),
        success=True,
        affect=_make_affect(valence=-0.1),
        drive="boredom",
    )
    await consolidator.record_outcome(outcome)

    rows = await pool._conn.execute_fetchall(
        "SELECT trait_name FROM candidates ORDER BY rowid DESC LIMIT 1"
    )
    assert rows, "No candidate was written — record_outcome must call pool.add_observation()"


async def test_failed_boredom_outcome_also_records_candidate(pool):
    """Failed outcomes are still informative — they must be recorded."""
    consolidator = OutcomeConsolidator(pool)
    outcome = Outcome(
        intent=_look_intent(),
        success=False,
        affect=_make_affect(valence=-0.6),
        drive="boredom",
    )
    await consolidator.record_outcome(outcome)

    rows = await pool._conn.execute_fetchall("SELECT trait_name FROM candidates")
    assert rows


async def test_outcome_trait_name_reflects_success_and_frustration(pool):
    """The derived trait name must differ between success-under-frustration
    and failure-under-frustration — they are different developmental signals."""
    consolidator = OutcomeConsolidator(pool)

    frustrated_affect = _make_affect(valence=-0.5)

    success_outcome = Outcome(
        intent=_look_intent(), success=True,
        affect=frustrated_affect, drive="boredom",
    )
    failure_outcome = Outcome(
        intent=_look_intent(), success=False,
        affect=frustrated_affect, drive="boredom",
    )

    success_name, _ = trait_name_from_outcome(success_outcome)
    failure_name, _ = trait_name_from_outcome(failure_outcome)

    assert success_name != failure_name, (
        f"Success and failure under frustration must yield different trait names; "
        f"both returned {success_name!r}"
    )


# ── 2. Promotion: repeated outcomes → trait surfaces at threshold ─────────────

async def test_repeated_similar_outcomes_promote_to_surface_tier(pool):
    """Five observations of the same trait must result in a promoted trait
    (surface threshold = 5 in lyra_memory.config.TRAIT_THRESHOLDS)."""
    from lyra_memory.identity_engine import IdentityEngine
    from lyra_memory.config import TRAIT_THRESHOLDS

    consolidator = OutcomeConsolidator(pool)
    engine = IdentityEngine(pool._conn, pool)

    frustrated = _make_affect(valence=-0.5)
    outcome = Outcome(
        intent=_look_intent(), success=True,
        affect=frustrated, drive="boredom",
    )

    surface_threshold = TRAIT_THRESHOLDS["surface"]

    for _ in range(surface_threshold):
        await consolidator.record_outcome(outcome)

    await engine.consolidate()

    traits = await engine.get_top_traits()
    assert traits, (
        f"After {surface_threshold} identical outcomes + consolidate(), "
        f"at least one trait must be promoted"
    )


# ── 3. Trait-biased selection: persistence-leaning bias shifts flip point ─────

def test_persistence_bias_shifts_flip_point_toward_later():
    """A SelectionBias with negative affect_weight_delta (persist longer) must
    cause the selector to persist at a frustration level that would otherwise
    flip to abandon."""
    selector = ActionSelector(affect_weight=1.0)
    pressures = {"boredom": 0.4}
    # At valence=-0.5, frustration = 0.5, drive = 0.4 → normally abandons
    affect = _make_affect(valence=-0.5)

    no_bias_intents = selector.select(pressures, affect)
    assert all(i.kind == IntentKind.noop for i in no_bias_intents), \
        "Without bias the selector should abandon here (test setup)"

    # Bias: negative delta → effective_weight = 1.0 + (-0.7) = 0.3
    # → frustration = 0.5 * 0.3 = 0.15 < 0.4 → persists
    persist_bias = SelectionBias(affect_weight_delta=-0.7)
    biased_intents = selector.select(pressures, affect, bias=persist_bias)
    assert any(i.kind != IntentKind.noop for i in biased_intents), \
        "With persistence bias the selector should persist, not abandon"


def test_quit_bias_shifts_flip_point_toward_earlier():
    """A SelectionBias with positive affect_weight_delta (quit easier) must
    flip to abandon at a frustration level that would otherwise persist."""
    selector = ActionSelector(affect_weight=1.0)
    pressures = {"boredom": 0.6}
    # At valence=-0.4, frustration = 0.4 < 0.6 → normally persists
    affect = _make_affect(valence=-0.4)

    no_bias_intents = selector.select(pressures, affect)
    assert any(i.kind != IntentKind.noop for i in no_bias_intents), \
        "Without bias the selector should persist here (test setup)"

    # Bias: positive delta → effective_weight = 1.0 + 0.6 = 1.6
    # → frustration = 0.4 * 1.6 = 0.64 > 0.6 → abandons
    quit_bias = SelectionBias(affect_weight_delta=0.6)
    biased_intents = selector.select(pressures, affect, bias=quit_bias)
    assert all(i.kind == IntentKind.noop for i in biased_intents), \
        "With quit bias the selector should abandon, not persist"


def test_none_bias_is_equivalent_to_no_bias():
    """Passing bias=None must produce identical output to the default (no bias arg)."""
    selector = ActionSelector(affect_weight=1.0)
    pressures = {"boredom": 0.5}
    affect = _make_affect(valence=-0.3)

    r1 = selector.select(pressures, affect)
    r2 = selector.select(pressures, affect, bias=None)
    assert [(i.kind, i.payload) for i in r1] == [(i.kind, i.payload) for i in r2]


def test_bias_from_traits_persistence_trait_gives_negative_delta():
    """A high-confidence 'persists under frustration' trait must produce a
    SelectionBias with a negative affect_weight_delta."""
    class _FakeTrait:
        def __init__(self, name, confidence):
            self.name = name
            self.confidence = confidence

    traits = [_FakeTrait("persists under frustration", 0.8)]
    bias = bias_from_traits(traits)
    assert isinstance(bias, SelectionBias)
    assert bias.affect_weight_delta < 0, \
        f"Persistence trait must push delta negative; got {bias.affect_weight_delta}"


def test_bias_from_traits_abandon_trait_gives_positive_delta():
    """A high-confidence 'abandons under frustration' trait must produce a
    SelectionBias with a positive affect_weight_delta."""
    class _FakeTrait:
        def __init__(self, name, confidence):
            self.name = name
            self.confidence = confidence

    traits = [_FakeTrait("abandons under frustration", 0.8)]
    bias = bias_from_traits(traits)
    assert bias.affect_weight_delta > 0


def test_bias_from_empty_traits_is_zero():
    """No traits → zero delta (no nudge)."""
    bias = bias_from_traits([])
    assert bias.affect_weight_delta == pytest.approx(0.0)


# ── 4. Full-circle failures(n): the complete developmental loop ───────────────

async def test_failures_full_circle_produces_candidate(pool):
    """Simulates the full developmental loop end-to-end:

    1. Repeated failure inputs → AffectEngine accumulates negative valence.
    2. ActionSelector flips to noop (abandon).
    3. That noop outcome is recorded by OutcomeConsolidator.
    4. The pool contains a candidate derived from the abandonment.

    This proves the cycle actually closes — no step is bypassed.
    """
    affect_engine = AffectEngine(accum_rate=3.0, emotion_decay=0.5, mood_drift=0.1)
    selector = ActionSelector(affect_weight=1.0)
    consolidator = OutcomeConsolidator(pool)

    pressures = {"boredom": 0.3}
    flip_step: int | None = None
    candidate_written = False

    for step in range(30):
        affect_engine.update(0.1, valence_input=-1.0)
        state = affect_engine.state

        intents = selector.select(pressures, state)
        chosen = intents[0]

        if chosen.kind == IntentKind.noop and flip_step is None:
            flip_step = step
            # Record the abandonment outcome
            outcome = Outcome(
                intent=chosen,
                success=False,
                affect=state,
                drive="boredom",
            )
            await consolidator.record_outcome(outcome)
            candidate_written = True
            break

    assert flip_step is not None, \
        "Selector never flipped — check accum_rate or affect_weight (test setup)"
    assert candidate_written, \
        "Flip occurred but record_outcome was not called (test logic bug)"

    rows = await pool._conn.execute_fetchall("SELECT trait_name FROM candidates")
    assert rows, \
        "Full circle failed: selector flipped and record_outcome was called, " \
        "but no candidate appears in the pool"


# ── 5. TemperamentTuner seam is OFF by default ────────────────────────────────

def test_temperament_tuner_off_by_default():
    """TemperamentTuner must expose enabled=False as the default."""
    tuner = TemperamentTuner()
    assert not tuner.enabled


def test_temperament_tuner_maybe_nudge_does_nothing_when_disabled():
    """maybe_nudge() on a disabled tuner must not change the AffectEngine's
    time constants — no accum_rate, emotion_decay, or mood_drift mutation."""
    engine = AffectEngine(accum_rate=1.0, emotion_decay=2.0, mood_drift=0.2)
    tuner = TemperamentTuner(engine=engine, enabled=False)

    before = (engine._accum_rate, engine._emotion_decay, engine._mood_drift)

    outcome = Outcome(
        intent=_look_intent(), success=False,
        affect=_make_affect(valence=-0.8), drive="boredom",
    )
    tuner.maybe_nudge(outcome)

    after = (engine._accum_rate, engine._emotion_decay, engine._mood_drift)
    assert before == after, \
        f"Disabled tuner must not mutate time constants; before={before}, after={after}"


def test_temperament_tuner_maybe_nudge_does_nothing_without_engine():
    """maybe_nudge() with no engine attached must be a safe no-op."""
    tuner = TemperamentTuner(enabled=False)
    outcome = Outcome(
        intent=_look_intent(), success=False,
        affect=_make_affect(valence=-0.8), drive="boredom",
    )
    tuner.maybe_nudge(outcome)  # must not raise


def test_temperament_tuner_enabled_mutates_time_constants():
    """When explicitly enabled, maybe_nudge() MUST change at least one time constant.
    This confirms the seam does something when turned on — not just more no-op."""
    engine = AffectEngine(accum_rate=1.0, emotion_decay=2.0, mood_drift=0.2)
    tuner = TemperamentTuner(engine=engine, enabled=True, nudge_rate=0.1)

    before = (engine._accum_rate, engine._emotion_decay, engine._mood_drift)

    outcome = Outcome(
        intent=_look_intent(), success=False,
        affect=_make_affect(valence=-0.8), drive="boredom",
    )
    tuner.maybe_nudge(outcome)

    after = (engine._accum_rate, engine._emotion_decay, engine._mood_drift)
    assert before != after, \
        "Enabled tuner must mutate at least one time constant after maybe_nudge()"
