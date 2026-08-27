"""Step 7 — entities.

Reads `atoms.text`, writes `entities` and `atom_entities`. This is what makes
subject-keyed fact retrieval (step 8) and project clustering (step 10)
possible: without a name to key on, both fall back to embedding similarity,
which is the candidate-dedup bug again.

Incremental rather than rebuilt: extraction costs a model call per batch, so
the pass carries a watermark and only reads what it has not seen. A full
rebuild is available for when the extractor itself changes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lyra_memory.store import Store
from lyra_memory.store.passes.entities import EntityPass
from lyra_memory.store.schema import ENTITY_KINDS


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def _atom(store: Store, text: str, source: str = "cli", ts: float | None = None) -> int:
    return await store.append_atom(
        speaker="wilson", source=source, text=text,
        ts=ts if ts is not None else time.time())


def _llm_returning(mapping: dict[str, list[dict]]):
    """A stand-in extractor: returns whatever the fixture says for each atom."""
    async def _llm(prompt: str) -> str:
        found = []
        for needle, entities in mapping.items():
            if needle in prompt:
                found.extend(entities)
        return json.dumps(found)
    return _llm


# ── extraction ───────────────────────────────────────────────────────────────

async def test_entities_and_links_are_written(store: Store):
    atom_id = await _atom(store, "wilson is building lyra")
    llm = _llm_returning({"wilson is building lyra": [
        {"name": "wilson", "kind": "person", "confidence": 0.95},
        {"name": "lyra", "kind": "project", "confidence": 0.9},
    ]})

    await EntityPass(store, llm=llm).run()

    async with store.db.execute("SELECT name, kind FROM entities ORDER BY name") as cur:
        assert await cur.fetchall() == [("lyra", "project"), ("wilson", "person")]
    async with store.db.execute(
        "SELECT COUNT(*) FROM atom_entities WHERE atom_id = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] == 2


async def test_confidence_is_recorded(store: Store):
    await _atom(store, "wilson is building lyra")
    llm = _llm_returning({"wilson": [
        {"name": "wilson", "kind": "person", "confidence": 0.75}]})
    await EntityPass(store, llm=llm).run()

    async with store.db.execute("SELECT confidence FROM atom_entities") as cur:
        assert (await cur.fetchone())[0] == pytest.approx(0.75)


async def test_the_same_entity_across_atoms_is_one_row(store: Store):
    now = time.time() - 1000
    await _atom(store, "kokoro is the default engine", ts=now)
    await _atom(store, "kokoro sounds better than coqui", ts=now + 500)
    llm = _llm_returning({"kokoro": [
        {"name": "kokoro", "kind": "topic", "confidence": 0.9}]})

    await EntityPass(store, llm=llm).run()

    async with store.db.execute("SELECT COUNT(*) FROM entities") as cur:
        assert (await cur.fetchone())[0] == 1
    async with store.db.execute("SELECT COUNT(*) FROM atom_entities") as cur:
        assert (await cur.fetchone())[0] == 2


async def test_first_and_last_seen_span_the_atoms(store: Store):
    now = time.time() - 10_000
    await _atom(store, "kokoro first mention", ts=now)
    await _atom(store, "kokoro later mention", ts=now + 5000)
    llm = _llm_returning({"kokoro": [
        {"name": "kokoro", "kind": "topic", "confidence": 0.9}]})

    await EntityPass(store, llm=llm).run()

    async with store.db.execute("SELECT first_seen, last_seen FROM entities") as cur:
        first_seen, last_seen = await cur.fetchone()
    assert first_seen == pytest.approx(now)
    assert last_seen == pytest.approx(now + 5000)


async def test_names_are_normalized_to_one_case(store: Store):
    """'Kokoro' and 'kokoro' are one entity, or subject-keyed fact lookup
    silently misses half its rows."""
    await _atom(store, "Kokoro is the engine")
    await _atom(store, "kokoro again")
    llm = _llm_returning({
        "Kokoro is the engine": [{"name": "Kokoro", "kind": "topic", "confidence": 0.9}],
        "kokoro again": [{"name": "kokoro", "kind": "topic", "confidence": 0.9}],
    })

    await EntityPass(store, llm=llm).run()
    async with store.db.execute("SELECT COUNT(*) FROM entities") as cur:
        assert (await cur.fetchone())[0] == 1


# ── deterministic file/path detection ────────────────────────────────────────

async def test_file_paths_are_found_without_the_model(store: Store):
    """Exact strings are the thing a model is worst at reproducing and the
    thing an index most needs verbatim."""
    async def _finds_nothing(prompt: str) -> str:
        return "[]"

    await _atom(store, "the bug was in lyra_embodiment/avatar.js all along")
    await EntityPass(store, llm=_finds_nothing).run()

    async with store.db.execute("SELECT name, kind FROM entities") as cur:
        rows = await cur.fetchall()
    assert ("lyra_embodiment/avatar.js", "file") in rows


async def test_a_bare_sentence_is_not_a_file(store: Store):
    async def _finds_nothing(prompt: str) -> str:
        return "[]"

    await _atom(store, "we talked about it and moved on")
    await EntityPass(store, llm=_finds_nothing).run()
    async with store.db.execute("SELECT COUNT(*) FROM entities") as cur:
        assert (await cur.fetchone())[0] == 0


# ── vocabulary ───────────────────────────────────────────────────────────────

async def test_unknown_kinds_are_rejected(store: Store):
    await _atom(store, "something")
    llm = _llm_returning({"something": [
        {"name": "thing", "kind": "spaceship", "confidence": 0.9}]})

    with pytest.raises(ValueError, match="spaceship"):
        await EntityPass(store, llm=llm).run()


def test_kinds_match_the_sheet():
    assert ENTITY_KINDS == {"project", "person", "repo", "file", "topic"}


# ── incremental, and re-derivable on demand ──────────────────────────────────

async def test_pass_is_idempotent(store: Store):
    await _atom(store, "kokoro is the default")
    llm = _llm_returning({"kokoro": [
        {"name": "kokoro", "kind": "topic", "confidence": 0.9}]})

    await EntityPass(store, llm=llm).run()
    await EntityPass(store, llm=llm).run()

    async with store.db.execute("SELECT COUNT(*) FROM atom_entities") as cur:
        assert (await cur.fetchone())[0] == 1


async def test_already_processed_atoms_are_not_re_read(store: Store):
    seen = []

    async def _counting(prompt: str) -> str:
        seen.append(prompt)
        return "[]"

    await _atom(store, "the first atom")
    await EntityPass(store, llm=_counting).run()
    first_round = len(seen)

    await EntityPass(store, llm=_counting).run()
    assert len(seen) == first_round, "the same atoms were sent to the model twice"

    await _atom(store, "a new atom arrives")
    await EntityPass(store, llm=_counting).run()
    assert len(seen) > first_round, "a new atom was never processed"


async def test_rebuild_reprocesses_everything(store: Store):
    """For when the extractor itself changes."""
    await _atom(store, "kokoro is the default")
    llm = _llm_returning({"kokoro": [
        {"name": "kokoro", "kind": "topic", "confidence": 0.9}]})
    await EntityPass(store, llm=llm).run()

    seen = []

    async def _counting(prompt: str) -> str:
        seen.append(prompt)
        return "[]"

    await EntityPass(store, llm=_counting, rebuild=True).run()
    assert seen, "rebuild did not re-read the atoms"


# ── documents ────────────────────────────────────────────────────────────────

async def test_entities_are_extracted_from_documents_too(store: Store):
    """Documents produce facts, and facts are subject-keyed on entities — so
    unlike the dream pass, this one reads sandbox_read atoms. The link records
    which atom it came from, so provenance survives."""
    atom_id = await _atom(store, "the resume mentions FGCU", source="sandbox_read")
    llm = _llm_returning({"FGCU": [
        {"name": "FGCU", "kind": "topic", "confidence": 0.8}]})

    await EntityPass(store, llm=llm).run()

    async with store.db.execute(
        "SELECT a.source FROM atom_entities ae JOIN atoms a ON a.id = ae.atom_id"
    ) as cur:
        assert (await cur.fetchone())[0] == "sandbox_read"
    assert atom_id is not None


# ── failure ──────────────────────────────────────────────────────────────────

async def test_a_malformed_extraction_does_not_write_a_partial_batch(store: Store):
    await _atom(store, "one")
    await _atom(store, "two")

    calls = {"n": 0}

    async def _fails_on_second(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("extractor died")
        return json.dumps([{"name": "one", "kind": "topic", "confidence": 0.9}])

    with pytest.raises(RuntimeError):
        await EntityPass(store, llm=_fails_on_second, batch_size=1).run()
    await store.db.rollback()

    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        assert (await cur.fetchone())[0] == 2
