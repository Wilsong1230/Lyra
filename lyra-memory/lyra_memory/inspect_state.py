"""lyra_memory.inspect_state — read-only state inspector.

Prints current consolidation state: candidate pool (with distance-to-threshold)
and promoted traits (with stability/confidence). Useful for watching dreaming
happen in real time.

Also offers a minimal --affect view: the last persisted AffectState snapshot
(written by CognitiveCore.stop(), restored by CognitiveCore.start()) — valence,
arousal, mood, and the temperament rates.

Usage:
    python -m lyra_memory.inspect_state
    python -m lyra_memory.inspect_state --watch          # refresh every 5s
    python -m lyra_memory.inspect_state --watch --interval 10
    python -m lyra_memory.inspect_state --truncate       # clip long values
    python -m lyra_memory.inspect_state --affect         # affect snapshot only
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional


def _distance_label(evidence_count: int, thresholds: dict[str, int]) -> str:
    """Return 'N to <tier>' for the next threshold, or 'core-eligible'."""
    ordered = sorted(thresholds.items(), key=lambda kv: kv[1])  # surface, character, core
    for tier, limit in ordered:
        if evidence_count < limit:
            return f"{limit - evidence_count} to {tier}"
    return "core-eligible"


def _truncate(text: str, width: int = 45) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def main(db_path: Optional[Path] = None, truncate: bool = False, affect: bool = False) -> None:
    from lyra_memory.config import DB_PATH, TRAIT_THRESHOLDS

    path = db_path if db_path is not None else DB_PATH

    # Open read-only; fail gracefully if file doesn't exist.
    try:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        print("no memory yet — talk to Lyra first")
        return

    try:
        if affect:
            _print_affect(conn)
        else:
            _print_report(conn, TRAIT_THRESHOLDS, truncate=truncate)
    except sqlite3.OperationalError:
        # Tables not yet created (brand-new DB with no schema).
        print("no memory yet — talk to Lyra first")
    finally:
        conn.close()


def _print_report(conn: sqlite3.Connection, thresholds: dict[str, int], truncate: bool = False) -> None:
    # ── CANDIDATES ─────────────────────────────────────────────────────────────
    rows = conn.execute(
        "SELECT id, trait_name, trait_value, evidence_count, category "
        "FROM candidates ORDER BY evidence_count DESC"
    ).fetchall()

    print("── CANDIDATES ──────────────────────────────────────────────")
    if rows:
        print(f"  {'id':>4}  {'trait_name':<30} {'cat':<12} {'evid':>4}  next milestone")
        print(f"  {'-'*4}  {'-'*30} {'-'*12} {'-'*4}  {'-'*20}")
        for cid, name, value, count, category in rows:
            dist = _distance_label(count, thresholds)
            print(f"  {cid:>4}  {name:<30} {category:<12} {count:>4}  {dist}")
            shown = _truncate(value) if truncate else value
            print(f"        │ {shown}")
    else:
        print("  (none)")

    # ── TRAITS ──────────────────────────────────────────────────────────────────
    trait_rows = conn.execute(
        "SELECT id, name, value, stability, confidence, evidence_count, updated_at "
        "FROM traits ORDER BY confidence DESC"
    ).fetchall()

    print()
    print("── TRAITS ──────────────────────────────────────────────────")
    if trait_rows:
        print(f"  {'id':>4}  {'name':<25} {'stability':<10} {'conf':>5}  {'evid':>4}  updated")
        print(f"  {'-'*4}  {'-'*25} {'-'*10} {'-'*5}  {'-'*4}  {'-'*16}")
        for tid, name, value, stability, confidence, evidence_count, updated_at in trait_rows:
            print(
                f"  {tid:>4}  {name:<25} {stability:<10} {confidence:>5.2f}  {evidence_count:>4}"
                f"  {_fmt_ts(updated_at)}"
            )
            shown = _truncate(value) if truncate else value
            print(f"        │ {shown}")
    else:
        print("  (none)")

    # ── SUMMARY ─────────────────────────────────────────────────────────────────
    stability_counts: dict[str, int] = {}
    for row in trait_rows:
        stability = row[3]
        stability_counts[stability] = stability_counts.get(stability, 0) + 1

    tier_str = "  ".join(f"{k}={v}" for k, v in stability_counts.items()) if stability_counts else ""
    n_cand = len(rows)
    n_trait = len(trait_rows)
    print()
    summary = f"{n_cand} candidate{'s' if n_cand != 1 else ''}  {n_trait} promoted trait{'s' if n_trait != 1 else ''}"
    if tier_str:
        summary += f"  ({tier_str})"
    print(f"── {summary}")


def _print_affect(conn: sqlite3.Connection) -> None:
    """Last persisted AffectState snapshot (written on CognitiveCore.stop()).

    Read-only: a single SELECT against the facts table, no writes. Does not
    reflect a currently-running session's live affect until that session
    stops (3.6 persists at start/stop, not per-tick).
    """
    print("── AFFECT (last persisted snapshot) ──────────────────────────")
    row = conn.execute(
        "SELECT value, updated_at FROM facts WHERE key = 'affect_state'"
    ).fetchone()
    if row is None:
        print("  (none yet — persisted when Lyra's core stops)")
        return

    value, updated_at = row
    state = json.loads(value)
    print(f"  valence       {state['emotion_v']:+.4f}")
    print(f"  arousal       {state['emotion_a']:+.4f}")
    print(f"  mood valence  {state['mood_v']:+.4f}")
    print(f"  mood arousal  {state['mood_a']:+.4f}")
    print(
        f"  temperament   accum_rate={state['accum_rate']:.2f}"
        f" emotion_decay={state['emotion_decay']:.2f}"
        f" mood_drift={state['mood_drift']:.2f}"
    )
    print(f"  updated       {_fmt_ts(updated_at)}")


# ── CLI entry point ───────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only inspector for Lyra's memory state."
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh the display every --interval seconds (default: off).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        metavar="N",
        help="Seconds between refreshes when --watch is active (default: 5).",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Clip long candidate/trait values instead of printing them in full (default: off).",
    )
    parser.add_argument(
        "--affect",
        action="store_true",
        help="Show only the last persisted affect snapshot (valence/arousal/mood/temperament).",
    )
    args = parser.parse_args()

    if args.watch:
        while True:
            os.system("clear")
            main(truncate=args.truncate, affect=args.affect)
            time.sleep(args.interval)
    else:
        main(truncate=args.truncate, affect=args.affect)


if __name__ == "__main__":
    _cli()
