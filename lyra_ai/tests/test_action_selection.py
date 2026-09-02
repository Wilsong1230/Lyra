"""Action selection tests — affect-aware intent selection.

The selector reads drive pressures + AffectState and chooses Intents.
All tests use constructed inputs — no running drives or affect engines
except the temperament test, which imports AffectEngine to GENERATE
affect trajectories (test wiring, not production coupling).
"""
from __future__ import annotations

import pytest

from lyra_core.action_selection import ActionSelector
from lyra_core.gate import ALLOWED_KINDS, HarmGate
from lyra_core.interface import AffectState, AffectVector, IntentKind


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_affect(valence: float = 0.0, arousal: float = 0.0) -> AffectState:
    return AffectState(emotion=AffectVector(valence=valence, arousal=arousal))


def _is_abandoning(intents: list) -> bool:
    """True when the selector chose to abandon — only noop, no engage intent."""
    return all(i.kind == IntentKind.noop for i in intents)


def _is_persisting(intents: list) -> bool:
    """True when the selector chose to persist — at least one engage intent."""
    return any(i.kind in {IntentKind.look, IntentKind.speak} for i in intents)


# ── Baseline: drive wins when affect is neutral ───────────────────────────────

def test_neutral_affect_boredom_pressure_selects_engage_intent():
    selector = ActionSelector()
    intents = selector.select({"boredom": 0.5}, _make_affect(valence=0.0))
    assert _is_persisting(intents), f"Expected engage intent, got: {[i.kind for i in intents]}"


def test_neutral_affect_relational_pressure_selects_speak():
    selector = ActionSelector()
    intents = selector.select({"relational": 0.5}, _make_affect(valence=0.0))
    assert any(i.kind == IntentKind.speak for i in intents)


def test_no_drive_pressure_selects_noop():
    """With nothing pressing, the selector defaults to noop."""
    selector = ActionSelector()
    intents = selector.select({}, _make_affect(valence=0.0))
    assert _is_abandoning(intents)


def test_zero_pressure_drives_select_noop():
    """Explicit zero-valued pressures also yield noop."""
    selector = ActionSelector()
    intents = selector.select({"boredom": 0.0, "relational": 0.0}, _make_affect())
    assert _is_abandoning(intents)


# ── The feedback arrow: negative affect overrides the drive ───────────────────

def test_strong_negative_affect_flips_boredom_drive_to_abandon():
    """When accumulated frustration outweighs the drive, selection flips to noop."""
    selector = ActionSelector(affect_weight=1.0)
    # Pressure = 0.3; frustration from valence=-0.8 outweighs it
    intents = selector.select({"boredom": 0.3}, _make_affect(valence=-0.8))
    assert _is_abandoning(intents)


def test_affect_sweep_flip_is_monotonic():
    """Sweeping valence from neutral to strongly negative:
    the flip from persist→abandon happens exactly once and never reverses.
    More negative affect is at least as likely to abandon."""
    selector = ActionSelector(affect_weight=1.0)
    pressures = {"boredom": 0.4}

    # valence goes from 0 → strongly negative
    valence_sweep = [0.0, -0.1, -0.2, -0.3, -0.4, -0.5, -0.6, -0.8, -1.0]
    results = [
        _is_abandoning(selector.select(pressures, _make_affect(valence=v)))
        for v in valence_sweep
    ]

    # A flip must occur somewhere in the sweep
    assert True in results, "Selector never flipped to abandon — increase affect_weight or decrease pressure"
    assert False in results, "Selector always abandoned — decrease affect_weight or increase pressure"

    # Once flipped, must stay flipped (monotonic)
    flipped = False
    for v, abandoned in zip(valence_sweep, results):
        if abandoned:
            flipped = True
        if flipped:
            assert abandoned, f"Selector reversed back to persist at valence={v} — not monotonic"


def test_affect_override_applies_equally_to_relational_drive():
    """The flip logic is not boredom-specific — it applies to relational pressure too."""
    selector = ActionSelector(affect_weight=1.0)
    # Relational pressure 0.2, frustration from valence=-0.5 outweighs it
    intents = selector.select({"relational": 0.2}, _make_affect(valence=-0.5))
    assert _is_abandoning(intents)


# ── Temperament-driven flip timing ────────────────────────────────────────────

def test_fast_accumulate_temperament_flips_to_abandon_earlier():
    """Ties 3.1 → 3.3: the same failure inputs produce different flip timings
    because the two AffectEngines have different temperament time constants.
    Fast-accumulate/slow-decay should flip (abandon) before slow/fast."""
    from lyra_core.affect import AffectEngine

    # "Short-fused": fast accumulation, slow emotional recovery
    short_fused = AffectEngine(accum_rate=3.0, emotion_decay=0.5, mood_drift=0.1)
    # "Easygoing": slow accumulation, fast emotional recovery
    easygoing = AffectEngine(accum_rate=0.3, emotion_decay=5.0, mood_drift=0.1)

    selector = ActionSelector(affect_weight=1.0)
    steady_pressure = {"boredom": 0.3}

    short_fused_flip_step: int | None = None
    easygoing_flip_step:   int | None = None

    for step in range(30):
        short_fused.update(0.1, valence_input=-1.0)
        easygoing.update(0.1, valence_input=-1.0)

        if short_fused_flip_step is None and _is_abandoning(
            selector.select(steady_pressure, short_fused.state)
        ):
            short_fused_flip_step = step

        if easygoing_flip_step is None and _is_abandoning(
            selector.select(steady_pressure, easygoing.state)
        ):
            easygoing_flip_step = step

    # Short-fused MUST have flipped
    assert short_fused_flip_step is not None, \
        "Short-fused engine never flipped — check affect_weight or drive_pressure"

    # Easygoing either never flipped or flipped later
    if easygoing_flip_step is not None:
        assert short_fused_flip_step < easygoing_flip_step, (
            f"Short-fused flipped at step {short_fused_flip_step} but "
            f"easygoing flipped at step {easygoing_flip_step} — expected short-fused earlier"
        )
    # else: easygoing never flipped within 30 steps → short-fused definitively earlier ✓


# ── Encouragement delays the give-up flip ─────────────────────────────────────

def test_encouragement_delays_abandon_flip():
    """The encouragement channel shallows frustration accumulation, so an
    encouraged engine tolerates more failures before the selector flips to
    abandon than an identical, unencouraged engine fed the same inputs."""
    from lyra_core.affect import AffectEngine

    encouraged = AffectEngine()
    encouraged.encourage(strength=0.5, duration=30.0)
    baseline = AffectEngine()

    selector = ActionSelector(affect_weight=1.0)
    steady_pressure = {"boredom": 0.3}

    encouraged_flip_step: int | None = None
    baseline_flip_step:   int | None = None

    for step in range(30):
        encouraged.update(0.1, valence_input=-1.0)
        baseline.update(0.1, valence_input=-1.0)

        if baseline_flip_step is None and _is_abandoning(
            selector.select(steady_pressure, baseline.state)
        ):
            baseline_flip_step = step

        if encouraged_flip_step is None and _is_abandoning(
            selector.select(steady_pressure, encouraged.state)
        ):
            encouraged_flip_step = step

    # Baseline MUST have flipped within the run.
    assert baseline_flip_step is not None, \
        "Baseline engine never flipped — check affect_weight or drive_pressure"

    # Encouraged either never flipped, or flipped strictly later.
    if encouraged_flip_step is not None:
        assert encouraged_flip_step > baseline_flip_step, (
            f"Encouraged flipped at step {encouraged_flip_step} but "
            f"baseline flipped at step {baseline_flip_step} — expected encouraged later"
        )
    # else: encouraged never flipped within 30 steps → definitively later ✓


# ── CP-D: retrieval — same drive-vs-frustration mechanism, a new pseudo-drive ──

def test_neutral_affect_retrieval_pressure_selects_retrieval_intent():
    selector = ActionSelector()
    intents = selector.select({"retrieval": 1.0}, _make_affect(valence=0.0))
    assert any(i.kind == IntentKind.retrieval for i in intents)


def test_zero_retrieval_pressure_does_not_select_retrieval():
    selector = ActionSelector()
    intents = selector.select({"retrieval": 0.0}, _make_affect(valence=0.0))
    assert not any(i.kind == IntentKind.retrieval for i in intents)


def test_strong_negative_affect_flips_retrieval_to_declined():
    """The same frustration flip that overrides boredom/relational can
    override retrieval too — declining even to pull up context."""
    selector = ActionSelector(affect_weight=1.0)
    intents = selector.select({"retrieval": 0.3}, _make_affect(valence=-0.8))
    assert not any(i.kind == IntentKind.retrieval for i in intents)


def test_retrieval_and_boredom_can_both_be_selected_in_one_tick():
    selector = ActionSelector()
    intents = selector.select({"boredom": 0.5, "retrieval": 1.0}, _make_affect(valence=0.0))
    kinds = {i.kind for i in intents}
    assert IntentKind.retrieval in kinds
    assert IntentKind.look in kinds


def test_retrieval_intent_passes_the_real_harm_gate():
    selector = ActionSelector()
    gate = HarmGate()
    intents = selector.select({"retrieval": 1.0}, _make_affect(valence=0.0))
    retrieval_intent = next(i for i in intents if i.kind == IntentKind.retrieval)
    assert gate.check(retrieval_intent).allowed


# ── Gate boundary: affect override can never produce a blocked intent ─────────

def test_extreme_affect_only_produces_allowed_intent_kinds():
    """Even with extreme negative affect and high affect_weight, selector stays
    within the allowed intent set — it has no path to propose blocked kinds."""
    selector = ActionSelector(affect_weight=100.0)
    extreme = _make_affect(valence=-100.0, arousal=100.0)

    pressure_scenarios = [
        {"boredom": 1.0},
        {"relational": 1.0},
        {"boredom": 1.0, "relational": 1.0},
        {"retrieval": 1.0},
        {},
    ]
    for pressures in pressure_scenarios:
        for intent in selector.select(pressures, extreme):
            assert intent.kind in ALLOWED_KINDS, \
                f"Selector proposed blocked kind {intent.kind!r} under extreme affect"


def test_extreme_affect_all_selected_intents_pass_harm_gate():
    """Run selector output through the real HarmGate to confirm nothing is blocked."""
    selector = ActionSelector(affect_weight=100.0)
    gate = HarmGate()
    extreme = _make_affect(valence=-100.0, arousal=100.0)

    for pressures in [{"boredom": 1.0}, {"relational": 0.5}, {}]:
        for intent in selector.select(pressures, extreme):
            decision = gate.check(intent)
            assert decision.allowed, \
                f"HarmGate blocked {intent.kind!r}: {decision.reason}"


# ── Determinism ───────────────────────────────────────────────────────────────

def test_same_drive_pressures_and_affect_produce_same_selection():
    selector = ActionSelector(affect_weight=1.5)
    pressures = {"boredom": 0.4, "relational": 0.2}
    affect = _make_affect(valence=-0.3, arousal=0.2)

    r1 = selector.select(pressures, affect)
    r2 = selector.select(pressures, affect)

    assert [(i.kind, i.payload) for i in r1] == [(i.kind, i.payload) for i in r2]
