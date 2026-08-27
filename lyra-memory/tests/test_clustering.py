"""Step 10 — clustering entities into project nodes.

No verification target is given for this step, so the tests assert the two
things that would make it useless if wrong: that the grouping signal is
co-occurrence rather than name similarity, and that nothing is deleted.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lyra_memory.config import CLUSTER_MIN_COOCCURRENCE, CLUSTER_MIN_SIZE
from lyra_memory.store import Store
from lyra_memory.store.passes.clustering import ClusteringPass, _components


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def _entity(store: Store, name: str, kind: str = "topic") -> int:
    now = time.time()
    await store.db.execute(
        "INSERT INTO entities (name, kind, first_seen, last_seen) VALUES (?,?,?,?)",
        (name, kind, now, now))
    async with store.db.execute("SELECT id FROM entities WHERE name = ?", (name,)) as cur:
        return (await cur.fetchone())[0]


async def _atom_mentioning(store: Store, text: str, entity_ids: list[int]) -> int:
    atom_id = await store.append_atom(speaker="wilson", source="cli", text=text)
    for entity_id in entity_ids:
        await store.db.execute(
            "INSERT INTO atom_entities (atom_id, entity_id, confidence) VALUES (?,?,?)",
            (atom_id, entity_id, 0.9))
    await store.db.commit()
    return atom_id


async def _projects(store: Store) -> list[str]:
    async with store.db.execute(
        "SELECT name FROM entities WHERE kind = 'project' ORDER BY name") as cur:
        return [r[0] for r in await cur.fetchall()]


# ── the pure component ───────────────────────────────────────────────────────

def test_components_groups_connected_edges():
    edges = {(1, 2): 5, (2, 3): 5, (4, 5): 5}
    groups = sorted(sorted(g) for g in _components(edges, 3))
    assert groups == [[1, 2, 3], [4, 5]]


def test_components_ignores_weak_edges():
    edges = {(1, 2): 1, (2, 3): 5}
    groups = sorted(sorted(g) for g in _components(edges, 3))
    assert groups == [[2, 3]]


def test_components_of_nothing_is_nothing():
    assert _components({}, 1) == []


# ── clustering ───────────────────────────────────────────────────────────────

async def test_entities_that_co_occur_become_a_project(store: Store):
    voice = await _entity(store, "lyra-voice")
    listen = await _entity(store, "lyra-listen")
    kokoro = await _entity(store, "kokoro")

    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"voice and listen and kokoro, take {i}",
                               [voice, listen, kokoro])

    await ClusteringPass(store).run()
    assert await _projects(store)


async def test_the_project_links_the_atoms_that_built_it(store: Store):
    voice = await _entity(store, "lyra-voice")
    listen = await _entity(store, "lyra-listen")
    kokoro = await _entity(store, "kokoro")
    atom_ids = [await _atom_mentioning(store, f"take {i}", [voice, listen, kokoro])
                for i in range(CLUSTER_MIN_COOCCURRENCE)]

    await ClusteringPass(store).run()

    async with store.db.execute(
        "SELECT ae.atom_id FROM atom_entities ae JOIN entities e ON e.id = ae.entity_id"
        " WHERE e.kind = 'project' ORDER BY ae.atom_id"
    ) as cur:
        linked = [r[0] for r in await cur.fetchall()]
    assert linked == sorted(atom_ids)


async def test_entities_that_never_co_occur_do_not_cluster(store: Store):
    alone = await _entity(store, "kokoro")
    other = await _entity(store, "fgcu")
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"only kokoro {i}", [alone])
        await _atom_mentioning(store, f"only fgcu {i}", [other])

    await ClusteringPass(store).run()
    assert await _projects(store) == []


async def test_one_chance_co_occurrence_is_not_a_project(store: Store):
    """A single shared mention is a coincidence, not a project."""
    a = await _entity(store, "kokoro")
    b = await _entity(store, "fgcu")
    await _atom_mentioning(store, "mentioned both once", [a, b])

    await ClusteringPass(store).run()
    assert await _projects(store) == []


async def test_a_pair_below_min_size_is_not_a_project(store: Store):
    if CLUSTER_MIN_SIZE <= 2:
        pytest.skip("min size allows pairs")
    a = await _entity(store, "kokoro")
    b = await _entity(store, "coqui")
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"both {i}", [a, b])

    await ClusteringPass(store).run()
    assert await _projects(store) == []


async def test_similar_names_alone_do_not_cluster(store: Store):
    """The signal is co-occurrence, not spelling. lyra-voice and lyra-listen
    look alike; that is a fact about their names, not about her history."""
    voice = await _entity(store, "lyra-voice")
    listen = await _entity(store, "lyra-listen")
    vision = await _entity(store, "lyra-vision")
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"voice only {i}", [voice])
        await _atom_mentioning(store, f"listen only {i}", [listen])
        await _atom_mentioning(store, f"vision only {i}", [vision])

    await ClusteringPass(store).run()
    assert await _projects(store) == []


# ── naming ───────────────────────────────────────────────────────────────────

async def test_the_model_names_the_project(store: Store):
    ids = [await _entity(store, n) for n in ("lyra-voice", "lyra-listen", "kokoro")]
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"take {i}", ids)

    async def _namer(prompt: str) -> str:
        return "the speech stack"

    await ClusteringPass(store, llm=_namer).run()
    assert "the speech stack" in await _projects(store)


async def test_naming_falls_back_to_the_most_connected_member(store: Store):
    ids = [await _entity(store, n) for n in ("lyra-voice", "lyra-listen", "kokoro")]
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"take {i}", ids)

    async def _broken(prompt: str) -> str:
        raise RuntimeError("namer down")

    await ClusteringPass(store, llm=_broken).run()
    projects = await _projects(store)
    assert projects, "a failed namer must not prevent the cluster"
    assert projects[0] in {"lyra-voice", "lyra-listen", "kokoro"}


async def test_a_runaway_name_is_rejected(store: Store):
    ids = [await _entity(store, n) for n in ("lyra-voice", "lyra-listen", "kokoro")]
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"take {i}", ids)

    async def _runaway(prompt: str) -> str:
        return "a project name " * 200

    await ClusteringPass(store, llm=_runaway).run()
    projects = await _projects(store)
    assert projects and len(projects[0]) <= 60


async def test_dreams_are_offered_as_naming_context(store: Store):
    ids = [await _entity(store, n) for n in ("lyra-voice", "lyra-listen", "kokoro")]
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"take {i}", ids)
    await store.db.execute(
        "INSERT INTO dreams (ts, text) VALUES (?, ?)",
        (time.time(), "a reflection about getting her voice working"))
    await store.db.commit()

    seen = []

    async def _namer(prompt: str) -> str:
        seen.append(prompt)
        return "voice work"

    await ClusteringPass(store, llm=_namer).run()
    assert any("getting her voice working" in p for p in seen)


# ── nothing is deleted ───────────────────────────────────────────────────────

async def test_pass_is_idempotent(store: Store):
    ids = [await _entity(store, n) for n in ("lyra-voice", "lyra-listen", "kokoro")]
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"take {i}", ids)

    async def _namer(prompt: str) -> str:
        return "the speech stack"

    await ClusteringPass(store, llm=_namer).run()
    first = await _projects(store)
    async with store.db.execute("SELECT COUNT(*) FROM atom_entities") as cur:
        (links,) = await cur.fetchone()

    await ClusteringPass(store, llm=_namer).run()
    assert await _projects(store) == first
    async with store.db.execute("SELECT COUNT(*) FROM atom_entities") as cur:
        assert (await cur.fetchone())[0] == links


async def test_existing_entities_are_never_deleted(store: Store):
    """No deletion path. `facts.subject` may already key on a name."""
    ids = [await _entity(store, n) for n in ("lyra-voice", "lyra-listen", "kokoro")]
    stale = await _entity(store, "an old project", kind="project")
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"take {i}", ids)

    await ClusteringPass(store).run()

    async with store.db.execute("SELECT COUNT(*) FROM entities WHERE id = ?", (stale,)) as cur:
        assert (await cur.fetchone())[0] == 1


async def test_project_entities_do_not_cluster_with_each_other(store: Store):
    """Projects are an output of this pass, so feeding them back in would let
    clusters merge with themselves on every run."""
    ids = [await _entity(store, n) for n in ("lyra-voice", "lyra-listen", "kokoro")]
    for i in range(CLUSTER_MIN_COOCCURRENCE):
        await _atom_mentioning(store, f"take {i}", ids)

    async def _namer(prompt: str) -> str:
        return "the speech stack"

    for _ in range(3):
        await ClusteringPass(store, llm=_namer).run()

    assert await _projects(store) == ["the speech stack"]


async def test_empty_store_does_nothing(store: Store):
    await ClusteringPass(store).run()
    assert await _projects(store) == []
