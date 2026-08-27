"""Step 11 — forgetting.

Demotion, not deletion. Storage never shrinks; only competition does.

Verification target: "demoted atoms are ones you'd expect — manual read".
That is a judgement, so `review_forgetting` exists for it and MANUAL.md says
how. What is asserted here is everything that would make the manual read
meaningless: that nothing is deleted, that demotion only removes an atom from
the KNN pool, that facts are not in the pool at all, and that retrieval keeps
an atom alive.

Every constant is provisional. The sheet puts this step last because it needs
a large store to tune against, and none of these numbers has been fitted to
real data.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lyra_memory.config import FORGET_THRESHOLD, SALIENCE_BASELINE
from lyra_memory.store import Store
from lyra_memory.store.passes.forgetting import ForgettingPass, retrievability

_DAY = 86400.0


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


# ── the curve ────────────────────────────────────────────────────────────────

def test_a_new_atom_is_fully_retrievable():
    assert retrievability(age_days=0, salience=0.5, access_count=0) == pytest.approx(1.0)


def test_retrievability_decays_with_age():
    young = retrievability(age_days=10, salience=0.5, access_count=0)
    old = retrievability(age_days=1000, salience=0.5, access_count=0)
    assert young > old


def test_salience_slows_decay():
    dull = retrievability(age_days=200, salience=0.0, access_count=0)
    vivid = retrievability(age_days=200, salience=1.0, access_count=0)
    assert vivid > dull


def test_access_slows_decay():
    """Access is itself evidence of relevance."""
    unused = retrievability(age_days=200, salience=0.5, access_count=0)
    used = retrievability(age_days=200, salience=0.5, access_count=10)
    assert used > unused


def test_access_has_diminishing_returns():
    """The tenth retrieval says much less than the second."""
    first = retrievability(age_days=200, salience=0.5, access_count=1)
    second = retrievability(age_days=200, salience=0.5, access_count=2)
    tenth = retrievability(age_days=200, salience=0.5, access_count=10)
    eleventh = retrievability(age_days=200, salience=0.5, access_count=11)
    assert (second - first) > (eleventh - tenth)


def test_retrievability_is_bounded():
    for age in (0, 1, 100, 100_000):
        for salience in (0.0, 0.5, 1.0):
            score = retrievability(age_days=age, salience=salience, access_count=0)
            assert 0.0 <= score <= 1.0


# ── the pass ─────────────────────────────────────────────────────────────────

async def _atom(store: Store, text: str, age_days: float, salience: float,
                now: float) -> int:
    atom_id = await store.append_atom(
        speaker="wilson", source="cli", text=text, ts=now - age_days * _DAY)
    await store.db.execute(
        "UPDATE atoms SET salience = ? WHERE id = ?", (salience, atom_id))
    await store.db.commit()
    return atom_id


async def _score(store: Store, atom_id: int) -> float:
    async with store.db.execute(
        "SELECT retrievability FROM atoms WHERE id = ?", (atom_id,)) as cur:
        return (await cur.fetchone())[0]


async def test_pass_scores_every_atom(store: Store):
    now = time.time()
    await _atom(store, "recent", 1, 0.5, now)
    await _atom(store, "ancient", 2000, 0.1, now)

    await ForgettingPass(store, now=now).run()

    async with store.db.execute(
        "SELECT COUNT(*) FROM atoms WHERE retrievability IS NULL") as cur:
        assert (await cur.fetchone())[0] == 0


async def test_old_dull_atoms_score_below_new_vivid_ones(store: Store):
    now = time.time()
    fresh = await _atom(store, "yesterday, and it mattered", 1, 0.9, now)
    stale = await _atom(store, "a forgettable tuesday years ago", 2000, 0.05, now)

    await ForgettingPass(store, now=now).run()
    assert await _score(store, fresh) > await _score(store, stale)


async def test_nothing_is_deleted(store: Store):
    """The whole claim of this step."""
    now = time.time()
    for i in range(10):
        await _atom(store, f"an ancient forgettable thing {i}", 5000, 0.0, now)

    await ForgettingPass(store, now=now).run()

    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        assert (await cur.fetchone())[0] == 10
    async with store.db.execute("SELECT COUNT(*) FROM vec_atoms") as cur:
        assert (await cur.fetchone())[0] == 10


async def test_the_pass_contains_no_delete(store: Store):
    """Structural, not a matter of intent — there must be no deletion path.

    Checked against executable statements rather than the prose, which
    discusses deletion at length precisely because there is none.
    """
    import ast
    import inspect

    from lyra_memory.store.passes import forgetting

    tree = ast.parse(inspect.getsource(forgetting))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "delete from" not in node.value.lower(), (
                f"a DELETE statement in the forgetting pass: {node.value!r}")
        if isinstance(node, ast.Attribute):
            assert "delete" not in node.attr.lower()


# ── demotion removes an atom from KNN and nothing else ───────────────────────

async def test_a_demoted_atom_leaves_the_knn_pool(store: Store):
    from lyra_memory.store.context import build_context

    now = time.time()
    text = "the retrieval floor is a similarity threshold not a rank cutoff"
    atom_id = await _atom(store, text, 5000, 0.0, now)

    result = await build_context(store, query=text, recent_turns=0)
    assert atom_id in result.atom_ids, "precondition: it is retrievable before demotion"

    await ForgettingPass(store, now=now).run()
    assert await _score(store, atom_id) < FORGET_THRESHOLD

    result = await build_context(store, query=text, recent_turns=0)
    assert atom_id not in result.atom_ids, (
        "a demoted atom came back through another recall path; every path "
        "feeding the recall block is competition")


async def test_a_demoted_atom_is_still_queryable_by_id_time_and_session(store: Store):
    """Still queryable by time, session, entity, or exact id."""
    now = time.time()
    atom_id = await _atom(store, "an ancient thing", 5000, 0.0, now)
    await store.db.execute("UPDATE atoms SET session_id = 7 WHERE id = ?", (atom_id,))
    await store.db.commit()

    await ForgettingPass(store, now=now).run()

    async with store.db.execute("SELECT text FROM atoms WHERE id = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] == "an ancient thing"
    async with store.db.execute("SELECT COUNT(*) FROM atoms WHERE session_id = 7") as cur:
        assert (await cur.fetchone())[0] == 1
    async with store.db.execute(
        "SELECT COUNT(*) FROM atoms WHERE ts < ?", (now,)) as cur:
        assert (await cur.fetchone())[0] == 1


async def test_an_unscored_atom_is_not_treated_as_forgotten(store: Store):
    """NULL means the pass has not run, which is not the same as forgotten."""
    from lyra_memory.store.context import build_context

    text = "a brand new note about the retrieval floor"
    atom_id = await store.append_atom(speaker="wilson", source="cli", text=text)

    async with store.db.execute(
        "SELECT retrievability FROM atoms WHERE id = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] is None

    result = await build_context(store, query=text, recent_turns=0)
    assert atom_id in result.atom_ids


# ── access keeps an atom alive ───────────────────────────────────────────────

async def test_a_retrieved_atom_outlives_an_identical_unretrieved_one(store: Store):
    now = time.time()
    used = await _atom(store, "a thing that keeps coming up", 400, 0.3, now)
    unused = await _atom(store, "a thing that never came up again", 400, 0.3, now)

    for i in range(5):
        await store.db.execute(
            "INSERT INTO context_log (ts, atom_ids, fact_ids, dream_ids, budget_used)"
            " VALUES (?, ?, '[]', '[]', 0)",
            (now - (10 - i) * _DAY, json.dumps([used])))
    await store.db.commit()

    await ForgettingPass(store, now=now).run()
    assert await _score(store, used) > await _score(store, unused)


async def test_retrieval_resets_the_clock(store: Store):
    """Recently retrieved stays live, however old the atom itself is."""
    now = time.time()
    ancient_but_used = await _atom(store, "old, but looked at last week", 3000, 0.3, now)
    await store.db.execute(
        "INSERT INTO context_log (ts, atom_ids, fact_ids, dream_ids, budget_used)"
        " VALUES (?, ?, '[]', '[]', 0)",
        (now - 7 * _DAY, json.dumps([ancient_but_used])))
    await store.db.commit()

    await ForgettingPass(store, now=now).run()
    assert await _score(store, ancient_but_used) > FORGET_THRESHOLD


# ── facts are not in the pool ────────────────────────────────────────────────

async def test_facts_do_not_decay(store: Store):
    """Routing, not an exemption: facts live in their own table with their own
    physics, so no 'except facts' branch is needed anywhere.

    She forgets what a Tuesday felt like. She does not forget where you go to
    school.
    """
    now = time.time()
    ancient = now - 5000 * _DAY
    await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
        " VALUES (?,?,?,?,?,?)",
        (ancient, "wilson", "studies at FGCU", "stated", 0.9, ancient))
    await store.db.commit()

    await ForgettingPass(store, now=now).run()

    async with store.db.execute("PRAGMA table_info(facts)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    assert "retrievability" not in columns, "facts must not be in the decay pool"

    from lyra_memory.store.context import build_context

    result = await build_context(store, query="tell me about wilson", recent_turns=0)
    assert "studies at FGCU" in result.blocks["facts"]


# ── re-runnable ──────────────────────────────────────────────────────────────

async def test_pass_is_idempotent_at_a_fixed_time(store: Store):
    now = time.time()
    atom_id = await _atom(store, "a thing", 100, 0.4, now)

    await ForgettingPass(store, now=now).run()
    first = await _score(store, atom_id)
    await ForgettingPass(store, now=now).run()
    assert await _score(store, atom_id) == pytest.approx(first)


async def test_scores_fall_as_time_passes(store: Store):
    now = time.time()
    atom_id = await _atom(store, "a thing", 100, 0.4, now)

    await ForgettingPass(store, now=now).run()
    early = await _score(store, atom_id)
    await ForgettingPass(store, now=now + 500 * _DAY).run()
    assert await _score(store, atom_id) < early


async def test_missing_salience_falls_back_to_the_baseline(store: Store):
    now = time.time()
    atom_id = await store.append_atom(
        speaker="wilson", source="cli", text="never scored for salience",
        ts=now - 10 * _DAY)

    await ForgettingPass(store, now=now).run()
    expected = retrievability(age_days=10, salience=SALIENCE_BASELINE, access_count=0)
    assert await _score(store, atom_id) == pytest.approx(expected)
