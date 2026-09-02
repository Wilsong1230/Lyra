"""Tests for lyra_core.report — read-only self-report (CP-C).

Async tests here run under pytest-asyncio (asyncio_mode = "auto",
lyra_ai/pyproject.toml — already configured before this checkpoint; see
DECISIONS.md). No mocks for the store: a real Store, a real tmp sqlite
file, real atoms — report.py's whole point is querying real data.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lyra_core.report import (
    FIELDS,
    KNOWN_GAPS,
    UNAVAILABLE,
    _classify_misses,
    collect_from_path,
    collect_measurements,
    format_log_line,
    open_read_only,
    render,
)
from lyra_memory.store import Store


# ── _classify_misses ─────────────────────────────────────────────────────────

def test_classify_misses_both_hit_when_no_miss_recorded():
    assert _classify_misses([]) == (True, True)


def test_classify_misses_vector_missed():
    assert _classify_misses(["semantic: nothing above similarity floor 0.35"]) == (False, True)


def test_classify_misses_lexical_missed():
    assert _classify_misses(["lexical: no BM25 match"]) == (True, False)


def test_classify_misses_neither_hit():
    assert _classify_misses(["semantic: nothing above similarity floor 0.35",
                              "lexical: query had no selective tokens"]) == (False, False)


def test_classify_misses_ignores_unrelated_miss_strings():
    """A "recall: ..." miss (temporal withheld) says nothing about either
    path on its own — must not be misread as a vector or lexical miss."""
    assert _classify_misses(["recall: no relevant hit on any path; temporal pool withheld"]) == (True, True)


# ── format_log_line / render ────────────────────────────────────────────────

def test_format_log_line_covers_every_stable_field():
    line = format_log_line({})
    assert line.startswith("SELF_REPORT ")
    for f in FIELDS:
        assert f"{f}=" in line


def test_format_log_line_uses_unavailable_for_missing_fields():
    line = format_log_line({"atoms_today": 5})
    assert "atoms_today=5" in line
    assert f"atoms_total={UNAVAILABLE}" in line


def test_format_log_line_formats_floats_with_fixed_precision():
    line = format_log_line({"emotion_v": -0.1})
    assert "emotion_v=-0.100000" in line


def test_render_includes_every_known_gap_verbatim():
    text = render({}, window_days=7)
    for gap in KNOWN_GAPS:
        assert gap in text


def test_render_shows_window_days():
    text = render({}, window_days=30)
    assert "last 30 days" in text


def test_render_never_raises_on_a_fully_unavailable_measurement_set():
    """Mirrors the daemon's own fallback (open failed -> {}) — every field
    read via .get(..., UNAVAILABLE), so an empty dict must render cleanly."""
    text = render({}, window_days=7)
    assert UNAVAILABLE in text


# ── collect_measurements / collect_from_path — real Store, real atoms ─────────

@pytest.fixture
async def store(tmp_path: Path) -> Store:
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def test_collect_measurements_counts_atoms_written_today(store, tmp_path):
    await store.append_atom(speaker="wilson", source="cli", text="hello there")
    await store.append_atom(speaker="lyra", source="cli", text="hi wilson")

    m = await collect_measurements(
        store, store_path=tmp_path / "store.db", runs_path=store.runs_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["atoms_today"] == 2
    assert m["atoms_total"] == 2


async def test_collect_measurements_splits_retrievability_by_floor(store, tmp_path):
    id1 = await store.append_atom(speaker="wilson", source="cli", text="a")
    id2 = await store.append_atom(speaker="wilson", source="cli", text="b")
    await store.db.execute("UPDATE atoms SET retrievability = 0.05 WHERE id = ?", (id1,))
    await store.db.execute("UPDATE atoms SET retrievability = 0.9 WHERE id = ?", (id2,))
    await store.db.commit()

    m = await collect_measurements(
        store, store_path=tmp_path / "store.db", runs_path=store.runs_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["atoms_below_floor"] == 1
    assert m["atoms_above_floor"] == 1  # NULL retrievability counts as above


async def test_collect_measurements_retrieval_self_test_finds_verbatim_match(store, tmp_path):
    """Using an atom's own text as the query — both paths should hit
    (exact FTS match, and cosine ~1.0 under any embedder)."""
    await store.append_atom(speaker="wilson", source="cli", text="a distinctive sentence about retrieval")

    m = await collect_measurements(
        store, store_path=tmp_path / "store.db", runs_path=store.runs_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["assemblies_total"] == 1
    assert m["assemblies_with_hit"] == 1
    assert m["retrieval_both"] == 1
    assert m["retrieval_neither"] == 0


async def test_collect_measurements_outcomes_zero_on_a_fresh_store(store, tmp_path):
    m = await collect_measurements(
        store, store_path=tmp_path / "store.db", runs_path=store.runs_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["outcomes_total"] == 0


async def test_collect_measurements_current_affect_from_persisted_fact(store, tmp_path):
    import json
    now = time.time()
    await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
        " VALUES (?, '_lyra_internal_affect_state', ?, 'inferred', 1.0, ?)",
        (now, json.dumps({"emotion_v": 0.25, "emotion_a": -0.1, "mood_v": 0.0, "mood_a": 0.0}), now),
    )
    await store.db.commit()

    m = await collect_measurements(
        store, store_path=tmp_path / "store.db", runs_path=store.runs_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["emotion_v"] == 0.25
    assert m["emotion_a"] == -0.1


async def test_collect_measurements_affect_unavailable_when_never_persisted(store, tmp_path):
    """The gap this checkpoint found: nothing writes the affect_state fact
    except CognitiveCore.stop() — a store no session has ever cleanly
    stopped against has no current affect to report."""
    m = await collect_measurements(
        store, store_path=tmp_path / "store.db", runs_path=store.runs_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["emotion_v"] == UNAVAILABLE


async def test_collect_measurements_never_raises_on_a_broken_section(store, tmp_path, monkeypatch):
    """change 5: a failing measurement query marks its own fields
    unavailable rather than taking the whole collection down."""
    async def _boom(*a, **kw):
        raise RuntimeError("simulated failure")

    import lyra_core.report as report_module
    monkeypatch.setattr(report_module, "_outcomes_section", _boom)

    m = await collect_measurements(
        store, store_path=tmp_path / "store.db", runs_path=store.runs_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["outcomes_total"] == UNAVAILABLE
    assert m["atoms_total"] == 0  # unaffected sections still work


async def test_collect_from_path_is_read_only_and_never_writes(tmp_path):
    """The store is opened mode=ro: this must succeed against a store it
    cannot write to, and the store must be unchanged afterward."""
    store = await Store.open(tmp_path / "store.db")
    await store.append_atom(speaker="wilson", source="cli", text="only atom")
    await store.close()

    before = (tmp_path / "store.db").stat().st_size

    m = await collect_from_path(
        tmp_path / "store.db", tmp_path / "runs.db", None,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["atoms_total"] == 1

    # A second read-only open must still work — nothing was left locked or
    # mutated by the first.
    m2 = await collect_from_path(
        tmp_path / "store.db", tmp_path / "runs.db", None,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m2["atoms_total"] == 1
    assert (tmp_path / "store.db").stat().st_size == before


async def test_open_read_only_cannot_write(tmp_path):
    store = await Store.open(tmp_path / "store.db")
    await store.close()

    store_like = await open_read_only(tmp_path / "store.db")
    try:
        with pytest.raises(Exception):
            await store_like.db.execute(
                "INSERT INTO atoms (ts, speaker, source, text) VALUES (0, 'wilson', 'cli', 'x')"
            )
    finally:
        await store_like.db.close()


async def test_collect_from_path_reports_file_sizes(tmp_path):
    store = await Store.open(tmp_path / "store.db")
    await store.close()
    history_path = tmp_path / "history.db"
    history_path.write_bytes(b"x" * 100)

    m = await collect_from_path(
        tmp_path / "store.db", tmp_path / "runs.db", history_path,
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["store_db_bytes"] > 0
    assert m["history_db_bytes"] == 100


async def test_collect_from_path_history_bytes_unavailable_when_missing(tmp_path):
    store = await Store.open(tmp_path / "store.db")
    await store.close()

    m = await collect_from_path(
        tmp_path / "store.db", tmp_path / "runs.db", tmp_path / "no_such_history.db",
        log_path=tmp_path / "no_such_log.log",
    )
    assert m["history_db_bytes"] == UNAVAILABLE
