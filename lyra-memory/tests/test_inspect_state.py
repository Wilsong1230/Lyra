"""Tests for lyra_memory.inspect_state — read-only DB inspector.

Seed a minimal SQLite DB (no vec extension needed — inspector is sync/read-only),
run main(db_path=...), capture stdout, assert key strings appear.
"""
from __future__ import annotations

import io
import json
import sqlite3
import time
from contextlib import redirect_stdout
from pathlib import Path

# Longer than the old truncation width (45 chars).
_LONG_VALUE = "explores ideas deeply, asks layered follow-up questions, and revisits earlier topics unprompted"


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


def _seed_long_value_db(path: Path) -> None:
    """Same schema as _seed_db, but with a candidate/trait value longer than
    the old 45-char truncation width."""
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
        ("user_asks_questions", _LONG_VALUE, 3, now, "behavioral"),
    )
    conn.execute(
        "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("curious", _LONG_VALUE, 0.85, "character", 12, now),
    )
    conn.commit()
    conn.close()


def _seed_facts(path: Path, affect: dict, key: str = "affect_state") -> None:
    """Create the facts table (matching lyra_memory.db's schema) and insert
    a JSON-serialized row, mirroring StructuredState.set_fact()."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps(affect), time.time()),
    )
    conn.commit()
    conn.close()


def _run(db_path: Path, **kwargs) -> str:
    from lyra_memory import inspect_state
    buf = io.StringIO()
    with redirect_stdout(buf):
        inspect_state.main(db_path=db_path, **kwargs)
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


# ── Part A: full id + full description ──────────────────────────────────────

def test_candidates_and_traits_show_row_id(tmp_path):
    _seed_db(tmp_path / "test.db")
    out = _run(tmp_path / "test.db")
    # Autoincrement ids: candidates 1 (user_asks_questions), 2 (night_owl);
    # traits 1 (curious).
    assert "1  user_asks_questions" in out
    assert "2  night_owl" in out
    assert "1  curious" in out


def test_long_values_shown_in_full_by_default(tmp_path):
    _seed_long_value_db(tmp_path / "test.db")
    out = _run(tmp_path / "test.db")
    assert _LONG_VALUE in out
    assert "…" not in out


def test_truncate_flag_clips_long_values(tmp_path):
    _seed_long_value_db(tmp_path / "test.db")
    out = _run(tmp_path / "test.db", truncate=True)
    assert _LONG_VALUE not in out
    assert "…" in out


# ── Part B: minimal live affect readout ──────────────────────────────────────

_AFFECT_FIXTURE = {
    "accum_rate": 1.0,
    "emotion_decay": 2.0,
    "mood_drift": 0.2,
    "emotion_v": -0.1234,
    "emotion_a": 0.5678,
    "mood_v": -0.01,
    "mood_a": 0.02,
    "encourage_strength": 0.0,
    "encourage_remaining": 0.0,
}


def test_affect_view_shows_valence_arousal_and_mood(tmp_path):
    db = tmp_path / "test.db"
    _seed_facts(db, _AFFECT_FIXTURE)

    out = _run(db, affect=True)

    assert "-0.1234" in out
    assert "0.5678" in out
    assert "-0.0100" in out
    assert "0.0200" in out


def test_affect_view_shows_temperament_rates(tmp_path):
    db = tmp_path / "test.db"
    _seed_facts(db, _AFFECT_FIXTURE)

    out = _run(db, affect=True)

    assert "accum_rate=1.00" in out
    assert "emotion_decay=2.00" in out
    assert "mood_drift=0.20" in out


def test_affect_view_is_read_only(tmp_path):
    """Reading the affect snapshot must not mutate the persisted facts row."""
    db = tmp_path / "test.db"
    _seed_facts(db, _AFFECT_FIXTURE)

    def _read_fact():
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT value, updated_at FROM facts WHERE key='affect_state'").fetchone()
        conn.close()
        return row

    before = _read_fact()
    _run(db, affect=True)
    after = _read_fact()

    assert before == after


def test_affect_view_with_no_persisted_state_yet(tmp_path):
    """facts table exists but has no affect_state row — friendly message."""
    db = tmp_path / "test.db"
    _seed_facts(db, _AFFECT_FIXTURE, key="some_other_fact")

    out = _run(db, affect=True)

    assert "none" in out.lower()


def test_affect_view_falls_back_when_facts_table_missing(tmp_path):
    """No facts table at all (memory never started) — friendly fallback."""
    _seed_db(tmp_path / "test.db")

    out = _run(tmp_path / "test.db", affect=True)

    lower = out.lower()
    assert "no memory" in lower or "talk to lyra" in lower
