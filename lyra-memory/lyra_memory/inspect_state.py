"""lyra_memory.inspect_state — read-only state inspector.

Prints current consolidation state: candidate pool (with distance-to-threshold)
and promoted traits (with stability/confidence). Useful for watching dreaming
happen in real time.

Usage:
    python -m lyra_memory.inspect_state
    python -m lyra_memory.inspect_state --watch          # refresh every 5s
    python -m lyra_memory.inspect_state --watch --interval 10
"""
from __future__ import annotations

import datetime
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


def main(db_path: Optional[Path] = None) -> None:
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
        _print_report(conn, TRAIT_THRESHOLDS)
    except sqlite3.OperationalError:
        # Tables not yet created (brand-new DB with no schema).
        print("no memory yet — talk to Lyra first")
    finally:
        conn.close()


def _print_report(conn: sqlite3.Connection, thresholds: dict[str, int]) -> None:
    # ── CANDIDATES ─────────────────────────────────────────────────────────────
    rows = conn.execute(
        "SELECT trait_name, trait_value, evidence_count, category "
        "FROM candidates ORDER BY evidence_count DESC"
    ).fetchall()

    print("── CANDIDATES ──────────────────────────────────────────────")
    if rows:
        print(f"  {'trait_name':<30} {'cat':<12} {'evid':>4}  next milestone")
        print(f"  {'-'*30} {'-'*12} {'-'*4}  {'-'*20}")
        for name, value, count, category in rows:
            dist = _distance_label(count, thresholds)
            print(
                f"  {name:<30} {category:<12} {count:>4}  {dist}"
                f"  │ {_truncate(value)}"
            )
    else:
        print("  (none)")

    # ── TRAITS ──────────────────────────────────────────────────────────────────
    trait_rows = conn.execute(
        "SELECT name, value, stability, confidence, evidence_count, updated_at "
        "FROM traits ORDER BY confidence DESC"
    ).fetchall()

    print()
    print("── TRAITS ──────────────────────────────────────────────────")
    if trait_rows:
        print(f"  {'name':<25} {'stability':<10} {'conf':>5}  {'evid':>4}  updated")
        print(f"  {'-'*25} {'-'*10} {'-'*5}  {'-'*4}  {'-'*16}")
        for name, value, stability, confidence, evidence_count, updated_at in trait_rows:
            print(
                f"  {name:<25} {stability:<10} {confidence:>5.2f}  {evidence_count:>4}"
                f"  {_fmt_ts(updated_at)}"
                f"  │ {_truncate(value)}"
            )
    else:
        print("  (none)")

    # ── SUMMARY ─────────────────────────────────────────────────────────────────
    stability_counts: dict[str, int] = {}
    for _, _, stability, _, _, _ in trait_rows:
        stability_counts[stability] = stability_counts.get(stability, 0) + 1

    tier_str = "  ".join(f"{k}={v}" for k, v in stability_counts.items()) if stability_counts else ""
    n_cand = len(rows)
    n_trait = len(trait_rows)
    print()
    summary = f"{n_cand} candidate{'s' if n_cand != 1 else ''}  {n_trait} promoted trait{'s' if n_trait != 1 else ''}"
    if tier_str:
        summary += f"  ({tier_str})"
    print(f"── {summary}")


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
    args = parser.parse_args()

    if args.watch:
        while True:
            os.system("clear")
            main()
            time.sleep(args.interval)
    else:
        main()


if __name__ == "__main__":
    _cli()
