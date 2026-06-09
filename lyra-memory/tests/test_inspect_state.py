"""Tests for lyra_memory.inspect_state — read-only DB inspector.

Seed a minimal SQLite DB (no vec extension needed — inspector is sync/read-only),
run main(db_path=...), capture stdout, assert key strings appear.
"""
from __future__ import annotations

import io
import sqlite3
import time
from contextlib import redirect_stdout
from pathlib import Path


def _seed_db(path: Path) -> None:
    """Create the minimum tables and insert test fixtures."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            trait_name     TEXT NOT NULL,
            trait_value    TEXT NOT NULL,
            evidence_count INTEGER NOT NULL DEFAULT 1,
            last_seen      REAL NOT NULL,
            category       TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS traits (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            value          TEXT NOT NULL,
            confidence     REAL NOT NULL,
            stability      TEXT NOT NULL,
            evidence_count INTEGER NOT NULL,
            updated_at     REAL NOT NULL
        );
    """)
    now = time.time()
    conn.execute(
        "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category) "
        "VALUES (?, ?, ?, ?, ?)",
        ("user_asks_questions", "asks deep questions", 3, now, "behavioral"),
    )
    conn.execute(
        "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category) "
        "VALUES (?, ?, ?, ?, ?)",
        ("night_owl", "prefers working late", 7, now, "behavioral"),
    )
    conn.execute(
        "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("curious", "explores ideas deeply", 0.85, "character", 12, now),
    )
    conn.commit()
    conn.close()


def _run(db_path: Path) -> str:
    from lyra_memory import inspect_state
    buf = io.StringIO()
    with redirect_stdout(buf):
        inspect_state.main(db_path=db_path)
    return buf.getvalue()


def test_candidates_shown_with_names_and_evidence_counts(tmp_path):
    _seed_db(tmp_path / "test.db")
    out = _run(tmp_path / "test.db")
    assert "user_asks_questions" in out
    assert "night_owl" in out
    assert "3" in out
    assert "7" in out


def test_distance_to_threshold_text(tmp_path):
    """evidence_count=3 → 2 to surface (5-3=2); count=7 → 8 to character (15-7=8)."""
    _seed_db(tmp_path / "test.db")
    out = _run(tmp_path / "test.db")
    assert "2 to surface" in out
    assert "8 to character" in out


def test_promoted_trait_shown_with_stability_and_confidence(tmp_path):
    _seed_db(tmp_path / "test.db")
    out = _run(tmp_path / "test.db")
    assert "curious" in out
    assert "character" in out
    assert "0.85" in out


def test_summary_line_counts_candidates_and_traits(tmp_path):
    _seed_db(tmp_path / "test.db")
    out = _run(tmp_path / "test.db")
    # Summary must mention 2 candidates and 1 trait
    assert "2" in out
    assert "1" in out
    lower = out.lower()
    assert "candidate" in lower
    assert "trait" in lower


def test_empty_db_prints_friendly_message_and_does_not_raise(tmp_path):
    """Non-existent or empty DB → friendly message, no exception."""
    out = _run(tmp_path / "nonexistent.db")
    lower = out.lower()
    assert "no memory" in lower or "talk to lyra" in lower
