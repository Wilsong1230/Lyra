"""Step 2 — fail loud, and assert the schema at boot.

Verification target: **corrupt the schema on purpose → the process refuses to
start.** Not "logs a warning". Refuses.

Why this is worth a step of its own: fail-open makes a broken store and an
empty store behaviourally identical, and for an emergence thesis that is
undetectable from transcripts. It already happened here — 653 turns produced
0 episodes with no symptom at all.

A version number alone does not catch this. A store whose `atoms_fts` insert
trigger has been dropped still reports schema v1 while silently indexing
nothing, so the assertion compares structure, not a stamp.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lyra_memory.store import Store
from lyra_memory.store.integrity import SchemaMismatch
from lyra_memory.store.schema import SCHEMA_VERSION


async def _make_store(path: Path) -> None:
    store = await Store.open(path)
    await store.append_atom(speaker="wilson", source="cli", text="a first atom")
    await store.close()


def _corrupt(path: Path, *statements: str) -> None:
    """Reach past the store and break the schema, the way a bad migration or
    a stray sqlite3 session would."""
    conn = sqlite3.connect(path)
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


# ── a healthy store opens ────────────────────────────────────────────────────

async def test_healthy_store_opens(tmp_path: Path):
    await _make_store(tmp_path / "store.db")
    store = await Store.open(tmp_path / "store.db")  # must not raise
    await store.close()


async def test_reopening_is_stable(tmp_path: Path):
    """The assertion must not flag a store the code itself just wrote."""
    path = tmp_path / "store.db"
    for _ in range(3):
        store = await Store.open(path)
        await store.append_atom(speaker="wilson", source="cli", text="another")
        await store.close()


# ── the verification check: deliberate corruption refuses to start ───────────

async def test_dropped_table_refuses_to_start(tmp_path: Path):
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "DROP TABLE commitments")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)


async def test_dropped_column_refuses_to_start(tmp_path: Path):
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "ALTER TABLE atoms DROP COLUMN retrievability")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)


async def test_added_column_refuses_to_start(tmp_path: Path):
    """Drift in either direction is drift."""
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "ALTER TABLE atoms ADD COLUMN vibes TEXT")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)


async def test_dropped_fts_trigger_refuses_to_start(tmp_path: Path):
    """The subtle one. Without this trigger the store still answers every
    query, still reports schema v1, and silently indexes nothing."""
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "DROP TRIGGER atoms_fts_insert")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)


async def test_dropped_index_refuses_to_start(tmp_path: Path):
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "DROP INDEX idx_atoms_ts")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)


async def test_wrong_schema_version_refuses_to_start(tmp_path: Path):
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, f"UPDATE schema_meta SET value = '{SCHEMA_VERSION + 1}'"
                   " WHERE key = 'schema_version'")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)


async def test_missing_schema_version_refuses_to_start(tmp_path: Path):
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "DELETE FROM schema_meta WHERE key = 'schema_version'")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)


async def test_mismatch_names_what_is_wrong(tmp_path: Path):
    """A crash that does not say what drifted just moves the debugging."""
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "DROP TABLE commitments")

    with pytest.raises(SchemaMismatch, match="commitments"):
        await Store.open(path)


async def test_comment_only_ddl_change_does_not_break_a_store(tmp_path: Path):
    """The assertion compares structure, not the DDL text.

    SQLite stores CREATE statements verbatim, comments included. Comparing raw
    SQL would mean editing a comment invalidates every existing store — an
    assertion nobody would keep.
    """
    from lyra_memory.store import integrity

    path = tmp_path / "store.db"
    await _make_store(path)

    reference = await integrity.reference_structure()
    assert not any("--" in str(v) for v in reference["triggers"].values()), (
        "trigger SQL must be normalized before comparison"
    )


# ── fail loud: no try/except around ingest ───────────────────────────────────

async def test_ingest_does_not_swallow_a_failed_embed(tmp_path: Path, monkeypatch):
    """If the embedder breaks, the turn fails. It does not quietly append an
    atom that no semantic query will ever reach."""
    store = await Store.open(tmp_path / "store.db")

    async def _boom(text: str) -> bytes:
        raise RuntimeError("embedder is down")

    monkeypatch.setattr("lyra_memory.store.embed", _boom)

    with pytest.raises(RuntimeError, match="embedder is down"):
        await store.append_atom(speaker="wilson", source="cli", text="hello")

    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        assert (await cur.fetchone())[0] == 0
    await store.close()


async def test_ingest_turn_does_not_half_land(tmp_path: Path, monkeypatch):
    """A turn that half-lands is worse than one that fails: only the second
    is visible."""
    store = await Store.open(tmp_path / "store.db")

    from lyra_memory import embeddings

    real_embed = embeddings.embed
    calls = {"n": 0}

    async def _fail_on_second(text: str) -> bytes:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("embedder died mid-turn")
        return await real_embed(text)

    monkeypatch.setattr("lyra_memory.store.embed", _fail_on_second)

    with pytest.raises(RuntimeError):
        await store.ingest_turn(user_text="a question", lyra_text="a reply", source="cli")
    await store.db.rollback()

    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        assert (await cur.fetchone())[0] == 0, "the user atom must not survive alone"
    async with store.db.execute("SELECT COUNT(*) FROM context_log") as cur:
        assert (await cur.fetchone())[0] == 0
    await store.close()


def test_store_module_has_no_bare_except_around_ingest():
    """Read the source: the rule is structural, not a matter of intent.

    The one permitted except in the module is the sqlite-vec extension-load
    error, which is re-raised as a RuntimeError with a fix in the message.
    """
    import inspect

    from lyra_memory import store as store_module

    source = inspect.getsource(store_module)
    ingest_source = source[source.index("async def append_atom"):]
    assert "except" not in ingest_source, (
        "no try/except is permitted around ingest — fail-open makes a broken "
        "store and an empty store behaviourally identical"
    )


# ── embedding space ──────────────────────────────────────────────────────────

async def test_store_records_which_embedder_built_its_vectors(tmp_path: Path):
    from lyra_memory.embeddings import backend_id

    store = await Store.open(tmp_path / "store.db")
    async with store.db.execute(
        "SELECT value FROM schema_meta WHERE key = 'embedder'"
    ) as cur:
        (recorded,) = await cur.fetchone()
    await store.close()
    assert recorded == backend_id()


async def test_a_different_embedder_refuses_to_open(tmp_path: Path):
    """A vec table holding vectors from two embedding spaces is silently and
    unfixably wrong: every distance across the boundary is noise."""
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "UPDATE schema_meta SET value = 'some-other-embedder'"
                   " WHERE key = 'embedder'")

    with pytest.raises(SchemaMismatch, match="embedding space|some-other-embedder"):
        await Store.open(path)


async def test_missing_embedder_row_refuses_to_open(tmp_path: Path):
    path = tmp_path / "store.db"
    await _make_store(path)
    _corrupt(path, "DELETE FROM schema_meta WHERE key = 'embedder'")

    with pytest.raises(SchemaMismatch):
        await Store.open(path)
