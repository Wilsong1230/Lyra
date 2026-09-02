"""lyra_core.report — read-only self-report over the live store (CP-C).

WHY: the memory store was disconnected for three months and produced no
symptom (CP-A's register). This module exists so there is a standing,
external way to see whether accumulation is happening at all — not to fix
anything it finds.

Everything here is READ-ONLY. The store is opened via a sqlite URI in
`mode=ro`: this connection cannot create, migrate, or archive anything, and
is safe to run against a store a daemon is actively writing (WAL readers
don't block the writer, and mode=ro makes a write attempt fail loudly rather
than silently corrupt anything). This module does not import
lyra_core.runtime.Runtime, lyra_core.runtime.TurnHandler, or
lyra_core.transport — it has no business starting a daemon or a listener.

Two callers share every measurement and every field name below:
  - `python -m lyra_core --report` (lyra_core/__main__.py): opens its own
    short-lived read-only connection via `collect_from_path()`.
  - the running daemon's periodic telemetry line (lyra_core/runtime.py,
    change 5): calls `collect_measurements()` directly against its own
    already-open, already-read-write Store — no second connection, no
    read-only requirement (the daemon already owns exclusive legitimate
    write access to its own store).

Retrieval provenance (change 2c) is NOT read from Store's `context_log`
table: the daemon's turn path (CP-B) calls `lyra_memory.store.context.
build_context()` directly rather than through `Store.ingest_turn()`, which
is the only thing that ever writes `context_log` — so on every daemon this
checkpoint has run against, that table has zero rows. See DECISIONS.md
(CP-C) for the finding and why fixing the turn path is out of this
checkpoint's FILES set. Instead, "assemblies performed" here means a live,
read-only retrieval self-test this module performs itself: it takes real
atom text already sitting in the window as query strings and calls the
same `build_context()` the daemon calls, read-only, and classifies each
result by which of {vector, FTS} paths actually contributed. This is a
measurement of retrieval health against real content, not a historical
tally of turns — a real and disclosed difference from what "assemblies
performed" would mean if `context_log` were populated.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from lyra_core.config import LOG_PATH
from lyra_memory.config import FORGET_THRESHOLD
from lyra_memory.store import load_vec_extension
from lyra_memory.store.context import build_context

UNAVAILABLE = "unavailable"

# Retrieval self-test sample size — shared by report.py and the daemon's
# periodic emission (same measurements, same field names, change 5).
_ASSEMBLY_SAMPLE_LIMIT = 20

DEFAULT_WINDOW_DAYS = 7

# ── KNOWN GAPS — hardcoded, not computed (change 3) ─────────────────────────

KNOWN_GAPS: list[str] = [
    "affect_state is persisted as a facts row (subject="
    "'_lyra_internal_affect_state'), not a dedicated table — machine state "
    "sits in the same table as Lyra's record of the world (CP-B, item 3). "
    "Not fixed here; reported per CP-C's register.",
    "The recorded retrieval coverage baseline is 1.00 and is NOT trusted — "
    "see this run's eval-set characterization below and DECISIONS.md "
    "(CP-C, change 7).",
    "runs.db is open and empty by design — nothing writes bulk telemetry "
    "into it yet (deferred).",
    "MemorySystem, db.py, and retrieval.py are alive in the tree but dead "
    "on the daemon path — imported only by memory_bridge.py and their own "
    "tests (CP-B).",
]

# ── the stable field names (report.py and runtime.py's periodic line share
# these exactly) ────────────────────────────────────────────────────────────

FIELDS: tuple[str, ...] = (
    "atoms_today", "atoms_window_total", "atoms_total",
    "atoms_below_floor", "atoms_above_floor",
    "assemblies_total", "assemblies_with_hit",
    "retrieval_vector_only", "retrieval_fts_only",
    "retrieval_both", "retrieval_neither",
    "candidates_total", "candidates_promoted_window",
    "trait_count", "trait_confidence_mean",
    "outcomes_total",
    "emotion_v", "emotion_a", "mood_v", "mood_a",
    "temperament_v", "temperament_a", "temperament_c",
    "emotion_v_min", "emotion_v_max", "emotion_a_min", "emotion_a_max",
    "mood_v_min", "mood_v_max", "mood_a_min", "mood_a_max",
    "clamp_count_window", "clamp_max_elapsed_window",
    "store_db_bytes", "runs_db_bytes", "history_db_bytes",
)


class _ReadOnlyStore:
    """Minimal duck-typed stand-in for lyra_memory.store.Store.

    build_context() only ever touches `store.db` — this is not Store
    itself (Store.open() does DDL-if-new and a full schema assertion,
    neither of which belongs in a strictly read-only tool, and would fail
    outright under mode=ro if ever exercised).
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db


async def open_read_only(path: Path) -> _ReadOnlyStore:
    """Open `path` read-only. Never creates, migrates, or archives anything —
    a mode=ro connection cannot execute a write at all; SQLite raises."""
    conn = await aiosqlite.connect(f"file:{path}?mode=ro", uri=True)
    await load_vec_extension(conn)
    return _ReadOnlyStore(conn)


# ── measurement sections — each independently guarded (change 5: "If a
# measurement query fails, the line is emitted with that field marked
# unavailable and the daemon stays up") ─────────────────────────────────────

async def _atoms_section(db: aiosqlite.Connection, now: float, window_start: float) -> dict:
    today_start = datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    async with db.execute(
        "SELECT COUNT(*) FROM atoms WHERE ts >= ?", (today_start,)
    ) as cur:
        today = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT COUNT(*) FROM atoms WHERE ts >= ?", (window_start,)
    ) as cur:
        window_total = (await cur.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM atoms") as cur:
        total = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT date(ts, 'unixepoch') AS day, COUNT(*) FROM atoms"
        " WHERE ts >= ? GROUP BY day ORDER BY day",
        (window_start,),
    ) as cur:
        by_day = [(r[0], r[1]) for r in await cur.fetchall()]
    return {
        "atoms_today": today, "atoms_window_total": window_total,
        "atoms_total": total, "_atoms_by_day": by_day,
    }


async def _retrievability_section(db: aiosqlite.Connection) -> dict:
    """"Forgetting, observed" (change 2b) — a snapshot, not window-scoped:
    retrievability is a persistent per-atom attribute the forgetting pass
    updates whenever it runs, not something that resets each window."""
    async with db.execute(
        "SELECT COUNT(*) FROM atoms WHERE retrievability IS NOT NULL"
        " AND retrievability < ?",
        (FORGET_THRESHOLD,),
    ) as cur:
        below = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT COUNT(*) FROM atoms WHERE retrievability IS NULL"
        " OR retrievability >= ?",
        (FORGET_THRESHOLD,),
    ) as cur:
        above = (await cur.fetchone())[0]
    return {"atoms_below_floor": below, "atoms_above_floor": above}


def _classify_misses(misses: list[str]) -> tuple[bool, bool]:
    """(vector_hit, fts_hit) from a ContextResult's misses list — a miss
    entry means that path found nothing; its absence means it did."""
    vector_missed = any(m.startswith("semantic:") for m in misses)
    fts_missed = any(m.startswith("lexical:") for m in misses)
    return not vector_missed, not fts_missed


async def _retrieval_section(
    store_like: _ReadOnlyStore, db: aiosqlite.Connection, window_start: float
) -> dict:
    """Live, read-only retrieval self-test — see module docstring for why
    this isn't a historical context_log tally."""
    async with db.execute(
        "SELECT DISTINCT text FROM atoms WHERE ts >= ? AND speaker = 'wilson'"
        " AND length(text) > 0 ORDER BY id DESC LIMIT ?",
        (window_start, _ASSEMBLY_SAMPLE_LIMIT),
    ) as cur:
        queries = [r[0] for r in await cur.fetchall()]

    vector_only = fts_only = both = neither = with_hit = 0
    for q in queries:
        ctx = await build_context(store_like, query=q, recent_turns=0)
        vector_hit, fts_hit = _classify_misses(ctx.misses)
        if vector_hit and fts_hit:
            both += 1
        elif vector_hit:
            vector_only += 1
        elif fts_hit:
            fts_only += 1
        else:
            neither += 1
        if vector_hit or fts_hit:
            with_hit += 1

    return {
        "assemblies_total": len(queries), "assemblies_with_hit": with_hit,
        "retrieval_vector_only": vector_only, "retrieval_fts_only": fts_only,
        "retrieval_both": both, "retrieval_neither": neither,
    }


async def _candidates_traits_section(db: aiosqlite.Connection, window_start: float) -> dict:
    async with db.execute("SELECT COUNT(*) FROM candidates") as cur:
        candidates_total = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT COUNT(*) FROM trait_history WHERE event = 'promoted' AND ts >= ?",
        (window_start,),
    ) as cur:
        promoted = (await cur.fetchone())[0]
    async with db.execute("SELECT name, value, confidence FROM traits") as cur:
        rows = await cur.fetchall()
    confidences = [r[2] for r in rows if r[2] is not None]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return {
        "candidates_total": candidates_total,
        "candidates_promoted_window": promoted,
        "trait_count": len(rows),
        "trait_confidence_mean": mean_conf,
        "_traits": [(r[0], r[1], r[2]) for r in rows],
    }


async def _outcomes_section(db: aiosqlite.Connection) -> dict:
    async with db.execute("SELECT COUNT(*) FROM outcomes") as cur:
        total = (await cur.fetchone())[0]
    return {"outcomes_total": total}


async def _affect_section(db: aiosqlite.Connection, log_path: Path, window_start: float) -> dict:
    async with db.execute(
        "SELECT text FROM facts WHERE subject = '_lyra_internal_affect_state'"
        " AND valid_until IS NULL ORDER BY id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()

    result: dict = {}
    if row is not None:
        import json
        state = json.loads(row[0])
        result.update({
            "emotion_v": state.get("emotion_v"), "emotion_a": state.get("emotion_a"),
            "mood_v": state.get("mood_v"), "mood_a": state.get("mood_a"),
            # Temperament is a static parameter set, not a third dynamic
            # timescale (CP-A.2 register) — reported as the current
            # constants, no min/max (there is nothing to range over).
            "temperament_v": state.get("accum_rate"),
            "temperament_a": state.get("emotion_decay"),
            "temperament_c": state.get("mood_drift"),
        })
    else:
        for f in ("emotion_v", "emotion_a", "mood_v", "mood_a",
                   "temperament_v", "temperament_a", "temperament_c"):
            result[f] = UNAVAILABLE

    ranges = _log_affect_ranges(log_path, window_start)
    result.update(ranges)
    return result


_TICK_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \S+ lyra_core\.runtime: "
    r"(?:tick|DT_CLAMP_ENGAGED) .*?elapsed=(?P<elapsed>[-0-9.eE]+) "
    r"dt=[-0-9.eE]+ clamped=(?P<clamped>True|False) .*?"
    r"emotion_v=(?P<emotion_v>[-0-9.eE]+) emotion_a=(?P<emotion_a>[-0-9.eE]+) "
    r"mood_v=(?P<mood_v>[-0-9.eE]+) mood_a=(?P<mood_a>[-0-9.eE]+)"
)


def _log_affect_ranges(log_path: Path, window_start: float) -> dict:
    """min/max per axis over the window, from tick lines in lyra_core.log —
    "if the log is available" (change 2f): a missing/empty log is not an
    error, just nothing to range over."""
    axes = ("emotion_v", "emotion_a", "mood_v", "mood_a")
    if not log_path.is_file():
        out = {}
        for a in axes:
            out[f"{a}_min"] = UNAVAILABLE
            out[f"{a}_max"] = UNAVAILABLE
        return out

    mins = {a: None for a in axes}
    maxs = {a: None for a in axes}
    for line in log_path.read_text(errors="replace").splitlines():
        m = _TICK_LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if ts < window_start:
            continue
        for a in axes:
            v = float(m.group(a))
            mins[a] = v if mins[a] is None else min(mins[a], v)
            maxs[a] = v if maxs[a] is None else max(maxs[a], v)

    out = {}
    for a in axes:
        out[f"{a}_min"] = mins[a] if mins[a] is not None else UNAVAILABLE
        out[f"{a}_max"] = maxs[a] if maxs[a] is not None else UNAVAILABLE
    return out


def _clamp_section(log_path: Path, window_start: float) -> dict:
    if not log_path.is_file():
        return {"clamp_count_window": UNAVAILABLE, "clamp_max_elapsed_window": UNAVAILABLE}

    count = 0
    max_elapsed = None
    for line in log_path.read_text(errors="replace").splitlines():
        m = _TICK_LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if ts < window_start:
            continue
        elapsed = float(m.group("elapsed"))
        max_elapsed = elapsed if max_elapsed is None else max(max_elapsed, elapsed)
        if m.group("clamped") == "True":
            count += 1
    return {
        "clamp_count_window": count,
        "clamp_max_elapsed_window": max_elapsed if max_elapsed is not None else UNAVAILABLE,
    }


def _file_sizes_section(store_path: Path, runs_path: Path, history_path: Path | None) -> dict:
    def size(p: Path | None) -> object:
        if p is None or not p.is_file():
            return UNAVAILABLE
        return p.stat().st_size

    return {
        "store_db_bytes": size(store_path),
        "runs_db_bytes": size(runs_path),
        "history_db_bytes": size(history_path),
    }


# ── orchestration ────────────────────────────────────────────────────────────

@dataclass
class Report:
    measurements: dict
    window_days: int
    generated_at: float


async def collect_measurements(
    store_like: _ReadOnlyStore,
    *,
    store_path: Path,
    runs_path: Path,
    history_path: Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    log_path: Path = LOG_PATH,
    now: float | None = None,
) -> dict:
    """Every measurement in change 2, as a flat dict. Never raises: a
    section whose query fails has its fields set to UNAVAILABLE instead —
    telemetry is not a critical path (change 5)."""
    now = now if now is not None else time.time()
    window_start = now - window_days * 86400.0
    db = store_like.db

    out: dict = {}

    async def _section(name: str, fields: tuple[str, ...], coro) -> None:
        try:
            out.update(await coro)
        except Exception:
            for f in fields:
                out[f] = UNAVAILABLE

    await _section(
        "atoms", ("atoms_today", "atoms_window_total", "atoms_total"),
        _atoms_section(db, now, window_start),
    )
    await _section(
        "retrievability", ("atoms_below_floor", "atoms_above_floor"),
        _retrievability_section(db),
    )
    await _section(
        "retrieval",
        ("assemblies_total", "assemblies_with_hit", "retrieval_vector_only",
         "retrieval_fts_only", "retrieval_both", "retrieval_neither"),
        _retrieval_section(store_like, db, window_start),
    )
    await _section(
        "candidates_traits",
        ("candidates_total", "candidates_promoted_window", "trait_count",
         "trait_confidence_mean"),
        _candidates_traits_section(db, window_start),
    )
    await _section("outcomes", ("outcomes_total",), _outcomes_section(db))
    await _section(
        "affect",
        ("emotion_v", "emotion_a", "mood_v", "mood_a", "temperament_v",
         "temperament_a", "temperament_c", "emotion_v_min", "emotion_v_max",
         "emotion_a_min", "emotion_a_max", "mood_v_min", "mood_v_max",
         "mood_a_min", "mood_a_max"),
        _affect_section(db, log_path, window_start),
    )

    # Synchronous sections (log-file/filesystem only) get the same
    # per-section isolation without needing to be awaited.
    try:
        out.update(_clamp_section(log_path, window_start))
    except Exception:
        out["clamp_count_window"] = UNAVAILABLE
        out["clamp_max_elapsed_window"] = UNAVAILABLE

    try:
        out.update(_file_sizes_section(store_path, runs_path, history_path))
    except Exception:
        out["store_db_bytes"] = UNAVAILABLE
        out["runs_db_bytes"] = UNAVAILABLE
        out["history_db_bytes"] = UNAVAILABLE

    return out


async def collect_from_path(
    store_path: Path,
    runs_path: Path,
    history_path: Path | None = None,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    log_path: Path = LOG_PATH,
    now: float | None = None,
) -> dict:
    """Open store_path read-only, collect, close. The one entry point both
    `python -m lyra_core --report` and the daemon's periodic emission use
    (runtime.Runtime._emit_self_report) — never touches Runtime,
    TurnHandler, or the transport itself."""
    store_like = await open_read_only(store_path)
    try:
        return await collect_measurements(
            store_like, store_path=store_path, runs_path=runs_path,
            history_path=history_path, window_days=window_days, log_path=log_path,
            now=now,
        )
    finally:
        await store_like.db.close()


# ── rendering ────────────────────────────────────────────────────────────────

def format_log_line(measurements: dict) -> str:
    """One structured key=value line — the daemon's periodic emission
    (change 5) and this module's own field set, always in the same order."""
    parts = ["SELF_REPORT"]
    for f in FIELDS:
        v = measurements.get(f, UNAVAILABLE)
        if isinstance(v, float):
            v = f"{v:.6f}"
        parts.append(f"{f}={v}")
    return " ".join(parts)


def render(measurements: dict, window_days: int) -> str:
    """Human-readable stdout rendering for `python -m lyra_core --report`.
    Every metric is a count or a number — no prose except the fixed KNOWN
    GAPS checklist at the end (change 3)."""
    lines = [f"── lyra_core self-report (last {window_days} days) ──"]

    lines.append("\n2a. atoms")
    lines.append(f"  today               {measurements.get('atoms_today', UNAVAILABLE)}")
    lines.append(f"  window total        {measurements.get('atoms_window_total', UNAVAILABLE)}")
    lines.append(f"  total (all time)    {measurements.get('atoms_total', UNAVAILABLE)}")
    for day, count in measurements.get("_atoms_by_day", []):
        lines.append(f"    {day}  {count}")

    lines.append("\n2b. retrievability (forgetting, observed)")
    lines.append(f"  below floor ({FORGET_THRESHOLD})   {measurements.get('atoms_below_floor', UNAVAILABLE)}")
    lines.append(f"  above floor         {measurements.get('atoms_above_floor', UNAVAILABLE)}")

    lines.append("\n2c. retrieval (live self-test against window content — see module docstring)")
    lines.append(f"  assemblies          {measurements.get('assemblies_total', UNAVAILABLE)}")
    lines.append(f"  with >=1 atom       {measurements.get('assemblies_with_hit', UNAVAILABLE)}")
    lines.append(f"  vector only         {measurements.get('retrieval_vector_only', UNAVAILABLE)}")
    lines.append(f"  fts only            {measurements.get('retrieval_fts_only', UNAVAILABLE)}")
    lines.append(f"  both                {measurements.get('retrieval_both', UNAVAILABLE)}")
    lines.append(f"  neither             {measurements.get('retrieval_neither', UNAVAILABLE)}")

    lines.append("\n2d. candidates / traits")
    lines.append(f"  candidates total    {measurements.get('candidates_total', UNAVAILABLE)}")
    lines.append(f"  promoted (window)   {measurements.get('candidates_promoted_window', UNAVAILABLE)}")
    lines.append(f"  trait count         {measurements.get('trait_count', UNAVAILABLE)}")
    for name, value, conf in measurements.get("_traits", []):
        lines.append(f"    {name}: {value} (confidence={conf:.2f})" if conf is not None
                      else f"    {name}: {value} (confidence=?)")

    lines.append("\n2e. outcomes (expected: 0 until CP-D)")
    lines.append(f"  outcomes total      {measurements.get('outcomes_total', UNAVAILABLE)}")

    lines.append("\n2f. affect (temperament is a static parameter set, not a timescale)")
    for axis in ("emotion_v", "emotion_a", "mood_v", "mood_a"):
        cur = measurements.get(axis, UNAVAILABLE)
        lo = measurements.get(f"{axis}_min", UNAVAILABLE)
        hi = measurements.get(f"{axis}_max", UNAVAILABLE)
        lines.append(f"  {axis:<12} current={cur}  window min={lo}  max={hi}")
    lines.append(f"  temperament_v (accum_rate)    {measurements.get('temperament_v', UNAVAILABLE)}")
    lines.append(f"  temperament_a (emotion_decay) {measurements.get('temperament_a', UNAVAILABLE)}")
    lines.append(f"  temperament_c (mood_drift)    {measurements.get('temperament_c', UNAVAILABLE)}")

    lines.append("\n2g. dt clamp (window)")
    lines.append(f"  engagements         {measurements.get('clamp_count_window', UNAVAILABLE)}")
    lines.append(f"  largest raw elapsed {measurements.get('clamp_max_elapsed_window', UNAVAILABLE)}")

    lines.append("\n2h. store file sizes")
    lines.append(f"  store.db bytes      {measurements.get('store_db_bytes', UNAVAILABLE)}")
    lines.append(f"  runs.db bytes       {measurements.get('runs_db_bytes', UNAVAILABLE)}")
    lines.append(f"  history.db bytes    {measurements.get('history_db_bytes', UNAVAILABLE)}")

    lines.append("\nKNOWN GAPS")
    for gap in KNOWN_GAPS:
        lines.append(f"  - {gap}")

    return "\n".join(lines)
