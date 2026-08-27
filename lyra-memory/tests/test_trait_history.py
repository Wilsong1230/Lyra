"""Step 0 — trait_history + write-on-mutation.

Hard rule under test: IdentityEngine never mutates a trait without writing a
trait_history row in the same transaction. Verification target: every trait
mutation has a history row — 100%, asserted.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.db import init_db
from lyra_memory.identity_engine import IdentityEngine, assert_trait_history_integrity


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


async def _make_engine(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    engine = IdentityEngine(conn, pool)
    return conn, pool, engine


async def _insert_candidate(conn, name: str, count: int) -> None:
    await conn.execute(
        "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category)"
        " VALUES (?, ?, ?, ?, ?)",
        (name, f"description of {name}", count, time.time(), "behavioral"),
    )
    await conn.commit()


async def _history_rows(conn) -> list[tuple]:
    async with conn.execute(
        "SELECT trait_id, trait_label, event, conf_before, conf_after,"
        " tier_before, tier_after, evidence_count, dream_id"
        " FROM trait_history ORDER BY id"
    ) as cur:
        return await cur.fetchall()


async def test_trait_history_table_exists(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    async with conn.execute("PRAGMA table_info(trait_history)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    await conn.close()
    assert cols == {
        "id", "ts", "trait_id", "trait_label", "event",
        "conf_before", "conf_after", "tier_before", "tier_after",
        "evidence_count", "dream_id",
    }


async def test_promotion_writes_history_row(tmp_db_path: Path):
    conn, _, engine = await _make_engine(tmp_db_path)
    await _insert_candidate(conn, "curiosity", 5)  # surface threshold

    await engine.consolidate()

    rows = await _history_rows(conn)
    assert len(rows) == 1
    trait_id, label, event, conf_before, conf_after, tier_before, tier_after, evid, dream_id = rows[0]
    assert event == "promoted"
    assert label == "curiosity"
    assert conf_before is None
    assert conf_after == pytest.approx(5 / 50)
    assert tier_before is None
    assert tier_after == "surface"
    assert evid == 5
    assert dream_id is None

    async with conn.execute("SELECT id FROM traits WHERE name='curiosity'") as cur:
        (tid,) = await cur.fetchone()
    assert trait_id == tid
    await conn.close()


async def test_confidence_change_writes_history_row(tmp_db_path: Path):
    conn, _, engine = await _make_engine(tmp_db_path)
    await _insert_candidate(conn, "curiosity", 5)
    await engine.consolidate()

    # more evidence, same tier → confidence_change
    await conn.execute("UPDATE candidates SET evidence_count = 8 WHERE trait_name='curiosity'")
    await conn.commit()
    await engine.consolidate()

    rows = await _history_rows(conn)
    assert len(rows) == 2
    _, _, event, conf_before, conf_after, tier_before, tier_after, evid, _ = rows[1]
    assert event == "confidence_change"
    assert conf_before == pytest.approx(5 / 50)
    assert conf_after == pytest.approx(8 / 50)
    assert tier_before == "surface"
    assert tier_after == "surface"
    assert evid == 8
    await conn.close()


async def test_tier_change_writes_history_row(tmp_db_path: Path):
    conn, _, engine = await _make_engine(tmp_db_path)
    await _insert_candidate(conn, "curiosity", 5)
    await engine.consolidate()

    # crosses the character threshold → tier_change
    await conn.execute("UPDATE candidates SET evidence_count = 15 WHERE trait_name='curiosity'")
    await conn.commit()
    await engine.consolidate()

    rows = await _history_rows(conn)
    assert len(rows) == 2
    _, _, event, conf_before, conf_after, tier_before, tier_after, _, _ = rows[1]
    assert event == "tier_change"
    assert tier_before == "surface"
    assert tier_after == "character"
    await conn.close()


async def test_noop_consolidate_writes_no_history(tmp_db_path: Path):
    conn, _, engine = await _make_engine(tmp_db_path)
    await _insert_candidate(conn, "curiosity", 5)
    await engine.consolidate()
    # identical state — no mutation, no history row
    await engine.consolidate()

    rows = await _history_rows(conn)
    assert len(rows) == 1
    await conn.close()


async def test_write_protected_core_writes_no_history(tmp_db_path: Path):
    conn, _, engine = await _make_engine(tmp_db_path)
    ts = time.time()
    cur = await conn.execute(
        "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        ("bedrock", "locked core trait", 0.9, "core", 60, ts),
    )
    await conn.execute(
        "INSERT INTO trait_history"
        " (ts, trait_id, trait_label, event, conf_before, conf_after,"
        "  tier_before, tier_after, evidence_count, dream_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, cur.lastrowid, "bedrock", "promoted", None, 0.9, None, "core", 60, None),
    )
    await conn.commit()
    await _insert_candidate(conn, "bedrock", 61)

    await engine.consolidate()

    # the write-protected path skipped the mutation, so no second row
    rows = await _history_rows(conn)
    assert len(rows) == 1
    await conn.close()


async def test_every_trait_mutation_has_history_row(tmp_db_path: Path):
    """The step-0 verification check itself: 100% coverage, asserted."""
    conn, _, engine = await _make_engine(tmp_db_path)
    for name, count in [("a", 5), ("b", 15), ("c", 50)]:
        await _insert_candidate(conn, name, count)
    await engine.consolidate()

    await conn.execute("UPDATE candidates SET evidence_count = evidence_count + 10")
    await conn.commit()
    await engine.consolidate()

    # asserted by the engine itself; also assert here explicitly
    await assert_trait_history_integrity(conn)

    async with conn.execute("SELECT id, name, confidence, stability FROM traits") as cur:
        traits = await cur.fetchall()
    assert len(traits) == 3
    for tid, name, conf, stability in traits:
        async with conn.execute(
            "SELECT conf_after, tier_after FROM trait_history"
            " WHERE trait_id = ? ORDER BY id DESC LIMIT 1",
            (tid,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, f"trait {name!r} mutated with no history row"
        assert row[0] == pytest.approx(conf)
        assert row[1] == stability
    await conn.close()


async def test_integrity_assertion_fails_loud_on_orphan_trait(tmp_db_path: Path):
    """A trait with no history row means something wrote outside the path."""
    conn, _, _ = await _make_engine(tmp_db_path)
    await conn.execute(
        "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        ("smuggled", "wrote around the engine", 1.0, "core", 999, time.time()),
    )
    await conn.commit()

    with pytest.raises(AssertionError):
        await assert_trait_history_integrity(conn)
    await conn.close()


async def test_history_and_trait_written_in_same_transaction(tmp_db_path: Path):
    """Force the history insert to fail; the trait write must roll back too."""
    conn, _, engine = await _make_engine(tmp_db_path)
    await _insert_candidate(conn, "atomicity", 5)

    # sabotage: replace trait_history with a view so INSERT fails mid-transaction
    await conn.execute("ALTER TABLE trait_history RENAME TO trait_history_real")
    await conn.execute("CREATE VIEW trait_history AS SELECT * FROM trait_history_real")
    await conn.commit()

    with pytest.raises(Exception):
        await engine.consolidate()
    await conn.rollback()

    # the trait insert must not have survived on its own
    async with conn.execute("SELECT COUNT(*) FROM traits") as cur:
        (n,) = await cur.fetchone()
    assert n == 0
    await conn.close()
