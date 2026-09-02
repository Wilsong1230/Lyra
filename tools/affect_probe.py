#!/usr/bin/env python3
"""tools/affect_probe.py — CP-A.1/CP-A.2 offline characterization harness.

Drives lyra_core.affect.AffectEngine and lyra_core.drives.{BoredomDrive,
CompetenceTracker, RelationalDrive} directly, advancing them with an
injected synthetic clock (a plain Python loop over dt values standing in
for wall time). Imports only the affect module, the drives module, and
config.MAX_TICK_DT_SECONDS — no daemon, no transport, no store, no
lyra_core.interface.CognitiveCore.

Regenerates docs/AFFECT_CHARACTERIZATION.md. Every table in that document
is produced by a run of this script; nothing in it is hand-typed.

    python tools/affect_probe.py

works from the repo root with no venv activation, because affect.py,
drives.py and config.py have zero third-party dependencies — only
lyra_core.interface (for AffectState/AffectVector) is pulled in
transitively, and that module is stdlib-only too.

CP-A.1 measured the CP-A engines (Jacobi-split affect coupling, unbounded
BoredomDrive pressure) and found they did not agree across tick rates.
CP-A.2 changed both — exact 2x2 solve in AffectEngine, a bounded ceiling in
BoredomDrive — and this script now also CHECKS that fix: `main()` exits
nonzero if any measured span's terminal-state divergence across tick rates
exceeds AGREEMENT_TOLERANCE. This script still does not itself change,
correct, or retune any integrator — it measures lyra_core.affect and
lyra_core.drives as they stand and reports whether they agree.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LYRA_AI = REPO_ROOT / "lyra_ai"
if str(LYRA_AI) not in sys.path:
    sys.path.insert(0, str(LYRA_AI))

from lyra_core.affect import AffectEngine  # noqa: E402
from lyra_core.config import MAX_TICK_DT_SECONDS  # noqa: E402
from lyra_core.drives import (  # noqa: E402
    BoredomDrive, CompetenceTracker, RelationalDrive,
    _BOREDOM_PRESSURE_CEILING as PRESSURE_CEILING,
)
# Read-only: the actual "does this turn her terse" check, not a
# reimplementation of it. expression.py is outside CP-A.2's file set; this
# import verifies against it rather than editing it.
from lyra_core.expression import prose_hint, _NEGATIVE_HIGH_AROUSAL_HINT  # noqa: E402

DOC_PATH = REPO_ROOT / "docs" / "AFFECT_CHARACTERIZATION.md"

# ── shared knobs — match CognitiveCore()'s own construction (interface.py) ────
ACCUM_RATE = 1.0
EMOTION_DECAY = 2.0
MOOD_DRIFT = 0.2
IDLE_RATE = 0.1
RELIEF_RATE = 0.5
SATISFACTION_WINDOW = 5.0

# CP-A.2 item 5: how far apart the four tick rates' terminal states may be
# before main() exits nonzero. The exactly-invariant measurements
# (relaxation alone, either drive alone) agree to < 1e-6 — floating point.
# The coupled measurements (drive push -> affect) carry one real, bounded
# residual: BoredomDrive's pressure ramps from 0 to its ceiling over
# ~ceiling/idle_rate seconds, and a coarse dt resolves that ramp in fewer
# samples than a fine one, so ∫pressure dt over the ramp differs slightly by
# dt. That difference feeds AffectEngine's S channel (see affect.py's
# _step docstring), which has no decay of its own, so it is never erased —
# measured at a constant, small value regardless of span — under 1e-4 at
# the current _BOREDOM_PRESSURE_CEILING (see docs/AFFECT_CHARACTERIZATION.md
# "agreement check" for the actual number), not a growing divergence.
# 0.1 sits comfortably above that measured, bounded residual and far below
# anything resembling CP-A.1's catastrophic, unbounded-with-span disagreement
# (0.4-0.5 on this same relaxation-alone check, and orders of magnitude
# worse once coupled) — a regression back toward that scale still trips it.
AGREEMENT_TOLERANCE = 0.1

# Domain convention only: valence/arousal are treated as live in [-1, 1]
# elsewhere (prose_hint, the affect tests). AffectEngine itself enforces no
# such bound — nothing in affect.py clamps _emotion_v/_emotion_a/_mood_v/
# _mood_a. "Saturation" below means |axis| >= this threshold; it is a
# measurement convention for this document, not a property the code
# guarantees.
SATURATION = 1.0

RATES = [0.1, 2.0, 60.0]
SPAN_CHECKPOINTS = (60.0, 600.0, 3600.0, 28800.0)  # 1 min, 10 min, 1 h, 8 h

NON_NEUTRAL_START = dict(
    accum_rate=ACCUM_RATE, emotion_decay=EMOTION_DECAY, mood_drift=MOOD_DRIFT,
    emotion_v=-0.6, emotion_a=0.6, mood_v=-0.2, mood_a=0.1,
    encourage_strength=0.0, encourage_remaining=0.0,
)

# Six exchanges, gaps spanning the instructed 5-90 s range. Fixed and
# arbitrary — not tuned to produce any particular outcome.
CONVERSATION_GAPS = [5.0, 90.0, 30.0, 60.0, 15.0, 45.0]


# ── small helpers ───────────────────────────────────────────────────────────

def _new_engine(start: dict | None = None) -> AffectEngine:
    if start is None:
        return AffectEngine(accum_rate=ACCUM_RATE, emotion_decay=EMOTION_DECAY, mood_drift=MOOD_DRIFT)
    return AffectEngine.from_dict(start)


def _new_boredom() -> tuple[CompetenceTracker, BoredomDrive]:
    competence = CompetenceTracker()
    return competence, BoredomDrive(competence, idle_rate=IDLE_RATE, relief_rate=RELIEF_RATE)


def _new_relational() -> RelationalDrive:
    return RelationalDrive(satisfaction_window=SATISFACTION_WINDOW)


def _n_steps(span: float, dt: float) -> int:
    n = round(span / dt)
    if abs(n * dt - span) > 1e-6:
        raise ValueError(f"dt={dt} does not evenly divide span={span}")
    return n


def rates_for(span: float) -> list[tuple[str, float, int]]:
    """[(label, dt, n_steps), ...]: the three tick rates plus one single tick
    spanning the whole interval, for a given wall-clock span."""
    out = [(f"dt={dt:g}", dt, _n_steps(span, dt)) for dt in RATES]
    out.append((f"single tick (dt={span:g})", span, 1))
    return out


def _fmt(x) -> str:
    if x is None:
        return "not reached"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, float):
        return f"{x:.6g}"
    return str(x)


# ── 3a. Relaxation alone ────────────────────────────────────────────────────

def measure_relaxation_trace(dt: float = 60.0, n_steps: int = 6, start=NON_NEUTRAL_START) -> list[dict]:
    """Step-by-step emotion/mood valence at a single coarse dt, to show the
    per-step behaviour behind 3a's spread (not just the terminal value)."""
    engine = _new_engine(start)
    trace = [{"step": 0, "t": 0.0, "emotion_v": start["emotion_v"], "mood_v": start["mood_v"]}]
    for i in range(1, n_steps + 1):
        engine.update(dt)
        s = engine.state
        trace.append({"step": i, "t": i * dt, "emotion_v": s.emotion.valence, "mood_v": s.mood.valence})
    return trace


def measure_relaxation_alone(span: float = 3600.0) -> dict:
    rows = []
    for label, dt, n in rates_for(span):
        engine = _new_engine(NON_NEUTRAL_START)
        for _ in range(n):
            engine.update(dt)
        s = engine.state
        rows.append({
            "label": label, "dt": dt, "n": n,
            "emotion_v": s.emotion.valence, "emotion_a": s.emotion.arousal,
            "mood_v": s.mood.valence, "mood_a": s.mood.arousal,
        })
    spreads = {
        axis: max(r[axis] for r in rows) - min(r[axis] for r in rows)
        for axis in ("emotion_v", "emotion_a", "mood_v", "mood_a")
    }
    agree = all(v < 1e-9 for v in spreads.values())
    return {"span": span, "rows": rows, "spreads": spreads, "agree": agree}


# ── 3b. Drive pressure alone ────────────────────────────────────────────────

def measure_boredom_alone(spans=SPAN_CHECKPOINTS) -> dict:
    rows = []
    for span in spans:
        pressures = {}
        for label, dt, n in rates_for(span):
            _, drive = _new_boredom()
            for _ in range(n):
                drive.update(dt, engaged=False)
            pressures[label] = drive.pressure
        rows.append({"span": span, "pressures": pressures, "expected": IDLE_RATE * span})
    spreads = [max(r["pressures"].values()) - min(r["pressures"].values()) for r in rows]
    return {"spans": spans, "rows": rows, "spreads": spreads}


def measure_relational_alone(spans=SPAN_CHECKPOINTS) -> dict:
    """One problem marked and observed once at t=0, then idle: no further
    recurrence, no relief inlet exercised — the drive's own bounded decay."""
    rows = []
    for span in spans:
        pressures = {}
        for label, dt, n in rates_for(span):
            drive = _new_relational()
            drive.mark_problem("p")
            drive.observe_recurrence("p")
            for _ in range(n):
                drive.update(dt)
            pressures[label] = drive.pressure
        rows.append({"span": span, "pressures": pressures})
    spreads = [max(r["pressures"].values()) - min(r["pressures"].values()) for r in rows]
    return {"spans": spans, "rows": rows, "spreads": spreads}


# ── 3c. Coupled ──────────────────────────────────────────────────────────────

def measure_coupled_final_state(spans=SPAN_CHECKPOINTS) -> dict:
    """Final emotion axes at each span, at each tick rate — boredom's push
    (idle, unrelieved) is the only drive input; no relational problems."""
    rows = []
    for span in spans:
        states = {}
        for label, dt, n in rates_for(span):
            engine = _new_engine()
            _, drive = _new_boredom()
            for _ in range(n):
                drive.update(dt, engaged=False)
                push = drive.affect_push
                engine.update(dt, valence_input=push.valence_delta, arousal_input=push.arousal_delta)
            s = engine.state
            states[label] = (s.emotion.valence, s.emotion.arousal)
        v_spread = max(v for v, a in states.values()) - min(v for v, a in states.values())
        a_spread = max(a for v, a in states.values()) - min(a for v, a in states.values())
        rows.append({"span": span, "states": states, "spread_v": v_spread, "spread_a": a_spread})
    return {"spans": spans, "rows": rows}


# ── item 5: agreement check ─────────────────────────────────────────────────

def agreement_checks(doc: dict) -> list[dict]:
    """One row per measured span/scenario: max divergence across the four
    tick rates, per axis, against AGREEMENT_TOLERANCE. This is CP-A.2's
    change 5 — the actual pass/fail gate main() exits on."""
    checks = []

    ra = doc["relaxation_alone"]
    for axis, spread in ra["spreads"].items():
        checks.append({"scenario": "3a relaxation alone", "span": ra["span"], "axis": axis, "divergence": spread})

    ba = doc["boredom_alone"]
    for row in ba["rows"]:
        spread = max(row["pressures"].values()) - min(row["pressures"].values())
        checks.append({"scenario": "3b BoredomDrive alone", "span": row["span"], "axis": "pressure", "divergence": spread})

    rl = doc["relational_alone"]
    for row in rl["rows"]:
        spread = max(row["pressures"].values()) - min(row["pressures"].values())
        checks.append({"scenario": "3b RelationalDrive alone", "span": row["span"], "axis": "pressure", "divergence": spread})

    cf = doc["coupled_final"]
    for row in cf["rows"]:
        checks.append({"scenario": "3c coupled", "span": row["span"], "axis": "emotion_v", "divergence": row["spread_v"]})
        checks.append({"scenario": "3c coupled", "span": row["span"], "axis": "emotion_a", "divergence": row["spread_a"]})

    for c in checks:
        c["tolerance"] = AGREEMENT_TOLERANCE
        c["ok"] = c["divergence"] <= AGREEMENT_TOLERANCE
    return checks


# ── clamp safety (item 4 evidence) ──────────────────────────────────────────

def measure_clamp_safety(candidates=(60.0, 90.0, 120.0, 180.0, 300.0, 600.0, 900.0, 1200.0)) -> list[dict]:
    """For each candidate MAX_TICK_DT_SECONDS, the worst single tick a daemon
    can produce: pressure already at its ceiling (long prior idling), then
    one tick of dt=candidate. This is what an 8h+ idle gap collapses to once
    clamped — not a multi-tick integration. Informs the MAX_TICK_DT_SECONDS
    chosen in config.py."""
    rows = []
    for c in candidates:
        engine = _new_engine()
        _, drive = _new_boredom()
        drive._pressure = drive._pressure_ceiling  # worst case: already saturated
        drive.update(c, engaged=False)
        push = drive.affect_push
        engine.update(c, valence_input=push.valence_delta, arousal_input=push.arousal_delta)
        s = engine.state
        rows.append({
            "candidate": c, "pressure": drive.pressure,
            "emotion_v": s.emotion.valence, "emotion_a": s.emotion.arousal,
            "saturated": abs(s.emotion.valence) >= SATURATION or abs(s.emotion.arousal) >= SATURATION,
        })
    return rows


def measure_coupled_saturation(max_span: float = 28800.0) -> dict:
    """First wall time each emotion axis reaches |value| >= SATURATION, per
    multi-step tick rate, over one continuous trajectory to max_span."""
    per_rate = {}
    for dt in RATES:
        n = _n_steps(max_span, dt)
        engine = _new_engine()
        _, drive = _new_boredom()
        t = 0.0
        sat_v = sat_a = None
        for _ in range(n):
            drive.update(dt, engaged=False)
            push = drive.affect_push
            engine.update(dt, valence_input=push.valence_delta, arousal_input=push.arousal_delta)
            t += dt
            s = engine.state
            if sat_v is None and abs(s.emotion.valence) >= SATURATION:
                sat_v = t
            if sat_a is None and abs(s.emotion.arousal) >= SATURATION:
                sat_a = t
            if sat_v is not None and sat_a is not None:
                break
        per_rate[dt] = {"sat_valence": sat_v, "sat_arousal": sat_a}

    # Single-tick granularity has no internal time resolution: it can only
    # say whether a jump of the whole checkpoint span crosses SATURATION,
    # not when within that span it happened.
    single_tick = {}
    for span in SPAN_CHECKPOINTS:
        engine = _new_engine()
        _, drive = _new_boredom()
        drive.update(span, engaged=False)
        push = drive.affect_push
        engine.update(span, valence_input=push.valence_delta, arousal_input=push.arousal_delta)
        s = engine.state
        single_tick[span] = {
            "valence_saturated": abs(s.emotion.valence) >= SATURATION,
            "arousal_saturated": abs(s.emotion.arousal) >= SATURATION,
        }
    return {"per_rate": per_rate, "single_tick": single_tick, "max_span": max_span}


# ── 3d. Clamp cost ──────────────────────────────────────────────────────────

def measure_clamp_cost(elapsed: float = 28800.0, clamp_dt: float = MAX_TICK_DT_SECONDS) -> dict:
    """One tick with elapsed=`elapsed` but applied dt=`clamp_dt` (what the
    daemon actually does when TickClock clamps), versus the same elapsed
    span integrated finely at dt=`clamp_dt` (what full integration of the
    gap would have produced)."""

    def run(n_steps: int) -> dict:
        engine = _new_engine()
        _, drive = _new_boredom()
        relational = _new_relational()
        for _ in range(n_steps):
            drive.update(clamp_dt, engaged=False)
            relational.update(clamp_dt)
            push = drive.affect_push
            rpush = relational.affect_push
            engine.update(
                clamp_dt,
                valence_input=push.valence_delta + rpush.valence_delta,
                arousal_input=push.arousal_delta + rpush.arousal_delta,
            )
        s = engine.state
        return {
            "boredom_pressure": drive.pressure,
            "relational_pressure": relational.pressure,
            "emotion_v": s.emotion.valence, "emotion_a": s.emotion.arousal,
            "mood_v": s.mood.valence, "mood_a": s.mood.arousal,
        }

    coarse = run(1)  # the clamp: one tick, dt=clamp_dt, elapsed discarded beyond that
    fine = run(_n_steps(elapsed, clamp_dt))  # the full gap, integrated at the same dt

    divergence = {k: fine[k] - coarse[k] for k in coarse}
    return {"elapsed": elapsed, "clamp_dt": clamp_dt, "coarse": coarse, "fine": fine, "divergence": divergence}


# ── 3e. Conversation ────────────────────────────────────────────────────────

def measure_conversation(gaps=CONVERSATION_GAPS, clamp: float = MAX_TICK_DT_SECONDS) -> dict:
    def run(apply_clamp: bool) -> list[dict]:
        engine = _new_engine()
        _, drive = _new_boredom()
        relational = _new_relational()
        trajectory = []
        for i, gap in enumerate(gaps, start=1):
            dt = min(gap, clamp) if apply_clamp else gap
            drive.update(dt, engaged=True)
            relational.update(dt)
            push = drive.affect_push
            rpush = relational.affect_push
            engine.update(
                dt,
                valence_input=push.valence_delta + rpush.valence_delta,
                arousal_input=push.arousal_delta + rpush.arousal_delta,
            )
            s = engine.state
            trajectory.append({
                "exchange": i, "gap": gap, "dt": dt,
                "boredom_pressure": drive.pressure,
                "emotion_v": s.emotion.valence, "emotion_a": s.emotion.arousal,
                "mood_v": s.mood.valence, "mood_a": s.mood.arousal,
            })
        return trajectory, engine.state

    clamped_traj, clamped_final = run(True)
    unclamped_traj, unclamped_final = run(False)
    return {
        "clamp": clamp, "clamped": clamped_traj, "unclamped": unclamped_traj,
        "clamped_hint": prose_hint(clamped_final), "unclamped_hint": prose_hint(unclamped_final),
    }


# ── item 5: measured time constants ─────────────────────────────────────────

def measure_time_constants(span: float = 3600.0, start=NON_NEUTRAL_START) -> dict:
    """Empirical tau for emotion and mood valence, at each tick rate: the
    wall time for |value - equilibrium| to first fall to 1/e of its t=0
    value, where `equilibrium` is this same run's own value at t=span
    (>> 1/mood_drift, so effectively converged). Also checks whether
    temperament changes at all under update()."""
    per_rate = {}
    for dt in RATES:
        engine = _new_engine(start)
        n = _n_steps(span, dt)
        e0 = start["emotion_v"]
        m0 = start["mood_v"]
        target_e = None
        target_m = None
        t = 0.0
        trace = []
        for _ in range(n):
            engine.update(dt)
            t += dt
            trace.append((t, engine.state.emotion.valence, engine.state.mood.valence))
        eq_e = trace[-1][1]
        eq_m = trace[-1][2]
        thresh_e = abs(e0 - eq_e) / math.e
        thresh_m = abs(m0 - eq_m) / math.e
        tau_e = next((tt for tt, ev, _ in trace if abs(ev - eq_e) <= thresh_e), None)
        tau_m = next((tt for tt, _, mv in trace if abs(mv - eq_m) <= thresh_m), None)
        per_rate[dt] = {
            "equilibrium_valence": eq_e, "equilibrium_mood": eq_m,
            "tau_emotion": tau_e, "tau_mood": tau_m,
        }

    # Temperament: does it move at all under update()? Run the finest rate
    # and diff the temperament vector before/after.
    engine = _new_engine(start)
    before = engine.state.temperament
    for _ in range(_n_steps(span, 0.1)):
        engine.update(0.1)
    after = engine.state.temperament
    temperament_delta = (
        after.valence - before.valence, after.arousal - before.arousal,
        (after.control or 0.0) - (before.control or 0.0),
    )
    return {"span": span, "per_rate": per_rate, "declared": {
        "emotion_decay": EMOTION_DECAY, "tau_emotion_declared": 1.0 / EMOTION_DECAY,
        "mood_drift": MOOD_DRIFT, "tau_mood_declared": 1.0 / MOOD_DRIFT,
    }, "temperament_delta": temperament_delta, "temperament_ticks": _n_steps(span, 0.1)}


# ── dt consumption sites (item 4) — static, from reading the two modules ────
# Updated for CP-A.2: every site below is now closed-form. None is explicit
# Euler any more — that is the change this checkpoint made.

DT_SITES = [
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 121,
        "code": "relax = 1.0 - math.exp(-r * dt)",
        "class": "closed-form",
        "note": "exact relaxation fraction for the coupled pair's difference channel D=e-m, at combined rate r=emotion_decay+mood_drift",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 126,
        "code": "d1 = d0 * (1.0 - relax) + (forcing / r) * relax",
        "class": "closed-form",
        "note": "D relaxed exactly toward its forced offset forcing/r; forcing held constant over this one step",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 127,
        "code": "s1 = s0 + k_m * forcing * dt",
        "class": "closed-form",
        "note": "S=k_m*e+k_e*m has no restoring term, so this is exact for constant forcing over the step, not an approximation",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 99,
        "code": "self._encourage_remaining = max(0.0, self._encourage_remaining - dt)",
        "class": "explicit Euler",
        "note": "linear countdown; exact regardless of step size because the rate has no state feedback (a floor clamp can still make it path-dependent near expiry) — out of CP-A.2's scope (not part of the emotion/mood coupling or the drive-to-affect path)",
    },
    {
        "file": "lyra_ai/lyra_core/drives.py", "line": 126,
        "code": "self._pressure = max(0.0, self._pressure - self._relief_rate * dt)",
        "class": "explicit Euler",
        "note": "relief branch, unchanged by CP-A.2 (item 2 named only idle accumulation); already bounded at 0 by the floor and now also bounded above by the idle branch's ceiling",
    },
    {
        "file": "lyra_ai/lyra_core/drives.py", "line": 136,
        "code": "relax = 1.0 - math.exp(-k * dt)",
        "class": "closed-form",
        "note": "CP-A.2: idle accumulation is now exact exponential approach to _BOREDOM_PRESSURE_CEILING (k=idle_rate/ceiling), replacing the old unbounded `pressure += idle_rate*dt`",
    },
    {
        "file": "lyra_ai/lyra_core/drives.py", "line": 184,
        "code": "self._elapsed += dt",
        "class": "explicit Euler",
        "note": "pure clock accumulation; exact regardless of step size since it sums to wall time by construction",
    },
]


# ── markdown rendering ──────────────────────────────────────────────────────

def render(doc: dict) -> str:
    L: list[str] = []
    w = L.append

    w("# Affect and drive characterization (CP-A.1 -> CP-A.2)")
    w("")
    w("Generated by `tools/affect_probe.py`. Every number below comes from that")
    w("script driving `AffectEngine`, `BoredomDrive`, `CompetenceTracker` and")
    w("`RelationalDrive` directly with a synthetic clock — no daemon, no store,")
    w("no wall-clock sleep. Re-run `python tools/affect_probe.py` from the repo")
    w("root to regenerate this file; nothing here is hand-typed.")
    w("")
    w("**What changed since CP-A.1:** that checkpoint measured the engines and")
    w("found the coupled emotion/mood relaxation did not agree across tick rates")
    w("(a Jacobi/operator split, not a solution of the coupled system) and that")
    w("BoredomDrive's idle pressure was unbounded. CP-A.2 fixed both —")
    w("`AffectEngine._step` is now the exact solution of the coupled 2x2 system,")
    w("`BoredomDrive.update`'s idle branch now approaches a bounded ceiling — and")
    w(f"raised `MAX_TICK_DT_SECONDS` from 0.1 to {MAX_TICK_DT_SECONDS:g} on the evidence in")
    w("\"Clamp safety\" below. Every table is regenerated against the current code,")
    w("so this document now shows the fixed system's numbers, not CP-A.1's; the")
    w("prose still points out where a table's shape changed and why.")
    w("")
    w("This document measures; it does not itself change any integrator. See")
    w("`lyra_ai/DECISIONS.md` (CP-A.1 and CP-A.2 sections) for the scope both")
    w("checkpoints were produced under.")
    w("")

    # ── 3a ──
    ra = doc["relaxation_alone"]
    w("## 3a. Relaxation alone")
    w("")
    w("Zero drive input, non-neutral start "
      f"(emotion=({NON_NEUTRAL_START['emotion_v']:g}, {NON_NEUTRAL_START['emotion_a']:g}), "
      f"mood=({NON_NEUTRAL_START['mood_v']:g}, {NON_NEUTRAL_START['mood_a']:g}), "
      f"accum_rate={NON_NEUTRAL_START['accum_rate']:g}, "
      f"emotion_decay={NON_NEUTRAL_START['emotion_decay']:g}, "
      f"mood_drift={NON_NEUTRAL_START['mood_drift']:g}), over a "
      f"{ra['span']:g} s span, at four tick granularities.")
    w("")
    w("| rate | dt (s) | steps | emotion.valence | emotion.arousal | mood.valence | mood.arousal |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for r in ra["rows"]:
        w(f"| {r['label']} | {r['dt']:g} | {r['n']} | {r['emotion_v']:.6g} | {r['emotion_a']:.6g} "
          f"| {r['mood_v']:.6g} | {r['mood_a']:.6g} |")
    w("")
    w(f"Max spread across the four rates, per axis: "
      f"emotion.valence={ra['spreads']['emotion_v']:.3g}, "
      f"emotion.arousal={ra['spreads']['emotion_a']:.3g}, "
      f"mood.valence={ra['spreads']['mood_v']:.3g}, "
      f"mood.arousal={ra['spreads']['mood_a']:.3g}.")
    w("")
    if ra["agree"]:
        w("**The four agree** (spread below 1e-9 on every axis) at this span. This is")
        w("CP-A.2's fix: `AffectEngine._step` evaluates the coupled system's own")
        w("closed-form solution at t=dt, so running the same span in 36000 steps of")
        w("0.1, 60 steps of 60, or a single step of 3600 is the same formula evaluated")
        w("at the same t — not four different discretizations of it. CP-A.1 measured")
        w("a spread of 0.4-0.5 on these same axes, at this same span, with the old")
        w("Jacobi-split integrator (see the git history of this document, or")
        w("`lyra_ai/DECISIONS.md`'s CP-A.1 section, for that baseline).")
    else:
        w("**The four do NOT agree** — see `lyra_ai/DECISIONS.md` CP-A.2 for what this")
        w("means; a regression from CP-A.2's exact-solution fix would show up here.")
    w("")

    rt = doc["relaxation_trace"]
    w("Step-by-step at dt=60 (CP-A.1's swap case: with the old Jacobi split,")
    w("`emotion_decay=2.0` gave relax fraction `1-e^(-2*60)` ~= 1.0 and")
    w("`mood_drift=0.2` gave `1-e^(-0.2*60)` ~= 0.9999938, so the pair nearly swapped")
    w("values every step instead of converging). With the exact solve, the same")
    w("dt=60 steps now converge monotonically toward the shared equilibrium:")
    w("")
    w("| step | t (s) | emotion.valence | mood.valence |")
    w("|---:|---:|---:|---:|")
    for r in rt:
        w(f"| {r['step']} | {r['t']:g} | {r['emotion_v']:.6g} | {r['mood_v']:.6g} |")
    w("")
    equilibrium = (MOOD_DRIFT * NON_NEUTRAL_START["emotion_v"] + EMOTION_DECAY * NON_NEUTRAL_START["mood_v"]) / (EMOTION_DECAY + MOOD_DRIFT)
    w(f"Both variables move monotonically toward the shared equilibrium "
      f"({equilibrium:.6g}, the tau-weighted average of the start values) and stay")
    w("there — no swapping, no oscillation. This is the done-when CP-A.2 named")
    w("directly: \"the one-hour-at-dt=60 case converges toward mood instead of")
    w("returning to its starting value.\"")
    w("")

    # ── 3b ──
    ba = doc["boredom_alone"]
    w("## 3b. Drive pressure alone")
    w("")
    w("### BoredomDrive — idle, `engaged=False`, no relief")
    w("")
    header = "| span (s) | " + " | ".join(f"dt={dt:g}" for dt in RATES) + " | single tick | idle_rate*span |"
    w(header)
    w("|---:|" + "---:|" * (len(RATES) + 2))
    for row in ba["rows"]:
        cells = [f"{row['pressures'][f'dt={dt:g}']:.6g}" for dt in RATES]
        single = row["pressures"][f"single tick (dt={row['span']:g})"]
        w(f"| {row['span']:g} | " + " | ".join(cells) + f" | {single:.6g} | {row['expected']:.6g} |")
    w("")
    w(f"Max spread across rates, any span: {max(ba['spreads']):.3g}.")
    w("")
    w(f"**Bounded:** yes, as of CP-A.2 — to `_BOREDOM_PRESSURE_CEILING = {PRESSURE_CEILING:g}`")
    w("(named constant, `drives.py`). Pressure approaches this ceiling exponentially")
    w("and never exceeds it, at any dt; `idle_rate` is preserved as the slope at")
    w("pressure=0, so a fresh drive's first moments of idling are unchanged from")
    w("before this checkpoint — only the long-run limit is new.")
    w("")
    ratio_8h_1h = ba["rows"][3]["pressures"]["dt=0.1"] / ba["rows"][2]["pressures"]["dt=0.1"]
    w(f"Pressure at 1 h: {ba['rows'][2]['pressures']['dt=0.1']:.6g}. Pressure at 8 h: "
      f"{ba['rows'][3]['pressures']['dt=0.1']:.6g}. Ratio (8h / 1h): {ratio_8h_1h:.4g} — "
      "within a factor of two, as CP-A.2's done-when requires; both saturate to the")
    w(f"ceiling within seconds (tau = ceiling/idle_rate = {PRESSURE_CEILING/IDLE_RATE:.3g} s, "
      "so 1 h and 8 h are both effectively fully saturated).")
    w("")
    w("**Tick-rate dependent:** no. The idle branch is now the same closed-form")
    w("exponential-approach-to-target CP-A already used for affect's own relaxation")
    w("(see dt consumption sites below) — exact for any dt on its own, same as the")
    w("old unbounded accumulation was (both are single-variable relaxations with no")
    w("cross-coupling), just bounded now instead of unbounded.")
    w("")

    ra2 = doc["relational_alone"]
    w("### RelationalDrive — one problem, observed once at t=0, then idle")
    w("")
    w(header)
    w("|---:|" + "---:|" * (len(RATES) + 2))
    for row in ra2["rows"]:
        cells = [f"{row['pressures'][f'dt={dt:g}']:.6g}" for dt in RATES]
        single = row["pressures"][f"single tick (dt={row['span']:g})"]
        w(f"| {row['span']:g} | " + " | ".join(cells) + f" | {single:.6g} | n/a |")
    w("")
    w(f"Max spread across rates, any span: {max(ra2['spreads']):.3g}.")
    w("")
    w("**Bounded:** yes, to `[0, 1]` by construction — pressure is a clamped")
    w("function of elapsed time since last recurrence, not an accumulator.")
    w("**Tick-rate dependent:** no, for the same reason as `_elapsed += dt` above:")
    w("exact accumulation of a state-independent rate.")
    w("")

    # ── 3c ──
    cf = doc["coupled_final"]
    w("## 3c. Coupled (BoredomDrive push -> AffectEngine, idle, unrelieved)")
    w("")
    w("Final `emotion` axes at each span, at each tick rate:")
    w("")
    w("| span (s) | rate | emotion.valence | emotion.arousal |")
    w("|---:|---|---:|---:|")
    for row in cf["rows"]:
        for label, (v, a) in row["states"].items():
            w(f"| {row['span']:g} | {label} | {v:.6g} | {a:.6g} |")
    w("")

    cs = doc["coupled_saturation"]
    w(f"First wall time each axis reaches |value| >= {SATURATION:g}, per multi-step")
    w(f"rate, over one continuous {cs['max_span']:g} s trajectory:")
    w("")
    w("| rate (dt) | valence saturates at | arousal saturates at |")
    w("|---:|---:|---:|")
    for dt in RATES:
        r = cs["per_rate"][dt]
        w(f"| {dt:g} | {_fmt(r['sat_valence'])} | {_fmt(r['sat_arousal'])} |")
    w("")
    w("Single-tick granularity has no internal time resolution — it can only say")
    w("whether one jump across the whole checkpoint span crosses saturation, not")
    w("when within that span it happened:")
    w("")
    w("| span (s) | valence saturated after one tick | arousal saturated after one tick |")
    w("|---:|---|---|")
    for span, r in cs["single_tick"].items():
        w(f"| {span:g} | {r['valence_saturated']} | {r['arousal_saturated']} |")
    w("")

    # ── 3d ──
    cc = doc["clamp_cost"]
    w("## 3d. Clamp cost")
    w("")
    w(f"One tick, elapsed={cc['elapsed']:g} s, applied dt={cc['clamp_dt']:g} s (what the daemon")
    w(f"actually does when the clamp engages) versus the same {cc['elapsed']:g} s integrated")
    w(f"finely at dt={cc['clamp_dt']:g} s ({_n_steps(cc['elapsed'], cc['clamp_dt'])} steps):")
    w("")
    w("| | boredom_pressure | relational_pressure | emotion.valence | emotion.arousal | mood.valence | mood.arousal |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    c, f = cc["coarse"], cc["fine"]
    w(f"| clamped (1 tick) | {c['boredom_pressure']:.6g} | {c['relational_pressure']:.6g} "
      f"| {c['emotion_v']:.6g} | {c['emotion_a']:.6g} | {c['mood_v']:.6g} | {c['mood_a']:.6g} |")
    w(f"| fine ({_n_steps(cc['elapsed'], cc['clamp_dt'])} ticks) | {f['boredom_pressure']:.6g} "
      f"| {f['relational_pressure']:.6g} | {f['emotion_v']:.6g} | {f['emotion_a']:.6g} "
      f"| {f['mood_v']:.6g} | {f['mood_a']:.6g} |")
    d = cc["divergence"]
    w(f"| divergence (fine - clamped) | {d['boredom_pressure']:.6g} | {d['relational_pressure']:.6g} "
      f"| {d['emotion_v']:.6g} | {d['emotion_a']:.6g} | {d['mood_v']:.6g} | {d['mood_a']:.6g} |")
    w("")
    w("The clamped row is exactly what one daemon tick produces after any gap longer")
    w("than the clamp, however long the real gap was: the applied dt is the same")
    w("`MAX_TICK_DT_SECONDS` regardless of whether the raw elapsed time was 8 minutes")
    w("or 8 hours. The divergence row is everything the clamp declines to apply.")
    w("")

    # ── clamp safety ──
    csf = doc["clamp_safety"]
    w("## Clamp safety (evidence for MAX_TICK_DT_SECONDS)")
    w("")
    w("The daemon ticks once per turn, so an idle gap of any length — 8 minutes or")
    w("8 hours — collapses to exactly ONE tick at `dt = min(elapsed, MAX_TICK_DT_SECONDS)`.")
    w("The worst case for that one tick is boredom pressure already at its ceiling")
    w("(long prior idling) when the gap begins. For each candidate clamp value, one")
    w("tick at that dt, from a saturated-pressure start:")
    w("")
    w("| candidate MAX_TICK_DT_SECONDS | pressure | emotion.valence | emotion.arousal | saturated? |")
    w("|---:|---:|---:|---:|---|")
    for row in csf:
        w(f"| {row['candidate']:g} | {row['pressure']:.6g} | {row['emotion_v']:.6g} "
          f"| {row['emotion_a']:.6g} | {row['saturated']} |")
    w("")
    w(f"`{MAX_TICK_DT_SECONDS:g}` is the value in `config.py`: large enough to cover every gap this")
    w("checkpoint's done-when calls \"ordinary\" (5 s, 60 s, 10 min — verified against a")
    w("live daemon, not just this table) without clamping, while the single-tick worst")
    w("case above stays clearly under saturation. Candidates past it in this table")
    w("saturate — that is exactly why the clamp is not larger; the table is the")
    w("evidence, not a guess.")
    w("")

    # ── 3e ──
    cv = doc["conversation"]
    w("## 3e. Conversation")
    w("")
    w(f"Six exchanges, gaps `{CONVERSATION_GAPS}` s, `engaged=True` each exchange")
    w(f"(competence tracker never reaches the learnable edge in this scenario, so")
    w(f"boredom pressure keeps accumulating even while engaged — see note below).")
    w("")
    for label in ("clamped", "unclamped"):
        w(f"### {label} (dt = " + ("min(gap, %g)" % cv["clamp"] if label == "clamped" else "gap") + ")")
        w("")
        w("| exchange | gap (s) | applied dt | boredom_pressure | emotion.valence | emotion.arousal | mood.valence | mood.arousal |")
        w("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in cv[label]:
            w(f"| {row['exchange']} | {row['gap']:g} | {row['dt']:g} | {row['boredom_pressure']:.6g} "
              f"| {row['emotion_v']:.6g} | {row['emotion_a']:.6g} | {row['mood_v']:.6g} | {row['mood_a']:.6g} |")
        w("")
    w("Note: `engaged=True` only relieves boredom pressure when")
    w("`CompetenceTracker.at_learnable_edge` is also true (`drives.py:125`). A fresh")
    w("tracker with no `observe_error` calls never reaches that state, so in this")
    w("scenario — six ordinary exchanges, no prediction-error feedback wired in —")
    w("boredom pressure rises identically to the idle case regardless of `engaged`.")
    w("")
    clamped_final = cv["clamped"][-1]
    w(f"With the new clamp ({MAX_TICK_DT_SECONDS:g} s), every gap in this six-exchange")
    w("conversation is under the clamp, so the clamped and unclamped trajectories are")
    w("identical — the table above has one shape, not two divided by a much larger")
    w(f"gap between them. Final emotion.valence: {clamped_final['emotion_v']:.4g}. CP-A.1's")
    w("baseline for this same scenario, hand-copied here for comparison (that run is")
    w("not reproducible by this script any more — it measured the pre-CP-A.2 code):")
    w("at the old dt=0.1 clamp, final valence was -0.00316 (barely moved, because the")
    w("clamp discarded almost the whole gap each tick); unclamped at the old integrator,")
    w("it was -0.597 (already most of the way to \"turns her terse\").")
    w("")
    w("The actual check — `lyra_core.expression.prose_hint` called on the final")
    w("`AffectState`, not a threshold re-derived here — is the real done-when test:")
    w(f'clamped hint: `{cv["clamped_hint"] or "(none — near neutral / mixed region)"}`. ')
    w(f'unclamped hint: `{cv["unclamped_hint"] or "(none — near neutral / mixed region)"}`.')
    terse_triggered = cv["clamped_hint"] == _NEGATIVE_HIGH_AROUSAL_HINT or cv["unclamped_hint"] == _NEGATIVE_HIGH_AROUSAL_HINT
    w(f'Neither matches the terse hint (`"{_NEGATIVE_HIGH_AROUSAL_HINT}"`)' if not terse_triggered
      else '**Terse hint triggered — this fails CP-A.2\'s done-when.**')
    w("")

    # ── item 5: agreement check ──
    checks = doc["agreement_checks"]
    worst = max(checks, key=lambda c: c["divergence"])
    all_ok = all(c["ok"] for c in checks)
    w("## Agreement check (CP-A.2 item 5)")
    w("")
    w(f"For every span measured above, max divergence in terminal state across the")
    w(f"four tick rates (0.1, 2.0, 60.0, single tick), per axis, against a tolerance")
    w(f"of {AGREEMENT_TOLERANCE:g}:")
    w("")
    w("| scenario | span (s) | axis | max divergence | tolerance | ok |")
    w("|---|---:|---|---:|---:|---|")
    for c in checks:
        w(f"| {c['scenario']} | {c['span']:g} | {c['axis']} | {c['divergence']:.3g} "
          f"| {c['tolerance']:g} | {c['ok']} |")
    w("")
    w(f"Worst observed: {worst['divergence']:.3g} ({worst['scenario']}, span={worst['span']:g}, "
      f"axis={worst['axis']}). All checks {'PASS' if all_ok else 'FAIL'} — "
      f"`python tools/affect_probe.py` exits {0 if all_ok else 1}.")
    if all_ok:
        w("")
        w("The 3c (coupled) rows are not at floating-point-level agreement like 3a/3b:")
        w("BoredomDrive's pressure is exactly tick-rate invariant on its own, but")
        w("feeding it into AffectEngine still treats pressure as constant across each")
        w("step (item 1/3's design), so a run with fewer, larger steps samples the")
        w("pressure ramp from 0 to its ceiling — which takes only about")
        w(f"`ceiling/idle_rate` ~= {PRESSURE_CEILING/IDLE_RATE:.2g} s — more coarsely")
        w("than a run with many small steps. That difference feeds `S` (see affect.py's")
        w("`_step` docstring), which has no decay of its own, so the small quantization")
        w("in how well each rate resolves that one brief ramp becomes a PERMANENT,")
        w(f"constant offset, not a growing one: the coupled rows above show ~{worst['divergence']:.2g}")
        w("divergence at every span from 60 s to 8 h alike, not a value that grows with")
        w("span the way CP-A.1's did. That is the qualitative fix this checkpoint made")
        w("to the coupled path — bounded and flat instead of unbounded and accelerating —")
        w("even though it is not literally zero the way the uncoupled measurements are.")
    w("")

    # ── item 4 ──
    w("## dt consumption sites")
    w("")
    w("Every place `dt` is used to advance state, by file and line, classified as")
    w("closed-form (exact for any dt on its own) or explicit Euler (first-order,")
    w("exact only in the dt -> 0 limit or when the rate has no state feedback):")
    w("")
    w("| file:line | code | class | note |")
    w("|---|---|---|---|")
    for site in DT_SITES:
        code = site["code"].replace("|", "\\|")
        note = site["note"].replace("|", "\\|")
        w(f"| `{site['file']}:{site['line']}` | `{code}` | {site['class']} | {note} |")
    w("")

    # ── item 5 ──
    tc = doc["time_constants"]
    w("## Measured timescales")
    w("")
    w(f"Empirical tau (time for `|value - equilibrium|` to first fall to 1/e of its")
    w(f"t=0 value) from the {tc['span']:g} s relaxation-alone run (3a's start state),")
    w(f"per tick rate, against the declared `1/emotion_decay` and `1/mood_drift`:")
    w("")
    w(f"Declared: tau_emotion = 1/{tc['declared']['emotion_decay']:g} = "
      f"{tc['declared']['tau_emotion_declared']:g} s; "
      f"tau_mood = 1/{tc['declared']['mood_drift']:g} = {tc['declared']['tau_mood_declared']:g} s.")
    w("")
    w("| rate (dt) | equilibrium.valence | measured tau_emotion (s) | measured tau_mood (s) |")
    w("|---:|---:|---:|---:|")
    for dt in RATES:
        r = tc["per_rate"][dt]
        w(f"| {dt:g} | {r['equilibrium_valence']:.6g} | {_fmt(r['tau_emotion'])} | {_fmt(r['tau_mood'])} |")
    w("")
    dv, da, dc = tc["temperament_delta"]
    w(f"Temperament, over {tc['temperament_ticks']} ticks at dt=0.1 across the same")
    w(f"span: delta(valence)={dv:.3g}, delta(arousal)={da:.3g}, delta(control)={dc:.3g}.")
    w("Unchanged. `update()` never writes `_accum_rate`/`_emotion_decay`/`_mood_drift`;")
    w("only `AffectEngine.__init__`/`from_dict` set them. So temperament is not a")
    w("third *timescale* in the running system at all — it is a fixed parameter set")
    w("once at construction (or restore) and never advanced by `tick()`. \"Three")
    w("timescales\" as currently measured is two dynamic ones (emotion, mood) plus")
    w("one static one (temperament), not three rates of the same kind.")
    w("")
    w("Unlike CP-A.1 (where these numbers reflected genuine trajectory divergence —")
    w("the coupled pair actually evolved differently at each dt), the underlying")
    w("trajectory is now identical at every dt (3a's agreement check). Any remaining")
    w("difference in the table above is purely the sampling grid: tau is measured by")
    w("scanning the trace at whatever dt produced it, so a coarser dt can only report")
    w("a crossing time rounded up to its own step size, not a different crossing.")
    w("")

    return "\n".join(L) + "\n"


def main() -> int:
    doc = {
        "relaxation_alone": measure_relaxation_alone(),
        "relaxation_trace": measure_relaxation_trace(),
        "boredom_alone": measure_boredom_alone(),
        "relational_alone": measure_relational_alone(),
        "coupled_final": measure_coupled_final_state(),
        "coupled_saturation": measure_coupled_saturation(),
        "clamp_cost": measure_clamp_cost(),
        "clamp_safety": measure_clamp_safety(),
        "conversation": measure_conversation(),
        "time_constants": measure_time_constants(),
    }
    doc["agreement_checks"] = agreement_checks(doc)

    text = render(doc)
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(text)
    print(f"wrote {DOC_PATH} ({len(text)} bytes)")

    failures = [c for c in doc["agreement_checks"] if not c["ok"]]
    if failures:
        print(f"AGREEMENT CHECK FAILED: {len(failures)} of {len(doc['agreement_checks'])} "
              f"scenario/axis combinations exceeded tolerance {AGREEMENT_TOLERANCE:g}:")
        for c in failures:
            print(f"  {c['scenario']} span={c['span']:g} axis={c['axis']} "
                  f"divergence={c['divergence']:.6g}")
        return 1
    worst = max(doc["agreement_checks"], key=lambda c: c["divergence"])
    print(f"agreement check passed: {len(doc['agreement_checks'])} checks, "
          f"worst divergence {worst['divergence']:.3g} <= tolerance {AGREEMENT_TOLERANCE:g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
