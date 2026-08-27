"""Step 3 — context assembly.

Pinned order, byte budgets, three retrieval paths, dedupe, speaker weighting,
a synthesis pass, and `context_log` including misses.

Position is load-bearing: models weight the start and end of context most, so
stable identity goes at the top and the live conversation at the end, with
retrieved material in the middle where it informs without dominating. That
ordering is asserted here rather than left incidental.

Retrieval *quality* is not asserted here — it cannot be, without real
embeddings and a hand-labeled set. That is what
`lyra_memory.store.evaluate` is for; see MANUAL.md.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lyra_memory.config import (
    CONTEXT_BUDGETS,
    NON_CONVERSATION_BUDGET,
    TRAIT_CONFIDENCE_FLOOR,
)
from lyra_memory.store import Store
from lyra_memory.store.context import (
    BLOCK_ORDER,
    build_context,
    estimate_tokens,
)


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def _seed_atoms(store: Store, texts: list[str], speaker: str = "wilson",
                      base_ts: float | None = None) -> list[int]:
    base = base_ts if base_ts is not None else time.time() - 10_000
    ids = []
    for i, text in enumerate(texts):
        ids.append(await store.append_atom(
            speaker=speaker, source="cli", text=text, ts=base + i))
    return ids


# ── budgets and order ────────────────────────────────────────────────────────

def test_block_order_is_pinned():
    assert BLOCK_ORDER == ["facts", "traits", "commitments", "recall", "recent"]


def test_budgets_match_the_sheet():
    assert CONTEXT_BUDGETS["facts"] == 200
    assert CONTEXT_BUDGETS["traits"] == 100
    assert CONTEXT_BUDGETS["commitments"] == 100
    assert CONTEXT_BUDGETS["recall"] == 400


def test_non_conversation_budget_is_under_1500():
    assert sum(CONTEXT_BUDGETS.values()) < NON_CONVERSATION_BUDGET
    assert NON_CONVERSATION_BUDGET <= 1500


async def test_blocks_appear_in_pinned_order(store: Store):
    now = time.time()
    await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
        " VALUES (?,?,?,?,?,?)",
        (now, "lyra", "Lyra's store lives at ~/.lyra/store.db", "stated", 1.0, now))
    await store.db.execute(
        "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        ("terse", "prefers terse deliverables", 0.9, "character", 20, now))
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, status)"
        " VALUES (?,?,?,?)",
        (now, "migrate the database", "wilson", "open"))
    await store.db.commit()
    await _seed_atoms(store, ["something about lyra and the store"])

    result = await build_context(store, query="tell me about lyra")

    positions = [result.text.find(h) for h in
                 ("## Facts", "## Traits", "## Open commitments", "## Recall", "## Recent")]
    present = [p for p in positions if p >= 0]
    assert present == sorted(present), f"blocks out of order: {positions}"
    assert positions[0] >= 0, "facts block missing"


async def test_each_block_respects_its_budget(store: Store):
    """Hard truncation, not best effort."""
    now = time.time()
    for i in range(80):
        await store.db.execute(
            "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
            " VALUES (?,?,?,?,?,?)",
            (now + i, "lyra", f"fact number {i} about lyra, padded out with words", "stated", 1.0, now + i))
        await store.db.execute(
            "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (f"trait_{i}", f"a described behaviour number {i} with padding", 0.9, "surface", 5, now))
        await store.db.execute(
            "INSERT INTO commitments (created_ts, text, owner, status) VALUES (?,?,?,?)",
            (now + i, f"commitment number {i} with some padding words", "wilson", "open"))
    await store.db.commit()
    await _seed_atoms(store, [f"an atom about lyra number {i} with padding" for i in range(60)])

    result = await build_context(store, query="lyra")

    for name, budget in CONTEXT_BUDGETS.items():
        block = result.blocks.get(name, "")
        assert estimate_tokens(block) <= budget, (
            f"{name} block used {estimate_tokens(block)} tokens, budget {budget}")


async def test_non_conversation_total_stays_under_budget(store: Store):
    now = time.time()
    for i in range(100):
        await store.db.execute(
            "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
            " VALUES (?,?,?,?,?,?)",
            (now + i, "lyra", f"padded fact {i} " + "x" * 100, "stated", 1.0, now + i))
    await store.db.commit()
    await _seed_atoms(store, [f"atom {i} " + "y" * 100 for i in range(50)])

    result = await build_context(store, query="lyra")
    non_conversation = sum(
        estimate_tokens(result.blocks.get(b, "")) for b in BLOCK_ORDER if b != "recent")
    assert non_conversation < NON_CONVERSATION_BUDGET


async def test_empty_store_still_returns(store: Store):
    result = await build_context(store, query="anything at all")
    assert isinstance(result.text, str)
    assert result.atom_ids == []


# ── facts ────────────────────────────────────────────────────────────────────

async def test_facts_match_subject_exactly_not_by_similarity(store: Store):
    now = time.time()
    for subject, text in [("fgcu", "Wilson studies at FGCU"),
                          ("kokoro", "Kokoro is the default TTS engine")]:
        await store.db.execute(
            "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
            " VALUES (?,?,?,?,?,?)", (now, subject, text, "stated", 1.0, now))
    await store.db.commit()

    result = await build_context(store, query="what do you know about kokoro?")
    assert "Kokoro is the default TTS engine" in result.text
    assert "FGCU" not in result.text


async def test_facts_exclude_stamped_valid_until(store: Store):
    now = time.time()
    await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from, valid_until)"
        " VALUES (?,?,?,?,?,?,?)",
        (now, "kokoro", "an old superseded claim", "stated", 1.0, now, now))
    await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
        " VALUES (?,?,?,?,?,?)",
        (now, "kokoro", "the current claim", "stated", 1.0, now))
    await store.db.commit()

    result = await build_context(store, query="kokoro")
    assert "the current claim" in result.text
    assert "an old superseded claim" not in result.text


async def test_facts_are_newest_first(store: Store):
    now = time.time()
    for i, text in enumerate(["oldest claim", "middle claim", "newest claim"]):
        await store.db.execute(
            "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
            " VALUES (?,?,?,?,?,?)", (now + i, "kokoro", text, "stated", 1.0, now + i))
    await store.db.commit()

    result = await build_context(store, query="kokoro")
    block = result.blocks["facts"]
    assert block.index("newest claim") < block.index("oldest claim")


async def test_conflicting_facts_both_inject(store: Store):
    """v1 detects and does not resolve. Both rows stay live, newest first, and
    an LLM handles the contradiction without help."""
    now = time.time()
    cur = await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
        " VALUES (?,?,?,?,?,?)", (now, "wilson", "is at FGCU", "stated", 1.0, now))
    first = cur.lastrowid
    await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from, conflict_with)"
        " VALUES (?,?,?,?,?,?,?)",
        (now + 1, "wilson", "graduated from FGCU", "stated", 1.0, now + 1, first))
    await store.db.commit()

    result = await build_context(store, query="tell me about wilson")
    assert "is at FGCU" in result.text
    assert "graduated from FGCU" in result.text


async def test_fact_ids_are_logged(store: Store):
    now = time.time()
    cur = await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
        " VALUES (?,?,?,?,?,?)", (now, "kokoro", "the default engine", "stated", 1.0, now))
    await store.db.commit()
    result = await build_context(store, query="kokoro")
    assert result.fact_ids == [cur.lastrowid]


# ── traits ───────────────────────────────────────────────────────────────────

async def test_traits_below_confidence_floor_are_excluded(store: Store):
    now = time.time()
    await store.db.execute(
        "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        ("confident", "a well evidenced behaviour", TRAIT_CONFIDENCE_FLOOR + 0.1,
         "character", 20, now))
    await store.db.execute(
        "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        ("tentative", "a barely evidenced behaviour", TRAIT_CONFIDENCE_FLOOR - 0.1,
         "surface", 5, now))
    await store.db.commit()

    result = await build_context(store, query="anything")
    assert "a well evidenced behaviour" in result.text
    assert "a barely evidenced behaviour" not in result.text


# ── commitments ──────────────────────────────────────────────────────────────

async def test_only_open_commitments_are_injected(store: Store):
    now = time.time()
    for text, status in [("still open", "open"), ("already done", "done"),
                         ("was dropped", "dropped")]:
        await store.db.execute(
            "INSERT INTO commitments (created_ts, text, owner, status) VALUES (?,?,?,?)",
            (now, text, "lyra", status))
    await store.db.commit()

    result = await build_context(store, query="anything")
    assert "still open" in result.text
    assert "already done" not in result.text
    assert "was dropped" not in result.text


async def test_commitments_surface_unprompted(store: Store):
    """The one store that asserts itself rather than waiting for relevance."""
    now = time.time()
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, status) VALUES (?,?,?,?)",
        (now, "migrate the database", "wilson", "open"))
    await store.db.commit()

    result = await build_context(store, query="what is the weather like")
    assert "migrate the database" in result.text


async def test_many_commitments_keep_oldest_and_soonest_due(store: Store):
    now = time.time()
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, status, due_ts) VALUES (?,?,?,?,?)",
        (now - 10_000, "the oldest open loop", "lyra", "open", None))
    for i in range(20):
        await store.db.execute(
            "INSERT INTO commitments (created_ts, text, owner, status, due_ts) VALUES (?,?,?,?,?)",
            (now - 500 + i, f"filler commitment {i}", "lyra", "open", now + 100_000 + i))
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, status, due_ts) VALUES (?,?,?,?,?)",
        (now, "the soonest due", "lyra", "open", now + 60))
    await store.db.commit()

    result = await build_context(store, query="anything")
    block = result.blocks["commitments"]
    assert "the soonest due" in block
    assert "the oldest open loop" in block


async def test_commitment_owner_is_shown(store: Store):
    """She should be able to say 'you said you'd migrate the DB'."""
    now = time.time()
    await store.db.execute(
        "INSERT INTO commitments (created_ts, text, owner, status) VALUES (?,?,?,?)",
        (now, "migrate the database", "wilson", "open"))
    await store.db.commit()
    result = await build_context(store, query="anything")
    assert "wilson" in result.blocks["commitments"].lower()


# ── recall: three paths ──────────────────────────────────────────────────────

async def test_semantic_path_uses_a_floor_not_a_rank_cutoff(store: Store):
    """An unrelated atom must not be injected just for being the nearest one.

    This is the difference between "top k" and "above threshold": with k
    alone, an empty-ish store always returns its best garbage.
    """
    await _seed_atoms(store, ["the cat sat on a woven mat in the sunshine"])

    result = await build_context(
        store, query="quantum chromodynamics lattice gauge renormalization")
    assert "woven mat" not in result.blocks.get("recall", "")


async def test_a_floor_miss_is_recorded(store: Store):
    await _seed_atoms(store, ["the cat sat on a woven mat in the sunshine"])
    result = await build_context(
        store, query="quantum chromodynamics lattice gauge renormalization")
    assert any("semantic" in m for m in result.misses), result.misses


async def test_lexical_path_catches_an_exact_token(store: Store):
    """BM25 exists to catch repo names, filenames, and proper nouns that
    embeddings blur together."""
    await _seed_atoms(store, [
        "we fixed the bug in lyra_embodiment/avatar.js last tuesday",
        "an unrelated conversation about breakfast and the weather",
    ])
    # recent_turns=0 so the two atoms are not already in the verbatim block —
    # recall deliberately does not repeat what `recent` is about to print.
    result = await build_context(
        store, query="lyra_embodiment/avatar.js", recent_turns=0)
    assert "avatar.js" in result.blocks.get("recall", "")


async def test_temporal_pool_does_not_compete_in_knn(store: Store):
    """Recency is a separate pool: a recent atom can surface even when it
    would lose a nearest-neighbour contest outright."""
    old = time.time() - 100_000
    await _seed_atoms(store, ["an old note about the retrieval floor"], base_ts=old)
    await _seed_atoms(
        store, ["yesterday we talked about breakfast"], base_ts=time.time() - 60)

    result = await build_context(store, query="retrieval floor", recent_turns=0)
    recall = result.blocks.get("recall", "")
    assert "breakfast" in recall, "the temporal pool contributed nothing"


async def test_recall_dedupes_near_duplicates(store: Store):
    duplicate = "the segmentation pass writes gap_since_prev in the same pass"
    await _seed_atoms(store, [duplicate, duplicate, duplicate])
    result = await build_context(store, query="segmentation gap_since_prev", recent_turns=0)
    assert result.blocks.get("recall", "").count("gap_since_prev") <= 2


async def test_her_own_turns_are_down_weighted(store: Store):
    """Retrieving her own phrasing and re-saying it is a self-reinforcing
    style loop — the model reads its own output as evidence of how it talks."""
    text = "the retrieval floor is a similarity threshold not a rank cutoff"
    base = time.time() - 10_000
    await store.append_atom(speaker="lyra", source="cli", text=text, ts=base)
    await store.append_atom(speaker="wilson", source="cli", text=text, ts=base + 1)

    result = await build_context(store, query=text, recent_turns=0)
    assert result.atom_ids, "nothing recalled"

    async with store.db.execute(
        f"SELECT speaker FROM atoms WHERE id = {result.atom_ids[0]}"
    ) as cur:
        (first_speaker,) = await cur.fetchone()
    assert first_speaker == "wilson", "her own turn outranked an identical one of his"


# ── synthesis ────────────────────────────────────────────────────────────────

async def test_synthesis_replaces_concatenation(store: Store):
    await _seed_atoms(store, [
        "we chose sqlite-vec over faiss for the vector index",
        "the embedding model is all-MiniLM-L6-v2 at 384 dimensions",
    ])

    async def _synthesize(prompt: str) -> str:
        return "One synthesized paragraph about the vector index choice."

    result = await build_context(
        store, query="vector index choice", synthesize=_synthesize, recent_turns=0)
    assert "One synthesized paragraph" in result.blocks["recall"]


async def test_synthesis_failure_falls_back_to_concatenation(store: Store):
    """Never blocks a turn."""
    await _seed_atoms(store, ["we chose sqlite-vec over faiss for the vector index"])

    async def _broken(prompt: str) -> str:
        raise RuntimeError("the synthesis model is down")

    result = await build_context(
        store, query="sqlite-vec faiss vector index", synthesize=_broken, recent_turns=0)
    assert "sqlite-vec" in result.blocks["recall"]
    assert any("synthesis" in m for m in result.misses), result.misses


async def test_synthesis_output_is_still_budgeted(store: Store):
    await _seed_atoms(store, ["we chose sqlite-vec over faiss for the vector index"])

    async def _runaway(prompt: str) -> str:
        return "padding " * 5000

    result = await build_context(
        store, query="sqlite-vec vector index", synthesize=_runaway, recent_turns=0)
    assert estimate_tokens(result.blocks["recall"]) <= CONTEXT_BUDGETS["recall"]


# ── recent ───────────────────────────────────────────────────────────────────

async def test_recent_block_is_verbatim_and_last(store: Store):
    await _seed_atoms(store, [f"turn number {i} of the conversation" for i in range(20)])
    result = await build_context(store, query="anything", recent_turns=8)
    assert "turn number 19" in result.blocks["recent"]
    assert result.text.rstrip().endswith(result.blocks["recent"].rstrip())


# ── context_log ──────────────────────────────────────────────────────────────

async def test_context_log_records_what_was_injected(store: Store):
    now = time.time()
    await store.db.execute(
        "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
        " VALUES (?,?,?,?,?,?)", (now, "kokoro", "the default engine", "stated", 1.0, now))
    await store.db.commit()
    await _seed_atoms(store, ["a note about kokoro and voices"])

    result = await build_context(store, query="kokoro")
    await store.ingest_turn(
        user_text="tell me about kokoro", lyra_text="it is the default engine",
        source="cli", injected=result.as_log_row())

    async with store.db.execute(
        "SELECT atom_ids, fact_ids, dream_ids, budget_used, misses FROM context_log"
    ) as cur:
        atom_ids, fact_ids, dream_ids, budget_used, misses = await cur.fetchone()

    assert json.loads(fact_ids)
    assert json.loads(atom_ids) == result.atom_ids
    assert json.loads(dream_ids) == result.dream_ids
    assert budget_used == result.budget_used > 0
    assert misses is None or isinstance(json.loads(misses), list)


async def test_misses_are_logged_from_day_one(store: Store):
    """Misses show where the store is thin and which queries have no home."""
    result = await build_context(store, query="a query about something never discussed")
    await store.ingest_turn(
        user_text="a query about something never discussed", lyra_text="I don't know",
        source="cli", injected=result.as_log_row())

    async with store.db.execute("SELECT misses FROM context_log") as cur:
        (misses,) = await cur.fetchone()
    assert misses is not None
    assert json.loads(misses), "an empty store should record misses, not silence"


# ── budget accounting ────────────────────────────────────────────────────────

def test_estimate_tokens_is_monotonic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 4) <= estimate_tokens("a" * 400)
    assert estimate_tokens("a" * 400) > 0


# ── findings from the retrieval baseline ─────────────────────────────────────

async def test_temporal_pool_is_withheld_when_nothing_is_relevant(store: Store):
    """Recall must not be manufactured from recency alone.

    Found by the baseline harness: with the temporal pool ungated, a query the
    store cannot answer still came back with the last ten unrelated atoms
    under a "Recall" heading, which reads as remembering. Both unanswerable
    turns injected (spurious injection 1.00).
    """
    await _seed_atoms(store, [f"a note about the retrieval floor number {i}" for i in range(12)])

    result = await build_context(
        store, query="zzzqqq unrelated nonsense token", recent_turns=0)
    assert result.blocks.get("recall", "") == ""
    assert any("temporal pool withheld" in m for m in result.misses), result.misses


async def test_a_stopword_only_query_matches_nothing_lexically(store: Store):
    """BM25 is for selective tokens. OR-ing 'the/did/we/about' matches the
    whole store and then ranks it at random."""
    await _seed_atoms(store, [f"an atom about the store number {i}" for i in range(12)])

    result = await build_context(store, query="what did we do about it", recent_turns=0)
    assert any("no selective tokens" in m for m in result.misses), result.misses


async def test_selective_token_still_matches(store: Store):
    """The stopword filter must not swallow the query outright."""
    await _seed_atoms(store, [
        "the kokoro engine is the default", "an unrelated note about breakfast"])
    result = await build_context(store, query="what about kokoro", recent_turns=0)
    assert "kokoro" in result.blocks.get("recall", "").lower()
