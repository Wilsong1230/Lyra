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

CP-C found `context_log` always empty: the daemon's turn path called
`lyra_memory.store.context.build_context()` directly rather than through
`Store.ingest_turn()`, the only thing that ever writes it. CP-D.0 fixed the
wiring (interface.py's CognitiveCore.ingest_exchange -> Store.ingest_turn)
— `context_log` now has real rows, and this module reports on it in two
genuinely distinct ways, per change 6, each labeled separately in the
output:

  - `logged_*`: read directly from `context_log` rows in the window — a
    historical tally of what real turns actually retrieved (and how each
    one split across {vector, FTS}), including turns where retrieval found
    nothing at all (an empty `atom_ids` shows up as a row with
    `logged_empty_window` counted, not as an absent row).
  - `selftest_*`: the live, read-only self-test this module has always
    performed (CP-C) — real atom text from the window used as query
    strings against the current store, right now, at report time. Kept
    deliberately (change 6): it answers "is retrieval healthy against
    current content", which a historical tally can't — a store that has
    only ever been queried with bad search terms would show clean
    `logged_*` numbers (every real query got what it asked for) while
    `selftest_*` against the same content might reveal it.

CP-J: a turn's retrieval may now iterate (bounded by RETRIEVAL_PASS_CAP).
`context_log` still gets exactly one row per turn (unchanged); `outcomes`
gets one row per pass. Every field whose meaning shifted from "per turn" to
"per pass" as a result was renamed rather than left to mean something new
under its old name -- see FIELDS' inline comments and DECISIONS.md, CP-J
change 7. `retrieval_pass_distribution_window` and `retrieval_cap_hit_window`
(change 8) are new, DB-derived from grouping retrieval-kind `outcomes` rows
by shared `context_log_id`.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from lyra_core.config import LOG_PATH
from lyra_memory.config import FORGET_THRESHOLD, TRAIT_THRESHOLDS
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
    # CP-D.0: two distinct retrieval measurements, not one — see module
    # docstring. "logged_*" is a historical tally read from context_log;
    # "selftest_*" is the live, read-only self-test (CP-C).
    "logged_assemblies_window", "logged_with_hit_window", "logged_empty_window",
    "logged_declined_window",
    "logged_vector_only_window", "logged_fts_only_window",
    "logged_both_window", "logged_neither_window",
    "selftest_assemblies", "selftest_with_hit",
    "selftest_vector_only", "selftest_fts_only",
    "selftest_both", "selftest_neither",
    # CP-D: the intent loop (change 7) — log-derived counts (runtime.py's
    # six markers) plus the outcomes/candidates rows they produced.
    # CP-J change 7: a turn's retrieval may now iterate, so INTENT_PRODUCED/
    # INTENT_EXECUTED fire once per PASS, not once per turn — renamed to
    # retrieval_passes_produced_window/retrieval_passes_executed_window so
    # the field states what it now counts rather than silently meaning
    # something different under its old name. intents_declined_window keeps
    # its name: a turn either attempts retrieval (>=1 pass) or declines it
    # once, so decline stays turn-scoped, unchanged by CP-J.
    # consolidator_fired_window and candidates_created_window_log also now
    # fire once per pass (CONSOLIDATOR_FIRED/CANDIDATE_CREATED are tied 1:1
    # to each pass's own outcome_id) — left under their existing names:
    # they always counted "how many times this step ran," which was
    # turn-scoped only because a turn was always exactly one pass before
    # CP-J, not because the field promised turn-scoping (see DECISIONS.md).
    "retrieval_passes_produced_window", "retrieval_passes_executed_window",
    "intents_declined_window",
    "outcomes_total",
    "consolidator_fired_window",
    "candidates_created_window_log", "candidates_created_window",
    # CP-J change 8: pass-count distribution (turns by how many passes their
    # retrieval took) and how many turns hit RETRIEVAL_PASS_CAP — both
    # DB-derived from outcomes.environment's kind=retrieval;...;pass=N
    # field, grouped by shared context_log_id, per CP-J's requirement that
    # pass count be recoverable from the store alone.
    "retrieval_pass_distribution_window", "retrieval_cap_hit_window",
    # CP-E: candidates by GROUP, not a bare count — dedup now collapses
    # matching candidates into one row (see candidate_pool.py), so a row is
    # already a distinct, post-dedup group; these three make the collapse
    # itself visible rather than reporting only its result.
    "candidates_total", "candidates_total_evidence",
    "candidates_largest_evidence", "candidates_singleton_count",
    "candidates_promoted_window",
    "trait_count", "trait_confidence_mean",
    "emotion_v", "emotion_a", "mood_v", "mood_a",
    "temperament_v", "temperament_a", "temperament_c",
    "emotion_v_min", "emotion_v_max", "emotion_a_min", "emotion_a_max",
    "mood_v_min", "mood_v_max", "mood_a_min", "mood_a_max",
    "clamp_count_window", "clamp_max_elapsed_window",
    "store_db_bytes", "runs_db_bytes", "history_db_bytes",
    # CP-G change 8: commits indexed (all-time, like atoms_total — indexing
    # is an explicit one-off act, not a per-window flow), the repo-query
    # intent's own produced/executed counts (log-derived, its own markers —
    # see runtime.py's REPO_QUERY_PRODUCED/EXECUTED comment for why these
    # are not folded into retrieval_passes_produced_window/
    # retrieval_passes_executed_window, CP-J's renamed fields),
    # and the citation check itself: how many were checked, how many
    # existed, how many did not, and the false rate as a number (change 8's
    # own requirement — "The false rate must be a visible number").
    "commits_indexed",
    "repo_query_produced_window", "repo_query_executed_window",
    "repo_citations_checked_window", "repo_citations_hit_window",
    "repo_citations_miss_window", "repo_citation_false_rate_window",
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


def classify_misses(misses: list[str]) -> tuple[bool, bool]:
    """(vector_hit, fts_hit) from a ContextResult's misses list — a miss
    entry means that path found nothing; its absence means it did.

    Shared with runtime.py (TurnHandler._retrieval_path): the same
    classification decides both what gets logged (INTENT_EXECUTED,
    OUTCOME_RECORDED) and what gets reported (this module's logged_*/
    selftest_* sections) — one place, not two that could drift apart.
    """
    vector_missed = any(m.startswith("semantic:") for m in misses)
    fts_missed = any(m.startswith("lexical:") for m in misses)
    return not vector_missed, not fts_missed


def path_label(vector_hit: bool, fts_hit: bool) -> str:
    """"vector" | "fts" | "both" | "neither" — the string form of
    classify_misses()'s pair, for anywhere (a log line, an outcomes row)
    that wants one label rather than two booleans."""
    if vector_hit and fts_hit:
        return "both"
    if vector_hit:
        return "vector"
    if fts_hit:
        return "fts"
    return "neither"


async def _selftest_retrieval_section(
    store_like: _ReadOnlyStore, db: aiosqlite.Connection, window_start: float
) -> dict:
    """Live, read-only retrieval self-test (CP-C) — see module docstring for
    how this differs from _context_log_section's historical tally."""
    async with db.execute(
        "SELECT DISTINCT text FROM atoms WHERE ts >= ? AND speaker = 'wilson'"
        " AND length(text) > 0 ORDER BY id DESC LIMIT ?",
        (window_start, _ASSEMBLY_SAMPLE_LIMIT),
    ) as cur:
        queries = [r[0] for r in await cur.fetchall()]

    vector_only = fts_only = both = neither = with_hit = 0
    for q in queries:
        ctx = await build_context(store_like, query=q, recent_turns=0)
        vector_hit, fts_hit = classify_misses(ctx.misses)
        label = path_label(vector_hit, fts_hit)
        if label == "both":
            both += 1
        elif label == "vector":
            vector_only += 1
        elif label == "fts":
            fts_only += 1
        else:
            neither += 1
        if vector_hit or fts_hit:
            with_hit += 1

    return {
        "selftest_assemblies": len(queries), "selftest_with_hit": with_hit,
        "selftest_vector_only": vector_only, "selftest_fts_only": fts_only,
        "selftest_both": both, "selftest_neither": neither,
    }


async def _context_log_section(db: aiosqlite.Connection, window_start: float) -> dict:
    """A historical tally read straight from context_log (CP-D.0, change 6)
    — what CognitiveCore.ingest_exchange actually logged for real turns in
    the window, not a measurement performed now. An assembly that returned
    nothing is a row with an empty atom_ids list, not a missing row — that
    row is what logged_empty_window counts.

    CP-D: a row whose misses carries interface.DECLINED_MARKER is a turn
    that never attempted retrieval at all (change 3) — counted separately
    as logged_declined_window, not folded into vector/fts/both/neither
    (which all describe retrieval that ran)."""
    from lyra_core.interface import DECLINED_MARKER

    async with db.execute(
        "SELECT atom_ids, misses FROM context_log WHERE ts >= ?", (window_start,)
    ) as cur:
        rows = await cur.fetchall()

    vector_only = fts_only = both = neither = with_hit = empty = declined = 0
    for atom_ids_json, misses_json in rows:
        atom_ids = json.loads(atom_ids_json) if atom_ids_json else []
        misses = json.loads(misses_json) if misses_json else []
        if DECLINED_MARKER in misses:
            declined += 1
            continue
        vector_hit, fts_hit = classify_misses(misses)
        label = path_label(vector_hit, fts_hit)
        if label == "both":
            both += 1
        elif label == "vector":
            vector_only += 1
        elif label == "fts":
            fts_only += 1
        else:
            neither += 1
        if atom_ids:
            with_hit += 1
        else:
            empty += 1

    return {
        "logged_assemblies_window": len(rows), "logged_with_hit_window": with_hit,
        "logged_empty_window": empty, "logged_declined_window": declined,
        "logged_vector_only_window": vector_only, "logged_fts_only_window": fts_only,
        "logged_both_window": both, "logged_neither_window": neither,
    }


# CP-F change 7 / CP-H change 6: how far evidence_count sits from the next
# TRAIT_THRESHOLDS value it has not yet crossed (surface/character/core,
# ascending). 0 means already at or above the highest tier (core) —
# nothing left to cross. Shared by the all-candidates gap list
# (_candidates_traits_section) and the repo-citation-only breakdown
# (_repo_citation_candidates_section) — one formula, not two copies of it.
_SORTED_TRAIT_THRESHOLDS = sorted(TRAIT_THRESHOLDS.values())


def _gap_to_threshold(evidence_count: int) -> int:
    remaining = [t - evidence_count for t in _SORTED_TRAIT_THRESHOLDS if t > evidence_count]
    return remaining[0] if remaining else 0


async def _candidates_traits_section(db: aiosqlite.Connection, window_start: float) -> dict:
    # CP-E: candidates are deduplicated at write time (candidate_pool.py) —
    # a row IS a distinct, post-collapse group, evidence_count IS how much
    # evidence collapsed into it. "candidates_total" therefore already means
    # distinct-candidate-groups, not a raw per-turn row count; the three new
    # fields make the collapse legible instead of reporting only its result.
    async with db.execute(
        "SELECT COUNT(*), COALESCE(SUM(evidence_count), 0), COALESCE(MAX(evidence_count), 0),"
        " COALESCE(SUM(CASE WHEN evidence_count = 1 THEN 1 ELSE 0 END), 0) FROM candidates"
    ) as cur:
        candidates_total, total_evidence, largest_evidence, singleton_count = await cur.fetchone()
    # CP-D: candidates still has no created_ts (CP-C's finding stands).
    # CP-D's comment here claimed last_seen doubles as a creation time
    # because nothing ever updated an existing row — CP-E's dedup makes
    # that false: last_seen now also moves every time a repeat observation
    # STRENGTHENS an existing candidate. So this is now "created or
    # strengthened in window", not "created in window" — still a useful
    # cross-check against the log-derived candidates_created_window_log
    # (which counts only fresh CANDIDATE_CREATED-logged inserts... except
    # runtime.py's CANDIDATE_CREATED log line still fires on every
    # non-None return from the consolidator, i.e. on strengthen too — see
    # DECISIONS.md, CP-E, for why that name is now imprecise in both
    # places and was left alone rather than touched outside interface.py).
    async with db.execute(
        "SELECT COUNT(*) FROM candidates WHERE last_seen >= ?", (window_start,)
    ) as cur:
        candidates_created_window = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT COUNT(*) FROM trait_history WHERE event = 'promoted' AND ts >= ?",
        (window_start,),
    ) as cur:
        promoted = (await cur.fetchone())[0]
    async with db.execute("SELECT name, value, confidence FROM traits") as cur:
        rows = await cur.fetchall()
    confidences = [r[2] for r in rows if r[2] is not None]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

    # CP-F change 7: "make an impending promotion visible before it
    # happens" — for every candidate, regardless of category.
    async with db.execute(
        "SELECT trait_name, evidence_count FROM candidates ORDER BY evidence_count DESC"
    ) as cur:
        cand_rows = await cur.fetchall()
    candidate_gaps = [(name, ec, _gap_to_threshold(ec)) for name, ec in cand_rows]

    return {
        "candidates_total": candidates_total,
        "candidates_total_evidence": total_evidence,
        "candidates_largest_evidence": largest_evidence,
        "candidates_singleton_count": singleton_count,
        "candidates_created_window": candidates_created_window,
        "candidates_promoted_window": promoted,
        "trait_count": len(rows),
        "trait_confidence_mean": mean_conf,
        "_traits": [(r[0], r[1], r[2]) for r in rows],
        "_candidate_gaps": candidate_gaps,
    }


async def _outcomes_section(db: aiosqlite.Connection) -> dict:
    async with db.execute("SELECT COUNT(*) FROM outcomes") as cur:
        total = (await cur.fetchone())[0]
    return {"outcomes_total": total}


_REPO_OUTCOME_ENV_RE = re.compile(r"hits=(\d+);misses=(\d+)")


async def _repo_section(db: aiosqlite.Connection, window_start: float) -> dict:
    """CP-G change 8. `commits_indexed` is all-time (repo_index.py is a
    one-off explicit act, not a window-scoped flow, same reasoning as
    atoms_total). The citation counts read straight from `outcomes.
    environment` — CognitiveCore.record_repo_query_outcome() packs
    `kind=repo_query;context_log_id=...;hits=N;misses=N` into the same
    free-text column record_retrieval_outcome() already uses for its own
    extra fields (OUT OF SCOPE forbids a schema change to give repo-query
    outcomes real columns); the `kind=repo_query` prefix is what lets this
    query select just these rows out of the one shared `outcomes` table.
    """
    async with db.execute(
        "SELECT COUNT(*) FROM facts WHERE source_kind = 'repo_commit'"
    ) as cur:
        commits_indexed = (await cur.fetchone())[0]

    async with db.execute(
        "SELECT environment FROM outcomes"
        " WHERE environment LIKE 'kind=repo_query%' AND ts >= ?",
        (window_start,),
    ) as cur:
        rows = await cur.fetchall()

    hits = misses = 0
    for (env,) in rows:
        m = _REPO_OUTCOME_ENV_RE.search(env or "")
        if m:
            hits += int(m.group(1))
            misses += int(m.group(2))
    checked = hits + misses
    false_rate = misses / checked if checked else 0.0

    return {
        "commits_indexed": commits_indexed,
        "repo_citations_checked_window": checked,
        "repo_citations_hit_window": hits,
        "repo_citations_miss_window": misses,
        "repo_citation_false_rate_window": false_rate,
    }


async def _repo_citation_candidates_section(db: aiosqlite.Connection) -> dict:
    """CP-H change 6: candidates and evidence PER CITATION LABEL, broken
    out from the all-categories `_candidate_gaps` list above (which already
    includes these rows, undifferentiated) — DONE-WHEN asks specifically to
    see the four-label breakdown, not just find these rows mixed into the
    generic candidate list. `_gap_to_threshold` is the identical formula
    `_candidates_traits_section` uses, not a second one."""
    async with db.execute(
        "SELECT trait_name, evidence_count FROM candidates"
        " WHERE category = 'repo_citation' ORDER BY evidence_count DESC"
    ) as cur:
        rows = await cur.fetchall()
    return {"_repo_citation_labels": [(name, ec, _gap_to_threshold(ec)) for name, ec in rows]}


# CP-J change 8. Matches record_retrieval_outcome()'s environment string —
# "kind=retrieval;context_log_id=N;atom_count=N;pass=N" — pulling out
# context_log_id and pass so distinct passes of one turn can be grouped by
# their shared context_log_id without touching context_log itself (which
# stays one row per turn by design; see DECISIONS.md CP-J).
_RETRIEVAL_OUTCOME_ENV_RE = re.compile(r"kind=retrieval;context_log_id=(\d+);atom_count=\d+;pass=(\d+)")


async def _retrieval_pass_distribution_section(db: aiosqlite.Connection, window_start: float) -> dict:
    """CP-J change 8: turns by pass count, plus how many hit the cap.

    Pass count for a turn = the number of retrieval-kind outcome rows that
    share its context_log_id (each pass writes exactly one, per change 6).
    Rows written before CP-J have no "kind=retrieval;" prefix and no
    "pass=" field at all — they simply don't match this regex, so a window
    straddling the CP-J rollout undercounts only its pre-CP-J turns, never
    misattributes them to a pass count.
    """
    from lyra_core.config import RETRIEVAL_PASS_CAP

    async with db.execute(
        "SELECT environment FROM outcomes"
        " WHERE environment LIKE 'kind=retrieval%' AND ts >= ?",
        (window_start,),
    ) as cur:
        rows = await cur.fetchall()

    passes_by_turn: dict[str, int] = {}
    for (env,) in rows:
        m = _RETRIEVAL_OUTCOME_ENV_RE.search(env or "")
        if not m:
            continue
        context_log_id = m.group(1)
        passes_by_turn[context_log_id] = passes_by_turn.get(context_log_id, 0) + 1

    distribution: dict[int, int] = {}
    cap_hit = 0
    for pass_count in passes_by_turn.values():
        distribution[pass_count] = distribution.get(pass_count, 0) + 1
        if pass_count >= RETRIEVAL_PASS_CAP:
            cap_hit += 1

    dist_str = ";".join(f"{k}={v}" for k, v in sorted(distribution.items()))
    return {
        "retrieval_pass_distribution_window": dist_str if dist_str else "(none)",
        "retrieval_cap_hit_window": cap_hit,
    }


async def _affect_section(db: aiosqlite.Connection, log_path: Path, window_start: float) -> dict:
    async with db.execute(
        "SELECT text FROM facts WHERE subject = '_lyra_internal_affect_state'"
        " AND valid_until IS NULL ORDER BY id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()

    result: dict = {}
    if row is not None:
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


# CP-D: the intent loop has no table of its own to count from — the six
# markers in runtime.py (INTENT_PRODUCED/EXECUTED/DECLINED,
# CONSOLIDATOR_FIRED, CANDIDATE_CREATED) are the only record of "did this
# step happen," the same way DT_CLAMP_ENGAGED already was the only record
# of a clamp. OUTCOME_RECORDED is deliberately not parsed here — the
# outcomes table itself is the authoritative count (_outcomes_section);
# parsing the log for it too would just be two ways to get one number.
_LOOP_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \S+ lyra_core\.runtime: "
    r"(?P<marker>INTENT_PRODUCED|INTENT_EXECUTED|INTENT_DECLINED|CONSOLIDATOR_FIRED|CANDIDATE_CREATED)\b"
)


def _loop_log_section(log_path: Path, window_start: float) -> dict:
    # CP-J change 7: INTENT_PRODUCED/EXECUTED now fire once per pass, so the
    # counts below are pass counts, not turn counts — see FIELDS' comment.
    fields = (
        "retrieval_passes_produced_window", "retrieval_passes_executed_window",
        "intents_declined_window",
        "consolidator_fired_window", "candidates_created_window_log",
    )
    if not log_path.is_file():
        return {f: UNAVAILABLE for f in fields}

    counts = {
        "INTENT_PRODUCED": 0, "INTENT_EXECUTED": 0, "INTENT_DECLINED": 0,
        "CONSOLIDATOR_FIRED": 0, "CANDIDATE_CREATED": 0,
    }
    for line in log_path.read_text(errors="replace").splitlines():
        m = _LOOP_LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if ts < window_start:
            continue
        counts[m.group("marker")] += 1

    return {
        "retrieval_passes_produced_window": counts["INTENT_PRODUCED"],
        "retrieval_passes_executed_window": counts["INTENT_EXECUTED"],
        "intents_declined_window": counts["INTENT_DECLINED"],
        "consolidator_fired_window": counts["CONSOLIDATOR_FIRED"],
        "candidates_created_window_log": counts["CANDIDATE_CREATED"],
    }


# CP-G change 8: repo_query's own two markers (runtime.py's
# REPO_QUERY_PRODUCED/EXECUTED) — a separate regex/section rather than
# folding them into _LOOP_LINE_RE/_loop_log_section above, so
# retrieval_passes_produced_window/retrieval_passes_executed_window (CP-D's
# own fields, about retrieval, renamed by CP-J change 7) stay exactly what
# they have always measured.
_REPO_LOOP_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \S+ lyra_core\.runtime: "
    r"(?P<marker>REPO_QUERY_PRODUCED|REPO_QUERY_EXECUTED)\b"
)


def _repo_loop_log_section(log_path: Path, window_start: float) -> dict:
    fields = ("repo_query_produced_window", "repo_query_executed_window")
    if not log_path.is_file():
        return {f: UNAVAILABLE for f in fields}

    counts = {"REPO_QUERY_PRODUCED": 0, "REPO_QUERY_EXECUTED": 0}
    for line in log_path.read_text(errors="replace").splitlines():
        m = _REPO_LOOP_LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if ts < window_start:
            continue
        counts[m.group("marker")] += 1

    return {
        "repo_query_produced_window": counts["REPO_QUERY_PRODUCED"],
        "repo_query_executed_window": counts["REPO_QUERY_EXECUTED"],
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
        "context_log",
        ("logged_assemblies_window", "logged_with_hit_window", "logged_empty_window",
         "logged_declined_window",
         "logged_vector_only_window", "logged_fts_only_window",
         "logged_both_window", "logged_neither_window"),
        _context_log_section(db, window_start),
    )
    await _section(
        "selftest_retrieval",
        ("selftest_assemblies", "selftest_with_hit", "selftest_vector_only",
         "selftest_fts_only", "selftest_both", "selftest_neither"),
        _selftest_retrieval_section(store_like, db, window_start),
    )
    await _section(
        "candidates_traits",
        ("candidates_total", "candidates_total_evidence", "candidates_largest_evidence",
         "candidates_singleton_count", "candidates_created_window", "candidates_promoted_window",
         "trait_count", "trait_confidence_mean"),
        _candidates_traits_section(db, window_start),
    )
    await _section("outcomes", ("outcomes_total",), _outcomes_section(db))
    await _section(
        "retrieval_pass_distribution",
        ("retrieval_pass_distribution_window", "retrieval_cap_hit_window"),
        _retrieval_pass_distribution_section(db, window_start),
    )
    await _section(
        "repo",
        ("commits_indexed", "repo_citations_checked_window",
         "repo_citations_hit_window", "repo_citations_miss_window",
         "repo_citation_false_rate_window"),
        _repo_section(db, window_start),
    )
    await _section(
        "repo_citation_candidates", (), _repo_citation_candidates_section(db),
    )
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
        out.update(_loop_log_section(log_path, window_start))
    except Exception:
        for f in (
            "retrieval_passes_produced_window", "retrieval_passes_executed_window",
            "intents_declined_window",
            "consolidator_fired_window", "candidates_created_window_log",
        ):
            out[f] = UNAVAILABLE

    try:
        out.update(_repo_loop_log_section(log_path, window_start))
    except Exception:
        out["repo_query_produced_window"] = UNAVAILABLE
        out["repo_query_executed_window"] = UNAVAILABLE

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

    lines.append("\n2c. retrieval — two distinct measurements (see module docstring)")
    lines.append("  logged (context_log rows in the window — what real turns actually retrieved):")
    lines.append(f"    assemblies        {measurements.get('logged_assemblies_window', UNAVAILABLE)}")
    lines.append(f"    with >=1 atom     {measurements.get('logged_with_hit_window', UNAVAILABLE)}")
    lines.append(f"    empty (0 atoms)   {measurements.get('logged_empty_window', UNAVAILABLE)}")
    lines.append(f"    declined          {measurements.get('logged_declined_window', UNAVAILABLE)}")
    lines.append(f"    vector only       {measurements.get('logged_vector_only_window', UNAVAILABLE)}")
    lines.append(f"    fts only          {measurements.get('logged_fts_only_window', UNAVAILABLE)}")
    lines.append(f"    both              {measurements.get('logged_both_window', UNAVAILABLE)}")
    lines.append(f"    neither           {measurements.get('logged_neither_window', UNAVAILABLE)}")
    lines.append("  selftest (live, read-only probe against current window content, run now):")
    lines.append(f"    assemblies        {measurements.get('selftest_assemblies', UNAVAILABLE)}")
    lines.append(f"    with >=1 atom     {measurements.get('selftest_with_hit', UNAVAILABLE)}")
    lines.append(f"    vector only       {measurements.get('selftest_vector_only', UNAVAILABLE)}")
    lines.append(f"    fts only          {measurements.get('selftest_fts_only', UNAVAILABLE)}")
    lines.append(f"    both              {measurements.get('selftest_both', UNAVAILABLE)}")
    lines.append(f"    neither           {measurements.get('selftest_neither', UNAVAILABLE)}")

    lines.append("\n2d. candidates / traits (grouped — a row is a distinct, post-dedup candidate)")
    lines.append(f"  distinct candidates {measurements.get('candidates_total', UNAVAILABLE)}")
    lines.append(f"  total evidence      {measurements.get('candidates_total_evidence', UNAVAILABLE)}")
    lines.append(f"  largest evidence    {measurements.get('candidates_largest_evidence', UNAVAILABLE)}")
    lines.append(f"  singletons          {measurements.get('candidates_singleton_count', UNAVAILABLE)}")
    lines.append(f"  created/strengthened (window)  {measurements.get('candidates_created_window', UNAVAILABLE)}")
    lines.append(f"  promoted (window)   {measurements.get('candidates_promoted_window', UNAVAILABLE)}")
    lines.append(f"  trait count         {measurements.get('trait_count', UNAVAILABLE)}")
    for name, value, conf in measurements.get("_traits", []):
        lines.append(f"    {name}: {value} (confidence={conf:.2f})" if conf is not None
                      else f"    {name}: {value} (confidence=?)")
    lines.append("  candidates — evidence vs. next threshold not yet crossed (CP-F):")
    for name, evidence_count, gap in measurements.get("_candidate_gaps", []):
        if gap == 0:
            lines.append(f"    {name}: evidence={evidence_count} (at or above the highest tier)")
        else:
            lines.append(f"    {name}: evidence={evidence_count}  gap={gap}")

    lines.append("\n2e. outcomes")
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

    lines.append("\n2i. intent loop (CP-D — from the daemon log's six markers; window)")
    lines.append("  retrieval may iterate (CP-J) — produced/executed below are PASS counts, not turn counts")
    lines.append(f"  passes produced     {measurements.get('retrieval_passes_produced_window', UNAVAILABLE)}")
    lines.append(f"  passes executed     {measurements.get('retrieval_passes_executed_window', UNAVAILABLE)}")
    lines.append(f"  declined (turns)    {measurements.get('intents_declined_window', UNAVAILABLE)}")
    lines.append(f"  consolidator fired  {measurements.get('consolidator_fired_window', UNAVAILABLE)}  (per pass)")
    lines.append(f"  candidates created  {measurements.get('candidates_created_window_log', UNAVAILABLE)}"
                  f"  (candidates table, same window: {measurements.get('candidates_created_window', UNAVAILABLE)})")
    lines.append(f"  pass distribution (turns by pass count)  {measurements.get('retrieval_pass_distribution_window', UNAVAILABLE)}")
    lines.append(f"  turns hitting the cap  {measurements.get('retrieval_cap_hit_window', UNAVAILABLE)}")

    lines.append("\n2j. repo index (CP-G)")
    lines.append(f"  commits indexed     {measurements.get('commits_indexed', UNAVAILABLE)}")
    lines.append(f"  repo_query produced {measurements.get('repo_query_produced_window', UNAVAILABLE)}  (window)")
    lines.append(f"  repo_query executed {measurements.get('repo_query_executed_window', UNAVAILABLE)}  (window)")
    lines.append(f"  citations checked   {measurements.get('repo_citations_checked_window', UNAVAILABLE)}  (window)")
    lines.append(f"  citations existed   {measurements.get('repo_citations_hit_window', UNAVAILABLE)}  (window)")
    lines.append(f"  citations did not   {measurements.get('repo_citations_miss_window', UNAVAILABLE)}  (window)")
    false_rate = measurements.get('repo_citation_false_rate_window', UNAVAILABLE)
    false_rate_str = f"{false_rate:.4f}" if isinstance(false_rate, float) else str(false_rate)
    lines.append(f"  citation false rate {false_rate_str}  (window)")

    lines.append("\n2k. repo citation candidates, per label (CP-H)")
    labels = measurements.get("_repo_citation_labels", [])
    if not labels:
        lines.append("  (none yet)")
    for name, evidence_count, gap in labels:
        if gap == 0:
            lines.append(f"  {name}: evidence={evidence_count} (at or above the highest tier)")
        else:
            lines.append(f"  {name}: evidence={evidence_count}  gap={gap}")

    lines.append("\nKNOWN GAPS")
    for gap in KNOWN_GAPS:
        lines.append(f"  - {gap}")

    return "\n".join(lines)
