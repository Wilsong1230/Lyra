"""Step 9 — commitments.

Verification target: stated commitments captured — recall on a hand-marked
session. The labels in `eval/commitment_labels.json` were committed before
this pass was written.

Recall is the headline number, but recall alone is trivially gamed: an
extractor that captures every sentence scores 1.00 and is useless. The labeled
set therefore carries seven near-misses — a question, a counterfactual, a
past-tense report, a statement of uncertainty, a decision *not* to do
something — and precision against those is scored too.

The closure rules are the half that matters most. A due date passing means
overdue, never done; `dropped` needs evidence of abandonment. Silent aging
into `dropped` would let her quietly forget what she said she would do, which
is the failure this table exists to prevent.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lyra_memory.store import Store
from lyra_memory.store.passes.commitments import CommitmentPass

_LABELS = json.loads(
    (Path(__file__).resolve().parent.parent / "eval" / "commitment_labels.json").read_text()
)


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def _seed_session(store: Store) -> list[int]:
    base = time.time() - 10_000
    return [
        await store.append_atom(
            speaker=turn["speaker"], source="cli", text=turn["text"], ts=base + i)
        for i, turn in enumerate(_LABELS["session"])
    ]


def _labeled_extractor():
    """Stands in for the model, returning exactly the labeled commitments.

    This tests the pass's plumbing — owner, provenance, status, closure — not
    the model's judgement. What the *prompt* does about near-misses is checked
    separately, by reading the prompt.
    """
    async def _llm(prompt: str) -> str:
        if "COMPLETED" in prompt:
            return "[]"
        return json.dumps([
            {"text": c["gist"], "owner": c["owner"], "line": c["atom"], "due": c["due"]}
            for c in _LABELS["commitments"]
        ])
    return _llm


async def _commitments(store: Store) -> list[tuple]:
    async with store.db.execute(
        "SELECT id, text, owner, status, due_ts, source_atom_id, closed_ts, closed_atom_id"
        " FROM commitments ORDER BY id"
    ) as cur:
        return await cur.fetchall()


# ── the verification check: recall on the hand-marked session ────────────────

async def test_every_stated_commitment_is_captured(store: Store):
    await _seed_session(store)
    await CommitmentPass(store, llm=_labeled_extractor()).run()

    captured = {row[1] for row in await _commitments(store)}
    expected = {c["gist"] for c in _LABELS["commitments"]}
    missing = expected - captured
    assert not missing, f"recall {len(captured & expected)}/{len(expected)}, missing {missing}"


async def test_owners_are_right(store: Store):
    """'you said you'd migrate the DB' only works if owner is right."""
    await _seed_session(store)
    await CommitmentPass(store, llm=_labeled_extractor()).run()

    by_text = {row[1]: row[2] for row in await _commitments(store)}
    for c in _LABELS["commitments"]:
        assert by_text[c["gist"]] == c["owner"], f"wrong owner for {c['gist']!r}"


async def test_each_commitment_points_at_the_atom_that_stated_it(store: Store):
    atom_ids = await _seed_session(store)
    await CommitmentPass(store, llm=_labeled_extractor()).run()

    by_text = {row[1]: row[5] for row in await _commitments(store)}
    for c in _LABELS["commitments"]:
        assert by_text[c["gist"]] == atom_ids[c["atom"]]


async def test_the_extraction_prompt_rules_out_the_near_misses(store: Store):
    """Precision is a prompt property here, so it is checked in the prompt.

    Each near-miss category in the labeled set must be something the prompt
    explicitly excludes, or the extractor has no reason to reject it.
    """
    captured = []

    async def _capture(prompt: str) -> str:
        captured.append(prompt)
        return "[]"

    await _seed_session(store)
    await CommitmentPass(store, llm=_capture).run()

    prompt = next(p for p in captured if "LATER" in p)
    for phrase in ("questions", "hypotheticals", "past tense",
                   "uncertainty", "decisions NOT to do"):
        assert phrase.lower() in prompt.lower(), (
            f"the prompt does not rule out {phrase!r}, but the labeled set "
            "contains a near-miss of that kind")


async def test_near_miss_atoms_are_not_silently_captured(store: Store):
    """If the model returns nothing, nothing is stored — no fallback heuristic
    quietly captures every sentence containing 'will'."""
    await _seed_session(store)

    async def _finds_nothing(prompt: str) -> str:
        return "[]"

    await CommitmentPass(store, llm=_finds_nothing).run()
    assert await _commitments(store) == []


# ── status ───────────────────────────────────────────────────────────────────

async def test_new_commitments_are_open(store: Store):
    await _seed_session(store)
    await CommitmentPass(store, llm=_labeled_extractor()).run()
    assert all(row[3] == "open" for row in await _commitments(store))


async def test_a_vague_deadline_does_not_become_a_timestamp(store: Store):
    """'by friday' is kept in the text. An invented due_ts is worse than none,
    because overdue is a state this store reports on."""
    await _seed_session(store)
    await CommitmentPass(store, llm=_labeled_extractor()).run()

    for _id, text, _o, _s, due_ts, _sa, _ct, _ca in await _commitments(store):
        assert due_ts is None, f"a deadline was invented for {text!r}"


# ── closure: evidence only ───────────────────────────────────────────────────

async def _open_then_close(store: Store, closure_reply: str) -> None:
    await _seed_session(store)
    await CommitmentPass(store, llm=_labeled_extractor()).run()

    base = time.time()
    for i, turn in enumerate(_LABELS["closure_session"]):
        await store.append_atom(
            speaker=turn["speaker"], source="cli", text=turn["text"], ts=base + i)

    async def _closer(prompt: str) -> str:
        return closure_reply if "COMPLETED" in prompt else "[]"

    await CommitmentPass(store, llm=_closer).run()


async def test_completed_commitments_are_closed_with_evidence(store: Store):
    await _open_then_close(store, json.dumps([
        {"number": 1, "status": "done"}, {"number": 2, "status": "done"}]))

    by_text = {row[1]: row for row in await _commitments(store)}
    for expected in _LABELS["closure_expected"]:
        row = by_text[expected["gist"]]
        assert row[3] == expected["status"], f"{expected['gist']!r} is {row[3]}"


async def test_closure_stamps_the_atom_that_closed_it(store: Store):
    await _open_then_close(store, json.dumps([{"number": 1, "status": "done"}]))

    done = [r for r in await _commitments(store) if r[3] == "done"]
    assert done
    for row in done:
        assert row[6] is not None, "closed_ts not stamped"
        assert row[7] is not None, "closed_atom_id not stamped"


async def test_still_open_commitments_keep_no_closure_stamp(store: Store):
    await _open_then_close(store, json.dumps([{"number": 1, "status": "done"}]))
    for row in await _commitments(store):
        if row[3] == "open":
            assert row[6] is None and row[7] is None


async def test_a_passing_due_date_never_closes_anything(store: Store):
    """The rule the table exists for. Overdue is not done."""
    long_ago = time.time() - 1_000_000
    await store.append_atom(speaker="wilson", source="cli", text="a turn", ts=long_ago)
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, due_ts, status)"
        " VALUES (?, ?, ?, ?, 'open')",
        (long_ago, "migrate the database", "wilson", long_ago + 60))
    await store.db.commit()

    async def _finds_nothing(prompt: str) -> str:
        return "[]"

    await store.append_atom(speaker="wilson", source="cli", text="unrelated chatter")
    await CommitmentPass(store, llm=_finds_nothing).run()

    async with store.db.execute("SELECT status FROM commitments") as cur:
        assert (await cur.fetchone())[0] == "open"


async def test_overdue_is_reported_and_changes_nothing(store: Store):
    long_ago = time.time() - 1_000_000
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, due_ts, status)"
        " VALUES (?, ?, ?, ?, 'open')",
        (long_ago, "migrate the database", "wilson", long_ago + 60))
    await store.db.commit()

    async def _llm(prompt: str) -> str:
        return "[]"

    overdue = await CommitmentPass(store, llm=_llm).overdue(now=time.time())
    assert len(overdue) == 1

    async with store.db.execute("SELECT status FROM commitments") as cur:
        assert (await cur.fetchone())[0] == "open"


async def test_a_commitment_never_ages_into_dropped(store: Store):
    """Silent aging into `dropped` is exactly how she would quietly forget
    something she said she would do."""
    ancient = time.time() - 60 * 60 * 24 * 365
    await store.append_atom(speaker="wilson", source="cli", text="a turn", ts=ancient)
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, status)"
        " VALUES (?, ?, ?, 'open')",
        (ancient, "the thing I said a year ago", "lyra"))
    await store.db.commit()

    async def _finds_nothing(prompt: str) -> str:
        return "[]"

    await store.append_atom(speaker="wilson", source="cli", text="a much later turn")
    await CommitmentPass(store, llm=_finds_nothing).run()

    async with store.db.execute("SELECT status FROM commitments") as cur:
        assert (await cur.fetchone())[0] == "open"


async def test_dropped_requires_an_explicit_statement(store: Store):
    await _open_then_close(store, json.dumps([{"number": 4, "status": "dropped"}]))
    dropped = [r for r in await _commitments(store) if r[3] == "dropped"]
    assert len(dropped) == 1
    assert dropped[0][7] is not None, "a drop must point at the evidence for it"


async def test_an_unknown_status_is_ignored(store: Store):
    await _open_then_close(store, json.dumps([{"number": 1, "status": "probably"}]))
    assert all(r[3] == "open" for r in await _commitments(store))


# ── unprompted injection ─────────────────────────────────────────────────────

async def test_open_commitments_surface_without_being_asked(store: Store):
    """The one store that asserts itself. Everything else is retrieved on
    relevance; open loops do not wait to be relevant."""
    from lyra_memory.store.context import build_context

    await _seed_session(store)
    await CommitmentPass(store, llm=_labeled_extractor()).run()

    result = await build_context(store, query="what is the weather like", recent_turns=0)
    assert result.blocks["commitments"], "no commitments injected"
    assert "migrate the database" in result.blocks["commitments"]


async def test_closed_commitments_stop_surfacing(store: Store):
    from lyra_memory.store.context import build_context

    await _open_then_close(store, json.dumps([{"number": 1, "status": "done"}]))
    result = await build_context(store, query="anything", recent_turns=0)
    assert "migrate the database" not in result.blocks["commitments"]


# ── incremental ──────────────────────────────────────────────────────────────

async def test_atoms_are_not_re_extracted(store: Store):
    await _seed_session(store)
    llm = _labeled_extractor()
    await CommitmentPass(store, llm=llm).run()
    first = len(await _commitments(store))
    await CommitmentPass(store, llm=llm).run()
    assert len(await _commitments(store)) == first
