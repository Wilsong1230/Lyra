"""Step 1 — atoms + vec_atoms + atoms_fts.

Verification target: **turns land** — atom count == turn count, no silent
gaps. "No silent gaps" is the load-bearing half: an atom that reaches `atoms`
but not `vec_atoms` or `atoms_fts` is invisible to two of the three retrieval
paths and nothing reports it.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lyra_memory.store import Store
from lyra_memory.store.schema import ENVIRONMENTS, SOURCES, SPEAKERS


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


# ── schema shape ─────────────────────────────────────────────────────────────

async def test_atoms_has_every_column_from_the_sheet(store: Store):
    async with store.db.execute("PRAGMA table_info(atoms)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "id", "ts", "speaker", "source", "environment", "instance", "text",
        "run_id", "session_id", "salience", "retrievability", "outcome_id",
    }


async def test_cold_columns_are_nullable(store: Store):
    """Everything structural is nullable, cold, derived."""
    async with store.db.execute("PRAGMA table_info(atoms)") as cur:
        notnull = {row[1]: row[3] for row in await cur.fetchall()}
    for cold in ("session_id", "salience", "retrievability", "outcome_id",
                 "run_id", "environment", "instance"):
        assert notnull[cold] == 0, f"{cold} must be nullable"
    for hot in ("ts", "speaker", "source", "text"):
        assert notnull[hot] == 1, f"{hot} must be NOT NULL"


async def test_all_tables_exist(store: Store):
    async with store.db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ) as cur:
        tables = {row[0] for row in await cur.fetchall()}
    for expected in (
        "atoms", "vec_atoms", "atoms_fts", "context_log", "sessions",
        "dreams", "dream_atoms", "entities", "atom_entities", "outcomes",
        "facts", "commitments", "trait_history", "traits", "candidates",
    ):
        assert expected in tables, f"missing table {expected}"


async def test_wal_mode_is_on(store: Store):
    async with store.db.execute("PRAGMA journal_mode") as cur:
        (mode,) = await cur.fetchone()
    assert mode.lower() == "wal"


async def test_runs_lives_in_a_separate_file(store: Store, tmp_path: Path):
    """Bulk is prunable, so it must not share a file with the substrate."""
    async with store.db.execute(
        "SELECT name FROM sqlite_master WHERE name = 'runs'"
    ) as cur:
        assert await cur.fetchone() is None
    assert store.runs_path != store.path
    async with store.runs.execute("SELECT name FROM sqlite_master WHERE name='runs'") as cur:
        assert await cur.fetchone() is not None


# ── appending an atom ────────────────────────────────────────────────────────

async def test_append_atom_writes_all_three(store: Store):
    atom_id = await store.append_atom(speaker="wilson", source="cli", text="hello there")

    async with store.db.execute("SELECT COUNT(*) FROM atoms WHERE id = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] == 1
    async with store.db.execute("SELECT COUNT(*) FROM vec_atoms WHERE rowid = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] == 1
    async with store.db.execute(
        "SELECT rowid FROM atoms_fts WHERE atoms_fts MATCH 'hello'"
    ) as cur:
        assert [r[0] for r in await cur.fetchall()] == [atom_id]


async def test_append_atom_populates_speaker_and_environment(store: Store):
    """Populate speaker and environment from step 1 even though nothing reads
    them yet — impossible to reconstruct retroactively."""
    atom_id = await store.append_atom(
        speaker="lyra", source="cli", text="an answer", environment="cli")
    async with store.db.execute(
        "SELECT speaker, source, environment FROM atoms WHERE id = ?", (atom_id,)
    ) as cur:
        assert await cur.fetchone() == ("lyra", "cli", "cli")


async def test_cold_columns_start_null(store: Store):
    atom_id = await store.append_atom(speaker="wilson", source="cli", text="hi")
    async with store.db.execute(
        "SELECT session_id, salience, retrievability, outcome_id FROM atoms WHERE id = ?",
        (atom_id,),
    ) as cur:
        assert await cur.fetchone() == (None, None, None, None)


async def test_embedding_is_the_configured_width(store: Store):
    from lyra_memory.config import EMBED_DIM

    atom_id = await store.append_atom(speaker="wilson", source="cli", text="hi")
    async with store.db.execute(
        "SELECT embedding FROM vec_atoms WHERE rowid = ?", (atom_id,)
    ) as cur:
        (raw,) = await cur.fetchone()
    assert len(struct.unpack(f"{EMBED_DIM}f", raw)) == EMBED_DIM


# ── vocabulary is enforced, loudly ───────────────────────────────────────────

async def test_unknown_speaker_is_rejected(store: Store):
    with pytest.raises(Exception):
        await store.append_atom(speaker="stranger", source="cli", text="hi")


async def test_unknown_source_is_rejected(store: Store):
    """A typo'd source would silently escape the sandbox_read dream exclusion."""
    with pytest.raises(ValueError):
        await store.append_atom(speaker="wilson", source="sandbox-read", text="hi")


async def test_unknown_environment_is_rejected(store: Store):
    with pytest.raises(ValueError):
        await store.append_atom(
            speaker="wilson", source="cli", text="hi", environment="holodeck")


async def test_rejected_atom_leaves_nothing_behind(store: Store):
    """A failed append must not leave a half-written atom."""
    with pytest.raises(ValueError):
        await store.append_atom(speaker="wilson", source="nope", text="hi")
    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        assert (await cur.fetchone())[0] == 0


def test_vocabularies_match_the_sheet():
    assert SPEAKERS == {"wilson", "lyra", "system"}
    assert SOURCES == {"cli", "wakeword", "ambient", "vision", "sandbox_read"}
    assert ENVIRONMENTS == {"cli", "shell", "physics", "bns"}


# ── the verification check: turns land, no silent gaps ───────────────────────

async def test_turn_count_equals_atom_count(store: Store):
    turns = [
        ("what does the segmentation pass read?", "atoms.ts and the embeddings."),
        ("and what does it write?", "sessions, plus gap_since_prev."),
        ("does it touch salience?", "no, that is a separate pass."),
    ]
    for user_text, lyra_text in turns:
        await store.ingest_turn(user_text=user_text, lyra_text=lyra_text, source="cli")

    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        (atom_count,) = await cur.fetchone()
    assert atom_count == len(turns) * 2, "one atom for the user turn, one for hers"

    async with store.db.execute("SELECT COUNT(*) FROM context_log") as cur:
        (log_count,) = await cur.fetchone()
    assert log_count == len(turns), "one context_log row per turn"


async def test_no_silent_gaps_across_the_three_writes(store: Store):
    """Every atom must be in vec_atoms and atoms_fts. An atom present in only
    one is invisible to two retrieval paths and nothing would report it."""
    for i in range(25):
        await store.ingest_turn(
            user_text=f"question number {i} about retrieval",
            lyra_text=f"answer number {i} about retrieval",
            source="cli",
        )

    gaps = await store.find_index_gaps()
    assert gaps == {"missing_vec": [], "missing_fts": []}, f"silent gaps: {gaps}"


async def test_find_index_gaps_actually_detects_a_gap(store: Store):
    """The gap check has to be able to fail, or it verifies nothing."""
    atom_id = await store.append_atom(speaker="wilson", source="cli", text="orphan atom")
    await store.db.execute("DELETE FROM vec_atoms WHERE rowid = ?", (atom_id,))
    await store.db.commit()

    gaps = await store.find_index_gaps()
    assert gaps["missing_vec"] == [atom_id]


async def test_speakers_are_recorded_per_turn(store: Store):
    await store.ingest_turn(user_text="a question", lyra_text="a reply", source="cli")
    async with store.db.execute("SELECT speaker, text FROM atoms ORDER BY id") as cur:
        rows = await cur.fetchall()
    assert rows == [("wilson", "a question"), ("lyra", "a reply")]


async def test_ingest_turn_is_ordered_user_then_lyra(store: Store):
    await store.ingest_turn(user_text="first", lyra_text="second", source="cli")
    async with store.db.execute("SELECT ts FROM atoms ORDER BY id") as cur:
        user_ts, lyra_ts = [r[0] for r in await cur.fetchall()]
    assert user_ts <= lyra_ts


# ── instance scope ───────────────────────────────────────────────────────────

async def test_instance_defaults_to_the_configured_instance(store: Store):
    from lyra_memory.store.schema import DEFAULT_INSTANCE

    atom_id = await store.append_atom(speaker="wilson", source="cli", text="hi")
    async with store.db.execute("SELECT instance FROM atoms WHERE id = ?", (atom_id,)) as cur:
        (instance,) = await cur.fetchone()
    assert instance == DEFAULT_INSTANCE


async def test_shared_perception_atoms_carry_no_instance(store: Store):
    """NULL instance means shared perception — visible to every instance."""
    atom_id = await store.append_atom(
        speaker="system", source="vision", text="a frame she looked at", instance=None)
    async with store.db.execute("SELECT instance FROM atoms WHERE id = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] is None


# ── bulk containment ─────────────────────────────────────────────────────────

async def test_bulk_goes_to_runs_and_the_atom_is_a_pointer(store: Store):
    """Hard rule: bulk never becomes atoms. The atom is the event + pointer."""
    run_id = await store.append_run(
        environment="shell",
        transcript="\n".join(f"line {i} of noisy stdout" for i in range(500)),
        metrics={"exit_code": 0},
    )
    atom_id = await store.append_atom(
        speaker="lyra", source="cli", environment="shell",
        text="ran the test suite; it passed on the fourth attempt",
        run_id=run_id,
    )

    async with store.db.execute("SELECT text, run_id FROM atoms WHERE id = ?", (atom_id,)) as cur:
        text, stored_run_id = await cur.fetchone()
    assert stored_run_id == run_id
    assert "noisy stdout" not in text
    assert len(text) < 200

    async with store.runs.execute(
        "SELECT transcript_ref FROM runs WHERE id = ?", (run_id,)
    ) as cur:
        (transcript,) = await cur.fetchone()
    assert "line 499 of noisy stdout" in transcript


# ── hot path budget ──────────────────────────────────────────────────────────

async def test_ingest_turn_stays_within_the_hot_path_budget(store: Store):
    """<50ms per turn, excluding the LLM call."""
    import time

    # warm the embedder so this measures the write path, not model load
    await store.ingest_turn(user_text="warmup", lyra_text="warmup", source="cli")

    durations = []
    for i in range(10):
        start = time.perf_counter()
        await store.ingest_turn(
            user_text=f"a realistic question about the store, number {i}",
            lyra_text=f"a realistic reply about the store, number {i}",
            source="cli",
        )
        durations.append((time.perf_counter() - start) * 1000)

    worst = max(durations)
    assert worst < 50, f"hot path exceeded budget: worst {worst:.1f}ms of {durations}"


async def test_precomputed_vector_is_used_verbatim(store: Store):
    """The hot path embeds the incoming text once, to retrieve with, and
    reuses that vector when appending — not a second ~10ms embed."""
    from lyra_memory.embeddings import embed

    text = "does the store reuse the query embedding?"
    precomputed = await embed(text)
    atom_id = await store.append_atom(
        speaker="wilson", source="cli", text=text, vec=precomputed)

    async with store.db.execute(
        "SELECT embedding FROM vec_atoms WHERE rowid = ?", (atom_id,)
    ) as cur:
        (stored,) = await cur.fetchone()
    assert bytes(stored) == precomputed
