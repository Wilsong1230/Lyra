"""Step 8 — facts.

Extraction in dream, subject-keyed retrieval (already built in step 3), and
**conflict flagging only**.

The load-bearing constraint: v1 writes no `superseded_by` and stamps no
`valid_until`. It detects a suspected contradiction, writes `conflict_with`,
and stops. Both rows stay live and both inject, newest first, and an LLM
handles "was at FGCU / more recently graduated" without help.

The asymmetry is why. Under-supersession is clutter: visible and recoverable.
Over-supersession silently erases true things: invisible and unrecoverable.
Prefer duplication.

`source_kind` is decided by the channel, never by the model — "you told me"
and "I read it in your resume" are different epistemic states, and a document
must not be able to claim it was stated.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lyra_memory.store import Store
from lyra_memory.store.passes.facts import FactPass, source_kind_for
from lyra_memory.store.schema import SOURCE_KINDS


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def _atom(store: Store, text: str, speaker: str = "wilson",
                source: str = "cli", ts: float | None = None) -> int:
    return await store.append_atom(
        speaker=speaker, source=source, text=text,
        ts=ts if ts is not None else time.time())


def _extractor(facts: list[dict], conflicts: str = "none"):
    async def _llm(prompt: str) -> str:
        if "contradict" in prompt.lower():
            return conflicts
        return json.dumps(facts)
    return _llm


async def _facts(store: Store) -> list[tuple]:
    async with store.db.execute(
        "SELECT id, subject, text, source_kind, confidence, valid_from,"
        " valid_until, superseded_by, conflict_with FROM facts ORDER BY id"
    ) as cur:
        return await cur.fetchall()


# ── extraction ───────────────────────────────────────────────────────────────

async def test_a_fact_is_written_with_its_source_atom(store: Store):
    """The atom stays. Facts are extracted, not moved."""
    atom_id = await _atom(store, "I'm at FGCU studying software engineering")
    llm = _extractor([{"subject": "wilson", "text": "studies at FGCU"}])

    await FactPass(store, llm=llm).run()

    async with store.db.execute("SELECT source_atom_id FROM facts") as cur:
        assert (await cur.fetchone())[0] == atom_id
    async with store.db.execute("SELECT COUNT(*) FROM atoms WHERE id = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] == 1


async def test_subject_is_normalized(store: Store):
    """Retrieval matches on subject exactly, so 'Wilson' and 'wilson' being
    two keys would silently halve every lookup."""
    await _atom(store, "something about him")
    llm = _extractor([{"subject": "Wilson", "text": "studies at FGCU"}])
    await FactPass(store, llm=llm).run()

    async with store.db.execute("SELECT subject FROM facts") as cur:
        assert (await cur.fetchone())[0] == "wilson"


async def test_valid_from_is_set_and_valid_until_is_not(store: Store):
    await _atom(store, "something")
    llm = _extractor([{"subject": "wilson", "text": "studies at FGCU"}])
    await FactPass(store, llm=llm).run()

    (_id, _s, _t, _k, _c, valid_from, valid_until, superseded, _cw) = (await _facts(store))[0]
    assert valid_from > 0
    assert valid_until is None, "v1 must never stamp valid_until"
    assert superseded is None, "v1 must never write superseded_by"


async def test_no_facts_extracted_writes_nothing(store: Store):
    await _atom(store, "just chatter")
    await FactPass(store, llm=_extractor([])).run()
    assert await _facts(store) == []


# ── source_kind is decided by the channel ────────────────────────────────────

def test_source_kinds_match_the_sheet():
    assert SOURCE_KINDS == {"stated", "observed", "document", "inferred"}


def test_wilson_speaking_is_stated():
    assert source_kind_for(speaker="wilson", source="cli") == "stated"


def test_a_document_is_document_even_when_wilson_read_it():
    """A file can assert anything. The channel decides, not the content."""
    assert source_kind_for(speaker="wilson", source="sandbox_read") == "document"


def test_her_own_conclusion_is_inferred():
    assert source_kind_for(speaker="lyra", source="cli") == "inferred"


def test_something_she_saw_is_observed():
    assert source_kind_for(speaker="lyra", source="vision") == "observed"


async def test_the_model_cannot_choose_source_kind(store: Store):
    """A document claiming source_kind='stated' would launder its authority."""
    await _atom(store, "the resume asserts a degree", source="sandbox_read")
    llm = _extractor([
        {"subject": "wilson", "text": "has a degree", "source_kind": "stated"}])

    await FactPass(store, llm=llm).run()

    async with store.db.execute("SELECT source_kind FROM facts") as cur:
        assert (await cur.fetchone())[0] == "document"


async def test_document_facts_are_less_confident_than_stated_ones(store: Store):
    await _atom(store, "he told me directly", speaker="wilson", source="cli")
    await _atom(store, "the resume says so", speaker="wilson", source="sandbox_read")

    async def _llm(prompt: str) -> str:
        if "contradict" in prompt.lower():
            return "none"
        if "told me directly" in prompt:
            return json.dumps([{"subject": "wilson", "text": "stated claim"}])
        return json.dumps([{"subject": "wilson", "text": "document claim"}])

    await FactPass(store, llm=_llm).run()

    async with store.db.execute(
        "SELECT source_kind, confidence FROM facts ORDER BY id") as cur:
        rows = dict(await cur.fetchall())
    assert rows["document"] < rows["stated"]


# ── documents produce facts ──────────────────────────────────────────────────

async def test_documents_do_produce_facts(store: Store):
    """The other half of 'documents produce facts, never traits'. Discovery is
    legitimate: she read something, the reading was an event, the fact points
    at the moment she read it."""
    atom_id = await _atom(store, "read wilson's resume", source="sandbox_read")
    llm = _extractor([{"subject": "wilson", "text": "studied at FGCU"}])

    await FactPass(store, llm=llm).run()

    async with store.db.execute(
        "SELECT source_atom_id, source_kind FROM facts") as cur:
        assert await cur.fetchone() == (atom_id, "document")


# ── conflict: detect, do not resolve ─────────────────────────────────────────

async def test_a_contradiction_is_flagged_not_resolved(store: Store):
    now = time.time() - 1000
    await _atom(store, "I'm at FGCU", ts=now)
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "is at FGCU"}])).run()

    await _atom(store, "I graduated last spring", ts=now + 500)
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "graduated from FGCU"}], conflicts="1")).run()

    rows = await _facts(store)
    assert len(rows) == 2
    older, newer = rows
    assert newer[8] == older[0], "the new fact must point at what it conflicts with"
    assert older[6] is None and newer[6] is None, "neither may be stamped valid_until"
    assert older[7] is None and newer[7] is None, "neither may be superseded"


async def test_both_sides_of_a_conflict_still_inject(store: Store):
    from lyra_memory.store.context import build_context

    now = time.time() - 1000
    await _atom(store, "I'm at FGCU", ts=now)
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "is at FGCU"}])).run()
    await _atom(store, "I graduated", ts=now + 500)
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "graduated from FGCU"}], conflicts="1")).run()

    result = await build_context(store, query="tell me about wilson", recent_turns=0)
    assert "is at FGCU" in result.blocks["facts"]
    assert "graduated from FGCU" in result.blocks["facts"]


async def test_no_conflict_leaves_the_flag_null(store: Store):
    await _atom(store, "a first thing")
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "prefers terse deliverables"}])).run()
    await _atom(store, "a second thing")
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "corrects scope creep mid-session"}],
        conflicts="none")).run()

    rows = await _facts(store)
    assert all(r[8] is None for r in rows)


async def test_conflicts_are_only_compared_within_a_subject(store: Store):
    """Supersession compares within a subject — a set of tens, not thousands."""
    seen = []

    async def _llm(prompt: str) -> str:
        if "contradict" in prompt.lower():
            seen.append(prompt)
            return "none"
        return json.dumps([{"subject": "kokoro", "text": "is the default engine"}])

    await _atom(store, "about wilson")
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "studies at FGCU"}])).run()

    await _atom(store, "about kokoro")
    await FactPass(store, llm=_llm).run()

    assert not any("FGCU" in p for p in seen), (
        "a fact about kokoro was compared against a fact about wilson")


# ── instrumentation: logged, not enforced ────────────────────────────────────

async def test_instrumentation_reports_facts_per_subject_and_conflicts(store: Store):
    await _atom(store, "a thing")
    await FactPass(store, llm=_extractor(
        [{"subject": "wilson", "text": "one"}, {"subject": "wilson", "text": "two"}])).run()

    stats = await FactPass(store, llm=_extractor([])).instrumentation()
    assert stats["facts_per_subject"]["wilson"] == 2
    assert stats["unresolved_conflicts"] == 0


async def test_a_crowded_subject_is_reported_not_blocked(store: Store):
    """Redundancy about a frequently-discussed subject is not itself a defect
    — she has the most experience of Wilson, so she should have the most facts
    about him. The failure to guard is contradictory and unresolvable, not
    numerous."""
    await _atom(store, "a thing")
    many = [{"subject": "wilson", "text": f"observation number {i}"} for i in range(25)]
    await FactPass(store, llm=_extractor(many)).run()

    stats = await FactPass(store, llm=_extractor([])).instrumentation()
    assert stats["facts_per_subject"]["wilson"] == 25
    assert "wilson" in stats["crowded_subjects"]
    assert len(await _facts(store)) == 25, "nothing may be dropped for being numerous"


# ── incremental ──────────────────────────────────────────────────────────────

async def test_atoms_are_not_re_extracted(store: Store):
    await _atom(store, "a thing")
    llm = _extractor([{"subject": "wilson", "text": "studies at FGCU"}])
    await FactPass(store, llm=llm).run()
    await FactPass(store, llm=llm).run()

    assert len(await _facts(store)) == 1


async def test_a_failed_extraction_leaves_atoms_intact(store: Store):
    await _atom(store, "a thing")

    async def _broken(prompt: str) -> str:
        raise RuntimeError("extractor down")

    with pytest.raises(RuntimeError):
        await FactPass(store, llm=_broken).run()
    await store.db.rollback()

    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        assert (await cur.fetchone())[0] == 1
    assert await _facts(store) == []
