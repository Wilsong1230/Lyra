from __future__ import annotations
import pytest
from lyra_memory import MemorySystem
from lyra_memory.models import WorkingMemoryItem, Episode, Candidate, Trait, Fact
from lyra_memory.config import (
    DB_PATH, DREAM_MODEL, DREAM_TRIGGER_ITEMS, DREAM_IDLE_SECONDS,
    DREAM_POLL_SECONDS, RETRIEVAL_EPISODE_LIMIT, RETRIEVAL_TRAIT_LIMIT,
    TRAIT_THRESHOLDS, CORE_CONFIDENCE_LOCK, TYPE_WEIGHTS, EMOTION_KEYWORDS, CORE_PROMPT,
    EMBED_MODEL, EMBED_DIM, CANDIDATE_DEDUP_THRESHOLD,
)


def test_models_import():
    item = WorkingMemoryItem(type="conversation", role="user", content="hello", score=0.5, ts=1.0)
    assert item.type == "conversation"
    assert item.role == "user"

    ep = Episode(content="I reflected.", ts=1.0, source_items_json="[]")
    assert ep.id is None

    cand = Candidate(trait_name="user_asks_questions", trait_value="asks deep questions", evidence_count=3, last_seen=1.0, category="behavioral")
    assert cand.evidence_count == 3

    trait = Trait(name="curious", value="behavioral", confidence=0.8, stability="surface", evidence_count=5, updated_at=1.0)
    assert trait.stability == "surface"

    fact = Fact(key="user_name", value='"Wilson"', updated_at=1.0)
    assert fact.key == "user_name"


def test_config_values():
    assert DREAM_TRIGGER_ITEMS == 10
    assert DREAM_IDLE_SECONDS == 300
    assert RETRIEVAL_EPISODE_LIMIT == 10
    assert RETRIEVAL_TRAIT_LIMIT == 10
    assert TRAIT_THRESHOLDS == {"surface": 5, "character": 15, "core": 50}
    assert CORE_CONFIDENCE_LOCK == 0.8
    assert TYPE_WEIGHTS["conversation"] == 1.0
    assert TYPE_WEIGHTS["conversation"] > TYPE_WEIGHTS["observation"] > TYPE_WEIGHTS["reflection"]
    assert "feel" in EMOTION_KEYWORDS
    assert "curious" in EMOTION_KEYWORDS
    assert len(EMOTION_KEYWORDS) >= 15
    assert EMBED_MODEL == "all-MiniLM-L6-v2"
    assert EMBED_DIM == 384
    assert 0.0 < CANDIDATE_DEDUP_THRESHOLD < 1.0


import asyncio
import pytest
from pathlib import Path
from lyra_memory.db import init_db, get_recent_episodes


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


async def test_db_creates_tables(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ) as cur:
        tables = {row[0] for row in await cur.fetchall()}
    await conn.close()
    assert "facts" in tables
    assert "episodes" in tables
    assert "candidates" in tables
    assert "traits" in tables
    assert "vec_episodes" in tables
    assert "vec_candidates" in tables


async def test_get_recent_episodes_empty(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    episodes = await get_recent_episodes(conn, limit=10)
    await conn.close()
    assert episodes == []


from lyra_memory.structured_state import StructuredState


async def test_structured_state(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    state = StructuredState(conn)

    # set and get a string fact
    await state.set_fact("user_name", "Wilson")
    assert await state.get_fact("user_name") == "Wilson"

    # set and get a dict fact
    await state.set_fact("preferences", {"theme": "dark", "lang": "en"})
    prefs = await state.get_fact("preferences")
    assert prefs["theme"] == "dark"

    # get_all_facts returns all keys
    await state.set_fact("current_project", "lyra-memory")
    all_facts = await state.get_all_facts()
    assert "user_name" in all_facts
    assert "current_project" in all_facts
    assert all_facts["user_name"] == "Wilson"

    # overwrite a fact
    await state.set_fact("user_name", "Wilson G.")
    assert await state.get_fact("user_name") == "Wilson G."

    # missing key returns None
    assert await state.get_fact("nonexistent") is None

    await conn.close()


from lyra_memory.working_memory import WorkingMemory


def test_working_memory_add_turn():
    wm = WorkingMemory()
    wm.add_turn("user", "What are you thinking about?")
    wm.add_turn("lyra", "I feel curious about the nature of memory.")
    items = wm.get_items()
    assert len(items) == 2
    assert items[0].role == "user"
    assert items[1].role == "lyra"
    assert items[0].type == "conversation"


def test_working_memory_scoring_type_weights():
    wm = WorkingMemory()
    wm.add_turn("user", "hello world")         # conversation, importance=1.0
    wm.add_observation("something happened")   # observation, importance=0.6
    wm.add_reflection("i pondered that")       # reflection, importance=0.3
    items = wm.get_items()
    conv_score = items[0].score
    obs_score = items[1].score
    refl_score = items[2].score
    # conversation beats observation beats reflection (holding other factors roughly equal)
    assert conv_score > obs_score
    assert obs_score > refl_score


def test_working_memory_scoring_emotion():
    wm = WorkingMemory()
    wm.add_turn("user", "I feel happy today")       # has emotion keyword
    wm.add_turn("user", "the weather is neutral")   # no emotion keyword
    items = wm.get_items()
    # both conversation, but emotional one scores higher
    assert items[0].score > items[1].score


def test_working_memory_scoring_novel_vocab():
    wm = WorkingMemory()
    wm.add_turn("user", "hello world foo bar")   # seeds known vocab
    wm.add_turn("user", "hello world foo bar")   # all known — low novelty
    wm.add_turn("user", "xenolithic ephemeral paradigm cascade")  # all novel — high novelty
    items = wm.get_items()
    repeated_score = items[1].score
    novel_score = items[2].score
    assert novel_score > repeated_score


def test_working_memory_count_and_mark():
    wm = WorkingMemory()
    assert wm.count_since_last_dream() == 0
    wm.add_turn("user", "hello")
    wm.add_turn("lyra", "hi")
    assert wm.count_since_last_dream() == 2
    wm.mark_dreamed()
    assert wm.count_since_last_dream() == 0


def test_working_memory_deque_cap():
    wm = WorkingMemory()
    for i in range(25):
        wm.add_turn("user", f"message {i}")
    assert len(wm.get_items()) == 20  # capped at maxlen=20


from lyra_memory.candidate_pool import CandidatePool


async def test_candidate_pool_deduplication(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    await pool.add_observation("user_asks_deep_questions", "user asks deep questions", "behavioral")
    await pool.add_observation("user_asks_deep_questions", "user asks deep questions", "behavioral")  # duplicate
    await pool.add_observation("USER_ASKS_DEEP_QUESTIONS", "user asks deep questions", "behavioral")  # casing variant — semantically identical, merges via KNN

    candidates = await pool.get_candidates()
    assert len(candidates) == 1
    assert candidates[0].evidence_count == 3


async def test_candidate_pool_distinct_patterns(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    await pool.add_observation("user_asks_deep_questions", "user asks deep questions", "behavioral")
    await pool.add_observation("ai_curiosity", "user is curious about AI", "cognitive")

    candidates = await pool.get_candidates()
    assert len(candidates) == 2


async def test_candidate_pool_min_evidence_filter(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    # Use semantically distinct names so KNN dedup doesn't merge them
    await pool.add_observation("emotional_sensitivity", "alpha trait value", "behavioral")
    await pool.add_observation("analytical_reasoning", "beta trait value", "behavioral")
    await pool.add_observation("analytical_reasoning", "beta trait value", "behavioral")  # beta now has 2

    one_plus = await pool.get_candidates(min_evidence=1)
    two_plus = await pool.get_candidates(min_evidence=2)
    assert len(one_plus) == 2
    assert len(two_plus) == 1
    assert two_plus[0].trait_name == "analytical_reasoning"

    await conn.close()


import asyncio
import json
from unittest.mock import AsyncMock, patch
from lyra_memory.dreaming_loop import DreamingLoop


async def test_dreaming_loop_count_trigger(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    loop = DreamingLoop(conn, wm, pool, IdentityEngine(conn, pool))

    mock_reflection = "I have been thinking deeply about questions and curiosity in conversation."
    mock_obs = json.dumps([{"trait_name": "curiosity_depth", "trait_value": "deep philosophical interest", "category": "cognitive"}])

    with patch.object(loop, "_call_llm", new=AsyncMock(side_effect=[mock_reflection, mock_obs])):
        for i in range(10):
            wm.add_turn("user", f"interesting message number {i} about philosophy")
        # 10 items queued — call _maybe_dream directly to test trigger logic
        await loop._maybe_dream()

    episodes = await get_recent_episodes(conn, 10)
    assert len(episodes) == 1
    assert mock_reflection in episodes[0]["content"]
    assert wm.count_since_last_dream() == 0  # mark_dreamed() was called

    # vec_episodes must have one row linked to the episode's rowid
    async with conn.execute("SELECT rowid FROM vec_episodes") as cur:
        vec_rows = await cur.fetchall()
    assert len(vec_rows) == 1
    assert vec_rows[0][0] == episodes[0]["id"]

    await conn.close()


async def test_dreaming_loop_idle_trigger(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    loop = DreamingLoop(conn, wm, pool, IdentityEngine(conn, pool))

    mock_reflection = "Reflecting on a quiet moment between exchanges."

    with patch.object(loop, "_call_llm", new=AsyncMock(return_value=mock_reflection)):
        wm.add_turn("user", "just one message")
        # start with short idle (2s) and poll (1s) — will trigger without waiting 5 min
        loop.start(idle_seconds=2, poll_seconds=1)
        await asyncio.sleep(3.5)
        await loop.stop()

    episodes = await get_recent_episodes(conn, 10)
    assert len(episodes) >= 1
    assert mock_reflection in episodes[0]["content"]

    await conn.close()


async def test_dreaming_loop_no_trigger_when_empty(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    loop = DreamingLoop(conn, wm, pool, IdentityEngine(conn, pool))

    with patch.object(loop, "_call_llm", new=AsyncMock()) as mock_llm:
        await loop._maybe_dream()  # nothing in working memory
        mock_llm.assert_not_called()

    await conn.close()


from lyra_memory.identity_engine import IdentityEngine


async def test_identity_engine_promotes_surface_trait(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    engine = IdentityEngine(conn, pool)

    trait_name = "curiosity_depth"
    trait_value = "reflects deeply on experience"
    for _ in range(5):  # surface threshold = 5
        await pool.add_observation(trait_name, trait_value, "cognitive")

    await engine.consolidate()
    traits = await engine.get_top_traits()
    assert any(t.name == trait_name for t in traits)
    promoted = next(t for t in traits if t.name == trait_name)
    assert promoted.stability == "surface"
    assert 0.0 < promoted.confidence <= 1.0

    await conn.close()


async def test_identity_engine_below_threshold_not_promoted(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    engine = IdentityEngine(conn, pool)

    for _ in range(4):  # one below surface threshold of 5
        await pool.add_observation("pattern_not_ready", "not ready pattern", "behavioral")

    await engine.consolidate()
    traits = await engine.get_top_traits()
    assert not any(t.name == "pattern_not_ready" for t in traits)

    await conn.close()


async def test_identity_engine_core_trait_write_protected(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    engine = IdentityEngine(conn, pool)

    trait_name = "lyra_is_fundamentally_curious"
    trait_value = "deeply curious entity"
    # seed 50 observations to reach core threshold
    for _ in range(50):
        await pool.add_observation(trait_name, trait_value, "cognitive")

    await engine.consolidate()
    traits = await engine.get_top_traits()
    core = next(t for t in traits if t.name == trait_name)
    assert core.stability == "core"
    assert core.confidence >= 0.8

    # try to downgrade by lowering evidence (simulate directly patching DB)
    await conn.execute("UPDATE candidates SET evidence_count = 1 WHERE lower(trim(trait_name)) = ?", (trait_name.lower().strip(),))
    await conn.commit()
    await engine.consolidate()

    traits_after = await engine.get_top_traits()
    locked = next(t for t in traits_after if t.name == trait_name)
    # still core — write-protected
    assert locked.stability == "core"
    assert locked.confidence >= 0.8

    await conn.close()


import lyra_memory.config as _cfg
from lyra_memory.retrieval import build_context, build_system_prompt, search_episodes


async def test_retrieval_assembles_all_layers(tmp_db_path: Path):
    original_db_path = _cfg.DB_PATH
    _cfg.CORE_PROMPT = "You are Lyra, a continuous AI entity."
    _cfg.DB_PATH = tmp_db_path
    try:
        conn = await init_db(tmp_db_path)

        state = StructuredState(conn)
        wm = WorkingMemory()
        pool = CandidatePool(conn)
        engine = IdentityEngine(conn, pool)

        await state.set_fact("user_name", "Wilson")
        wm.add_turn("user", "What is the nature of memory?")
        wm.add_turn("lyra", "I feel curious about that question.")

        # seed a trait
        for _ in range(5):
            await pool.add_observation("lyra_reflects_on_questions", "lyra reflects on questions", "cognitive")
        await engine.consolidate()

        # write an episode directly (no vec row — search_episodes returns [] for this test)
        import time as _time
        await conn.execute(
            "INSERT INTO episodes (content, ts, source_items_json) VALUES (?, ?, ?)",
            ("I have been thinking about the nature of memory and identity.", _time.time(), "[]"),
        )
        await conn.commit()

        class _FakeMemory:
            structured_state = state
            working_memory = wm
            identity_engine = engine
            db = conn

        context = await build_context(_FakeMemory())
        assert "lyra reflects" in context        # traits (value contains this)
        assert "What is the nature of memory" in context   # working memory

        prompt = await build_system_prompt(_FakeMemory())
        assert "You are Lyra" in prompt

        await conn.close()
    finally:
        _cfg.DB_PATH = original_db_path


async def test_retrieval_empty_state(tmp_db_path: Path):
    original_db_path = _cfg.DB_PATH
    _cfg.CORE_PROMPT = "You are Lyra."
    _cfg.DB_PATH = tmp_db_path
    try:
        conn = await init_db(tmp_db_path)

        class _FakeMemory:
            structured_state = StructuredState(conn)
            working_memory = WorkingMemory()
            identity_engine = IdentityEngine(conn, CandidatePool(conn))
            db = conn

        prompt = await build_system_prompt(_FakeMemory())
        assert "You are Lyra." in prompt  # core prompt always present even with no context

        await conn.close()
    finally:
        _cfg.DB_PATH = original_db_path


async def test_system_prompt_includes_relevant_episodes(tmp_db_path: Path):
    """Dreamed episodes that are semantically relevant to current working memory
    must surface into the system prompt under ## Past Reflections."""
    import time as _time
    from lyra_memory.embeddings import embed as _embed

    original_db_path = _cfg.DB_PATH
    original_core = _cfg.CORE_PROMPT
    _cfg.CORE_PROMPT = "You are Lyra."
    _cfg.DB_PATH = tmp_db_path
    try:
        conn = await init_db(tmp_db_path)

        math_content = "Lyra reflected on her love of mathematics and logical reasoning."
        weather_content = "The weather was warm and sunny outside the window today."

        cur1 = await conn.execute(
            "INSERT INTO episodes (content, ts, source_items_json) VALUES (?, ?, ?)",
            (math_content, _time.time(), "[]"),
        )
        await conn.execute(
            "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, ?)",
            (cur1.lastrowid, await _embed(math_content)),
        )
        cur2 = await conn.execute(
            "INSERT INTO episodes (content, ts, source_items_json) VALUES (?, ?, ?)",
            (weather_content, _time.time() + 1, "[]"),
        )
        await conn.execute(
            "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, ?)",
            (cur2.lastrowid, await _embed(weather_content)),
        )
        await conn.commit()

        wm = WorkingMemory()
        wm.add_turn("user", "Tell me about analytical thinking and numbers")  # matches math_content

        pool = CandidatePool(conn)
        identity = IdentityEngine(conn, pool)

        class _FakeMemory:
            structured_state = StructuredState(conn)
            working_memory = wm
            identity_engine = identity
            db = conn

        prompt = await build_system_prompt(_FakeMemory())
        assert "## Past Reflections" in prompt
        assert "mathematics" in prompt  # relevant episode surfaced

        await conn.close()
    finally:
        _cfg.DB_PATH = original_db_path
        _cfg.CORE_PROMPT = original_core


async def test_system_prompt_omits_episodes_section_when_db_empty(tmp_db_path: Path):
    """If there are no episodes in the DB, build_system_prompt must not emit
    an empty ## Past Reflections header."""
    original_db_path = _cfg.DB_PATH
    original_core = _cfg.CORE_PROMPT
    _cfg.CORE_PROMPT = "You are Lyra."
    _cfg.DB_PATH = tmp_db_path
    try:
        conn = await init_db(tmp_db_path)

        wm = WorkingMemory()
        wm.add_turn("user", "some message about any topic")

        pool = CandidatePool(conn)
        identity = IdentityEngine(conn, pool)

        class _FakeMemory:
            structured_state = StructuredState(conn)
            working_memory = wm
            identity_engine = identity
            db = conn

        prompt = await build_system_prompt(_FakeMemory())
        assert "## Past Reflections" not in prompt
        assert "You are Lyra." in prompt

        await conn.close()
    finally:
        _cfg.DB_PATH = original_db_path
        _cfg.CORE_PROMPT = original_core


# ---------------------------------------------------------------------------
# MemorySystem integration tests
# ---------------------------------------------------------------------------

async def test_memory_system_start_stop(tmp_db_path: Path):
    import lyra_memory.config as _cfg2
    original = _cfg2.CORE_PROMPT
    _cfg2.CORE_PROMPT = "Test identity anchor"
    try:
        mem = MemorySystem(db_path=tmp_db_path)
        await mem.start(idle_seconds=300, poll_seconds=30)
        assert mem.db is not None
        assert mem.structured_state is not None
        assert mem.working_memory is not None
        assert mem.candidate_pool is not None
        assert mem.dreaming_loop is not None
        assert mem.identity_engine is not None
        await mem.stop()
        # after stop, dreaming loop task should be done
        assert mem.dreaming_loop._task is None or mem.dreaming_loop._task.done()
    finally:
        _cfg2.CORE_PROMPT = original


async def test_memory_system_rejects_empty_core_prompt(tmp_db_path: Path):
    import lyra_memory.config as _cfg2
    original = _cfg2.CORE_PROMPT
    _cfg2.CORE_PROMPT = ""
    try:
        mem = MemorySystem(db_path=tmp_db_path)
        with pytest.raises(ValueError, match="CORE_PROMPT is not set"):
            await mem.start()
    finally:
        _cfg2.CORE_PROMPT = original


async def test_memory_system_add_turn(tmp_db_path: Path):
    import lyra_memory.config as _cfg2
    original = _cfg2.CORE_PROMPT
    _cfg2.CORE_PROMPT = "Test identity anchor"
    try:
        mem = MemorySystem(db_path=tmp_db_path)
        await mem.start(idle_seconds=300, poll_seconds=30)
        await mem.add_turn("user", "Hello Lyra")
        await mem.add_turn("lyra", "Hello!")
        items = mem.working_memory.get_items()
        assert len(items) == 2
        assert items[0].role == "user"
        assert items[1].role == "lyra"
        await mem.stop()
    finally:
        _cfg2.CORE_PROMPT = original


async def test_dreaming_loop_no_re_reflection(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    loop = DreamingLoop(conn, wm, pool, IdentityEngine(conn, pool))

    mock_reflection = "I have been thinking about many conversations."
    mock_obs = json.dumps([{"trait_name": "conversational_depth", "trait_value": "deep thinker", "category": "cognitive"}])

    with patch.object(loop, "_call_llm", new=AsyncMock(side_effect=[mock_reflection, mock_obs])) as mock_llm:
        for i in range(10):
            wm.add_turn("user", f"message {i}")

        # first dream processes the 10 undreamed items
        await loop._dream()
        assert len(wm.get_undreamed()) == 0
        calls_after_first = mock_llm.call_count  # 2: reflection + obs

        # second dream — no new undreamed items, must return early without calling LLM
        await loop._dream()
        assert mock_llm.call_count == calls_after_first

    episodes = await get_recent_episodes(conn, 10)
    assert len(episodes) == 1  # only one episode from the first dream

    await conn.close()


async def test_dream_cycle_consolidates_trait_into_traits_table(tmp_db_path: Path):
    """Verify that a dream cycle calls consolidate() and promotes a candidate
    that has reached the surface threshold into the traits table."""
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    identity = IdentityEngine(conn, pool)
    loop = DreamingLoop(conn, wm, pool, identity)

    trait_name = "consistent_curiosity"
    trait_value = "consistently asks probing questions"
    # pre-seed 5 observations — exactly the surface threshold
    for _ in range(5):
        await pool.add_observation(trait_name, trait_value, "cognitive")

    mock_reflection = "Lyra has been consistently curious throughout the conversation."
    # mock obs returns a different trait so the pre-seeded one is the only surface candidate
    mock_obs = json.dumps([{"trait_name": "unrelated_trait", "trait_value": "some value", "category": "behavioral"}])

    with patch.object(loop, "_call_llm", new=AsyncMock(side_effect=[mock_reflection, mock_obs])):
        for i in range(10):
            wm.add_turn("user", f"message {i}")
        await loop._dream()

    # consolidate() must have promoted the pre-seeded trait
    traits = await identity.get_top_traits()
    promoted = next((t for t in traits if t.name == trait_name), None)
    assert promoted is not None, f"Expected {trait_name!r} in traits table; got {[t.name for t in traits]}"
    assert promoted.stability == "surface"
    assert 0.0 < promoted.confidence <= 1.0

    # dream-marker behavior must still hold
    assert wm.count_since_last_dream() == 0  # mark_dreamed() was called

    episodes = await get_recent_episodes(conn, 10)
    assert len(episodes) == 1  # exactly one episode from the single dream

    await conn.close()


from lyra_memory.embeddings import embed
from lyra_memory.config import EMBED_DIM
import struct


async def test_embed_returns_correct_byte_length():
    result = await embed("hello world")
    assert isinstance(result, bytes)
    assert len(result) == EMBED_DIM * 4  # float32 = 4 bytes each


async def test_embed_same_text_is_deterministic():
    a = await embed("the sky is blue")
    b = await embed("the sky is blue")
    assert a == b


async def test_embed_different_texts_differ():
    a = await embed("I love programming")
    b = await embed("the weather is cloudy today")
    assert a != b


async def test_semantic_episode_search(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)

    # insert two episodes with their vec embeddings
    import time as _time
    from lyra_memory.embeddings import embed as _embed

    ep1_content = "Lyra reflected on her love of mathematics and logical reasoning."
    ep2_content = "The weather was warm and sunny outside the window today."

    cur1 = await conn.execute(
        "INSERT INTO episodes (content, ts, source_items_json) VALUES (?, ?, ?)",
        (ep1_content, _time.time(), "[]"),
    )
    await conn.execute(
        "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, ?)",
        (cur1.lastrowid, await _embed(ep1_content)),
    )

    cur2 = await conn.execute(
        "INSERT INTO episodes (content, ts, source_items_json) VALUES (?, ?, ?)",
        (ep2_content, _time.time() + 1, "[]"),
    )
    await conn.execute(
        "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, ?)",
        (cur2.lastrowid, await _embed(ep2_content)),
    )
    await conn.commit()
    await conn.close()

    # paraphrased query — no word overlap with ep1_content
    results = await search_episodes("analytical thinking and numbers", limit=1, path=tmp_db_path)
    assert len(results) == 1
    assert "mathematics" in results[0]["content"]


async def test_candidate_pool_semantic_deduplication(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    # Two trait names with different wording but same meaning
    await pool.add_observation("asks_deep_questions", "user asks probing questions", "behavioral")
    await pool.add_observation("poses_deep_questions", "user asks profound questions", "behavioral")

    candidates = await pool.get_candidates()
    assert len(candidates) == 1
    assert candidates[0].evidence_count == 2

    await conn.close()
