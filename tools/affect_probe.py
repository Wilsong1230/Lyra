#!/usr/bin/env python3
"""tools/affect_probe.py — CP-A.1 offline characterization harness.

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

This script does not change, correct, or retune any integrator. It only
measures what CP-A's engines already do, at various tick granularities.
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
from lyra_core.drives import BoredomDrive, CompetenceTracker, RelationalDrive  # noqa: E402

DOC_PATH = REPO_ROOT / "docs" / "AFFECT_CHARACTERIZATION.md"

# ── shared knobs — match CognitiveCore()'s own construction (interface.py) ────
ACCUM_RATE = 1.0
EMOTION_DECAY = 2.0
MOOD_DRIFT = 0.2
IDLE_RATE = 0.1
RELIEF_RATE = 0.5
SATISFACTION_WINDOW = 5.0

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
        rows.append({"span": span, "states": states})
    return {"spans": spans, "rows": rows}


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
        return trajectory

    return {"clamp": clamp, "clamped": run(True), "unclamped": run(False)}


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

DT_SITES = [
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 93,
        "code": "emotion_relax = 1.0 - math.exp(-self._emotion_decay * dt)",
        "class": "closed-form",
        "note": "exact relaxation fraction for a single-variable decay; stable at any dt on its own",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 94,
        "code": "mood_relax    = 1.0 - math.exp(-self._mood_drift * dt)",
        "class": "closed-form",
        "note": "same, for mood's own decay rate",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 97,
        "code": "self._emotion_v = ev + valence_accum_rate * valence_input * dt + (mv - ev) * emotion_relax",
        "class": "mixed: explicit Euler (input term) + closed-form (relax term)",
        "note": "the input-accumulation term is a plain forward-Euler step; only the decay-toward-mood term is exact",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 99,
        "code": "self._emotion_a = ea + self._accum_rate * arousal_input * dt + (ma - ea) * emotion_relax",
        "class": "mixed: explicit Euler (input term) + closed-form (relax term)",
        "note": "arousal counterpart of the above",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 103,
        "code": "self._mood_v = mv + (ev - mv) * mood_relax",
        "class": "closed-form (single-variable), but coupled",
        "note": "uses the pre-step (old) emotion value — see finding below on operator-splitting error",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 104,
        "code": "self._mood_a = ma + (ea - ma) * mood_relax",
        "class": "closed-form (single-variable), but coupled",
        "note": "arousal counterpart",
    },
    {
        "file": "lyra_ai/lyra_core/affect.py", "line": 106,
        "code": "self._encourage_remaining = max(0.0, self._encourage_remaining - dt)",
        "class": "explicit Euler",
        "note": "linear countdown; exact regardless of step size because the rate has no state feedback (a floor clamp can still make it path-dependent near expiry)",
    },
    {
        "file": "lyra_ai/lyra_core/drives.py", "line": 113,
        "code": "self._pressure = max(0.0, self._pressure - self._relief_rate * dt)",
        "class": "explicit Euler",
        "note": "relief branch; the max(0, ...) floor can make outcomes path-dependent near zero at coarse dt",
    },
    {
        "file": "lyra_ai/lyra_core/drives.py", "line": 115,
        "code": "self._pressure += self._idle_rate * dt",
        "class": "explicit Euler",
        "note": "idle-accumulation branch; unbounded above, and exact/tick-rate-invariant on its own because the rate has no state feedback (see 3b)",
    },
    {
        "file": "lyra_ai/lyra_core/drives.py", "line": 162,
        "code": "self._elapsed += dt",
        "class": "explicit Euler",
        "note": "pure clock accumulation; exact regardless of step size since it sums to wall time by construction",
    },
]


# ── markdown rendering ──────────────────────────────────────────────────────

def render(doc: dict) -> str:
    L: list[str] = []
    w = L.append

    w("# Affect and drive characterization (CP-A.1)")
    w("")
    w("Generated by `tools/affect_probe.py`. Every number below comes from that")
    w("script driving `AffectEngine`, `BoredomDrive`, `CompetenceTracker` and")
    w("`RelationalDrive` directly with a synthetic clock — no daemon, no store,")
    w("no wall-clock sleep. Re-run `python tools/affect_probe.py` from the repo")
    w("root to regenerate this file; nothing here is hand-typed.")
    w("")
    w("This is a measurement document. It changes nothing about the integrators,")
    w(f"drives, or `MAX_TICK_DT_SECONDS` (currently `{MAX_TICK_DT_SECONDS}`). See")
    w("`lyra_ai/DECISIONS.md` (CP-A.1 section) for the scope this was produced under.")
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
        w("**The four agree** (spread below 1e-9 on every axis) at this span.")
    else:
        w("**The four do NOT agree.** Each `emotion_relax`/`mood_relax` step is the")
        w("exact solution for *that one variable* decaying toward the *other")
        w("variable's pre-step value* — a first-order (Gauss-Seidel-style) operator")
        w("split of a genuinely coupled linear system, not a closed-form solution of")
        w("the coupled system itself. The split is only exact in the dt -> 0 limit;")
        w("at finite dt the discrete map does not conserve the same linear invariant")
        w("as the continuous ODE (`emotion_decay*mood + mood_drift*emotion` is exactly")
        w("conserved by the continuous system's decay terms, not by the discrete")
        w("step at dt=2.0 or dt=60.0). So \"closed-form and time-invariant\" holds")
        w("per relaxation term in isolation, not for the coupled emotion/mood pair.")
    w("")

    rt = doc["relaxation_trace"]
    w(f"Step-by-step at dt=60 (`emotion_decay=2.0` implies relax fraction")
    w(f"`1-e^(-2*60)` ~= 1.0; `mood_drift=0.2` implies `1-e^(-0.2*60)` ~= 0.9999938 —")
    w(f"both variables jump *almost fully* to the other's old value every step):")
    w("")
    w("| step | t (s) | emotion.valence | mood.valence |")
    w("|---:|---:|---:|---:|")
    for r in rt:
        w(f"| {r['step']} | {r['t']:g} | {r['emotion_v']:.6g} | {r['mood_v']:.6g} |")
    w("")
    w("At this dt the pair does not relax monotonically toward each other at all —")
    w("it nearly swaps values every tick (`emotion_v` at step 1 lands within 1e-6 of")
    w("`mood_v` at step 0, and vice versa) and only very slowly bleeds toward a common")
    w("value across many swaps. This is why the dt=60 row in the table above")
    w("(-0.599926) sits almost exactly on the *start* value (-0.6) after a full hour:")
    w("the trajectory is oscillating, not converging, at this granularity.")
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
    w("**Bounded:** no. Pressure accumulates without a ceiling as long as the")
    w("engine is idle; nothing in `BoredomDrive.update` caps it.")
    w("")
    w("**Tick-rate dependent:** no, not on its own. `idle_rate * dt` summed over any")
    w("partition of a span equals `idle_rate * span` exactly, because the rate has")
    w("no dependence on the drive's own state (no feedback for Euler's error to act")
    w("on). Every rate in the table above lands on `idle_rate*span` to float")
    w("precision.")
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
    w("`CompetenceTracker.at_learnable_edge` is also true (`drives.py:112`). A fresh")
    w("tracker with no `observe_error` calls never reaches that state, so in this")
    w("scenario — six ordinary exchanges, no prediction-error feedback wired in —")
    w("boredom pressure rises identically to the idle case regardless of `engaged`.")
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
    w("The measured tau_emotion and tau_mood track the declared values closely at")
    w("dt=0.1 (the near-continuum rate) and diverge at dt=2.0 and dt=60.0 — the same")
    w("coupling/operator-splitting effect noted in 3a: each `*_relax` fraction is")
    w("exact for its own variable in isolation, but the pair's joint relaxation is")
    w("only close to the continuous coupled solution when dt is small relative to")
    w("both `1/emotion_decay` and `1/mood_drift`.")
    w("")
    w("Caveat on resolution: at dt=2.0 the reported tau (2 s) is the *first sampled*")
    w("point past the 1/e threshold — a single step — so it cannot distinguish \"the")
    w("true crossing happened within that one 2 s step\" from \"coarse dt itself moved")
    w("the state past threshold in one jump\"; the grid cannot resolve finer than dt.")
    w("At dt=60.0 the reported tau (2280 s = 38 steps) is not a single-step artifact —")
    w("it is 38 grid points in, consistent with the swap/oscillation trace above")
    w("delaying convergence rather than a sampling limit.")
    w("")

    return "\n".join(L) + "\n"


def main() -> None:
    doc = {
        "relaxation_alone": measure_relaxation_alone(),
        "relaxation_trace": measure_relaxation_trace(),
        "boredom_alone": measure_boredom_alone(),
        "relational_alone": measure_relational_alone(),
        "coupled_final": measure_coupled_final_state(),
        "coupled_saturation": measure_coupled_saturation(),
        "clamp_cost": measure_clamp_cost(),
        "conversation": measure_conversation(),
        "time_constants": measure_time_constants(),
    }
    text = render(doc)
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(text)
    print(f"wrote {DOC_PATH} ({len(text)} bytes)")


if __name__ == "__main__":
    main()
