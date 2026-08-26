# Semantic Search (sqlite-vec + sentence-transformers) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add embedding-based semantic search to lyra-memory using sqlite-vec and sentence-transformers, covering episode search and candidate deduplication.

**Architecture:** A new `embeddings.py` module lazily loads `all-MiniLM-L6-v2` and exposes an async `embed()` that runs encoding in an executor. `db.py` loads the sqlite-vec extension at connection time and creates `vec_episodes`/`vec_candidates` virtual tables. `dreaming_loop.py` inserts episode vectors after writing each episode. `retrieval.py` replaces the LIKE query with a KNN search. `candidate_pool.py` replaces exact-string dedup with a KNN lookup gated on cosine distance threshold.

**Tech Stack:** `sentence-transformers` (all-MiniLM-L6-v2, 384-dim), `sqlite-vec` (vec0 virtual tables, L2 distance on unit-normalized vectors), `aiosqlite` (existing), Python 3.10+.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `lyra_memory/embeddings.py` | Lazy model load, async `embed(text) -> bytes` |
| Modify | `lyra_memory/config.py` | Add `EMBED_MODEL`, `EMBED_DIM`, `CANDIDATE_DEDUP_THRESHOLD` |
| Modify | `lyra_memory/db.py` | Load sqlite-vec extension, create vec virtual tables, export `load_vec_extension` |
| Modify | `lyra_memory/dreaming_loop.py` | Embed episode content, insert into `vec_episodes` after each dream |
| Modify | `lyra_memory/retrieval.py` | Replace LIKE with KNN in `search_episodes`, load vec on its connection |
| Modify | `lyra_memory/candidate_pool.py` | Replace exact-string dedup with semantic KNN dedup |
| Modify | `pyproject.toml` | Add `sentence-transformers` and `sqlite-vec` dependencies |
| Modify | `tests/test_memory.py` | Update `test_db_creates_tables`; update exact-dedup tests; add two semantic tests |

---

## Task 1: Add dependencies and config constants

**Files:**
- Modify: `pyproject.toml`
- Modify: `lyra_memory/config.py`
- Modify: `tests/test_memory.py` (update `test_config_values`)

- [ ] **Step 1.1: Add packages to pyproject.toml**

Replace the `dependencies` line in `pyproject.toml`:

```toml
[project]
name = "lyra-memory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "aiosqlite>=0.19",
    "pydantic>=2.0",
    "sentence-transformers>=3.0",
    "sqlite-vec>=0.1.0",
]
```

- [ ] **Step 1.2: Install updated dependencies**

```bash
cd lyra-memory
source venv/bin/activate
pip install -e ".[dev]"
```

Expected: `sentence-transformers` and `sqlite-vec` install successfully.

- [ ] **Step 1.3: Add constants to config.py**

Open `lyra_memory/config.py` and append these three lines before the final `CORE_PROMPT` line:

```python
EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384
CANDIDATE_DEDUP_THRESHOLD = 0.3  # cosine distance; lower = stricter match
```

Full final state of `config.py`:

```python
from __future__ import annotations
from pathlib import Path

DB_PATH = Path.home() / ".lyra" / "memory.db"

DREAM_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

DREAM_TRIGGER_ITEMS = 10
DREAM_IDLE_SECONDS = 300
DREAM_POLL_SECONDS = 30

RETRIEVAL_EPISODE_LIMIT = 10
RETRIEVAL_TRAIT_LIMIT = 10
SEARCH_EPISODES_DEFAULT_LIMIT = 5

TRAIT_THRESHOLDS: dict[str, int] = {"surface": 5, "character": 15, "core": 50}
CORE_CONFIDENCE_LOCK = 0.8

TYPE_WEIGHTS: dict[str, float] = {
    "conversation": 1.0,
    "observation": 0.6,
    "reflection": 0.3,
}

EMOTION_KEYWORDS: set[str] = {
    "feel", "feeling", "felt",
    "love", "hate", "fear", "afraid", "angry", "sad", "happy",
    "excited", "lonely", "anxious", "proud", "hurt", "joy",
    "wonder", "curious", "confused", "grateful", "frustrated",
    "hope", "regret", "miss", "wish", "care",
}

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384
CANDIDATE_DEDUP_THRESHOLD = 0.3  # cosine distance; lower = stricter match

CORE_PROMPT = ""  # immutable identity anchor — must be written before first use
```

- [ ] **Step 1.4: Update test_config_values to check new constants**

In `tests/test_memory.py`, find `test_config_values` and update its import and assertions:

```python
from lyra_memory.config import (
    DB_PATH, DREAM_MODEL, DREAM_TRIGGER_ITEMS, DREAM_IDLE_SECONDS,
    DREAM_POLL_SECONDS, RETRIEVAL_EPISODE_LIMIT, RETRIEVAL_TRAIT_LIMIT,
    TRAIT_THRESHOLDS, CORE_CONFIDENCE_LOCK, TYPE_WEIGHTS, EMOTION_KEYWORDS, CORE_PROMPT,
    EMBED_MODEL, EMBED_DIM, CANDIDATE_DEDUP_THRESHOLD,
)


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
```

- [ ] **Step 1.5: Run test to verify**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_config_values -v
```

Expected: PASS

- [ ] **Step 1.6: Commit**

```bash
git add lyra-memory/pyproject.toml lyra-memory/lyra_memory/config.py lyra-memory/tests/test_memory.py
git commit -m "feat(memory): add sentence-transformers + sqlite-vec deps and embedding config constants"
```

---

## Task 2: Create embeddings.py

**Files:**
- Create: `lyra_memory/embeddings.py`
- Test: `tests/test_memory.py` (add `test_embed_returns_bytes`, `test_embed_same_text_is_deterministic`, `test_embed_different_texts_differ`)

- [ ] **Step 2.1: Write failing tests**

Append to `tests/test_memory.py`:

```python
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
```

- [ ] **Step 2.2: Run tests to confirm they fail**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_embed_returns_correct_byte_length -v
```

Expected: `ModuleNotFoundError: No module named 'lyra_memory.embeddings'`

- [ ] **Step 2.3: Create lyra_memory/embeddings.py**

```python
from __future__ import annotations
import asyncio
import struct
from typing import TYPE_CHECKING

from lyra_memory.config import EMBED_MODEL, EMBED_DIM

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: "SentenceTransformer | None" = None


def _get_model() -> "SentenceTransformer":
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _embed_sync(text: str) -> bytes:
    vec = _get_model().encode(text, normalize_embeddings=True)
    return struct.pack(f"{EMBED_DIM}f", *vec)


async def embed(text: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embed_sync, text)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_embed_returns_correct_byte_length tests/test_memory.py::test_embed_same_text_is_deterministic tests/test_memory.py::test_embed_different_texts_differ -v
```

Expected: all three PASS (first run may be slow — model downloads ~90 MB)

- [ ] **Step 2.5: Commit**

```bash
git add lyra-memory/lyra_memory/embeddings.py lyra-memory/tests/test_memory.py
git commit -m "feat(memory): add embeddings module with lazy all-MiniLM-L6-v2 model and async embed()"
```

---

## Task 3: Update db.py — load sqlite-vec and create vec virtual tables

**Files:**
- Modify: `lyra_memory/db.py`
- Modify: `tests/test_memory.py` (update `test_db_creates_tables`)

- [ ] **Step 3.1: Update test_db_creates_tables to expect vec tables**

In `tests/test_memory.py`, find `test_db_creates_tables` and update the assertion:

```python
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
```

- [ ] **Step 3.2: Run test to confirm it fails**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_db_creates_tables -v
```

Expected: FAIL — `vec_episodes` and `vec_candidates` not in tables

- [ ] **Step 3.3: Rewrite db.py**

```python
from __future__ import annotations
import asyncio
import sqlite3
import aiosqlite
from pathlib import Path

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content           TEXT NOT NULL,
    ts                REAL NOT NULL,
    source_items_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trait_name     TEXT NOT NULL,
    trait_value    TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    last_seen      REAL NOT NULL,
    category       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    value          TEXT NOT NULL,
    confidence     REAL NOT NULL,
    stability      TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    updated_at     REAL NOT NULL
);
"""

_CREATE_VEC_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
    embedding FLOAT[384]
);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_candidates USING vec0(
    embedding FLOAT[384]
);
"""


def _setup_vec(raw_conn: sqlite3.Connection) -> None:
    try:
        raw_conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(raw_conn)
        raw_conn.enable_load_extension(False)
    except AttributeError as exc:
        raise RuntimeError(
            "sqlite3 extension loading is unavailable on this Python build. "
            "macOS system Python disables extension loading — use Python from "
            "Homebrew (brew install python) or python.org instead."
        ) from exc


async def load_vec_extension(conn: aiosqlite.Connection) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _setup_vec, conn._connection)


async def init_db(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await load_vec_extension(conn)
    await conn.executescript(_CREATE_SQL)
    await conn.executescript(_CREATE_VEC_SQL)
    await conn.commit()
    return conn


async def get_recent_episodes(conn: aiosqlite.Connection, limit: int) -> list[dict]:
    async with conn.execute(
        "SELECT id, content, ts, source_items_json "
        "FROM episodes ORDER BY ts DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"id": r[0], "content": r[1], "ts": r[2], "source_items_json": r[3]}
        for r in reversed(rows)
    ]
```

> **Note:** `conn._connection` is the underlying `sqlite3.Connection` inside `aiosqlite.Connection`. This private attribute is stable across all aiosqlite>=0.19 versions.

- [ ] **Step 3.4: Run test to verify it passes**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_db_creates_tables tests/test_memory.py::test_get_recent_episodes_empty -v
```

Expected: both PASS

- [ ] **Step 3.5: Run full test suite to check for regressions**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest -v
```

Expected: all existing tests pass (the model download may make first run slow)

- [ ] **Step 3.6: Commit**

```bash
git add lyra-memory/lyra_memory/db.py lyra-memory/tests/test_memory.py
git commit -m "feat(memory): load sqlite-vec extension in init_db and create vec_episodes/vec_candidates tables"
```

---

## Task 4: Update dreaming_loop.py — embed episode after insert

**Files:**
- Modify: `lyra_memory/dreaming_loop.py`
- Modify: `tests/test_memory.py` (update `test_dreaming_loop_count_trigger`)

- [ ] **Step 4.1: Write failing test**

In `tests/test_memory.py`, update `test_dreaming_loop_count_trigger` to also assert that `vec_episodes` gets a row:

```python
async def test_dreaming_loop_count_trigger(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    loop = DreamingLoop(conn, wm, pool)

    mock_reflection = "I have been thinking deeply about questions and curiosity in conversation."
    mock_obs = json.dumps([{"trait_name": "curiosity_depth", "trait_value": "deep philosophical interest", "category": "cognitive"}])

    with patch.object(loop, "_call_llm", new=AsyncMock(side_effect=[mock_reflection, mock_obs])):
        for i in range(10):
            wm.add_turn("user", f"interesting message number {i} about philosophy")
        await loop._maybe_dream()

    episodes = await get_recent_episodes(conn, 10)
    assert len(episodes) == 1
    assert mock_reflection in episodes[0]["content"]
    assert wm.count_since_last_dream() == 0

    # vec_episodes must have one row linked to the episode's rowid
    async with conn.execute("SELECT rowid FROM vec_episodes") as cur:
        vec_rows = await cur.fetchall()
    assert len(vec_rows) == 1
    assert vec_rows[0][0] == episodes[0]["id"]

    await conn.close()
```

- [ ] **Step 4.2: Run test to confirm it fails**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_dreaming_loop_count_trigger -v
```

Expected: FAIL — `AssertionError: assert 0 == 1` (vec_episodes empty)

- [ ] **Step 4.3: Update dreaming_loop.py**

Replace the `_dream` method. The new version captures `lastrowid` after the INSERT and embeds content asynchronously. Only the `_dream` method changes; all other methods stay identical.

Full updated `dreaming_loop.py`:

```python
from __future__ import annotations
import asyncio
import json
import os
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import DREAM_MODEL, OPENROUTER_BASE, DREAM_TRIGGER_ITEMS, DREAM_POLL_SECONDS
from lyra_memory.working_memory import WorkingMemory
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.embeddings import embed

_OBS_PROMPT = (
    "Based on this reflection, identify behavioral patterns about Lyra.\n"
    "Return a JSON list of objects, each with:\n"
    '  "trait_name"  — short stable key, e.g. "communication_style"\n'
    '  "trait_value" — the specific observation, e.g. "engineering-focused, concise"\n'
    '  "category"    — one of: behavioral, emotional, relational, cognitive\n'
    "Return only JSON, no other text.\n\nReflection:\n"
)


class DreamingLoop:
    def __init__(self, conn: aiosqlite.Connection, working_memory: WorkingMemory, candidate_pool: CandidatePool) -> None:
        self._conn = conn
        self._wm = working_memory
        self._pool = candidate_pool
        self._task: asyncio.Task | None = None
        self._idle_seconds: int = 300
        self._poll_seconds: int = DREAM_POLL_SECONDS

    def start(self, idle_seconds: int = 300, poll_seconds: int = DREAM_POLL_SECONDS) -> None:
        if self._task and not self._task.done():
            return
        self._idle_seconds = idle_seconds
        self._poll_seconds = poll_seconds
        self._task = asyncio.create_task(self._loop())
        print(f"[{datetime.now().isoformat()}] [DreamingLoop] started (idle={idle_seconds}s poll={poll_seconds}s)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print(f"[{datetime.now().isoformat()}] [DreamingLoop] stopped")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            await self._maybe_dream()

    async def _maybe_dream(self) -> None:
        count = self._wm.count_since_last_dream()
        if count == 0:
            return
        last_turn = self._wm.last_turn_ts()
        idle = (time.time() - last_turn) if last_turn else 0
        if count >= DREAM_TRIGGER_ITEMS or idle >= self._idle_seconds:
            await self._dream()

    async def _dream(self) -> None:
        items = self._wm.get_undreamed()
        if not items:
            return
        snapshot = json.dumps([i.model_dump() for i in items])
        prompt = (
            "You are Lyra, a continuous AI entity. Reflect on these recent experiences in your own words:\n\n"
            + "\n".join(f"[{i.role or i.type}]: {i.content}" for i in items)
        )
        content = await self._call_llm(prompt)
        ts = time.time()
        cur = await self._conn.execute(
            "INSERT INTO episodes (content, ts, source_items_json) VALUES (?, ?, ?)",
            (content, ts, snapshot),
        )
        episode_rowid = cur.lastrowid
        await self._conn.commit()
        print(f"[{datetime.now().isoformat()}] [DreamingLoop] episode written ({len(content)} chars)")

        vec_bytes = await embed(content)
        await self._conn.execute(
            "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, ?)",
            (episode_rowid, vec_bytes),
        )
        await self._conn.commit()

        obs_json = await self._call_llm(_OBS_PROMPT + content)
        try:
            observations = json.loads(obs_json)
            for obs in observations[:3]:
                await self._pool.add_observation(obs["trait_name"], obs["trait_value"], obs["category"])
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[{datetime.now().isoformat()}] [DreamingLoop] structured obs parse error: {e}")

        self._wm.mark_dreamed()

    async def _call_llm(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._call_llm_sync, prompt)

    def _call_llm_sync(self, prompt: str) -> str:
        import http.client
        import urllib.parse
        key = os.environ.get("OPENROUTER_API_KEY", "")
        parsed = urllib.parse.urlparse(OPENROUTER_BASE)
        conn = http.client.HTTPSConnection(parsed.netloc)
        body = json.dumps({"model": DREAM_MODEL, "messages": [{"role": "user", "content": prompt}]})
        conn.request("POST", "/api/v1/chat/completions", body=body,
                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
```

- [ ] **Step 4.4: Run tests to verify**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_dreaming_loop_count_trigger tests/test_memory.py::test_dreaming_loop_no_re_reflection -v
```

Expected: both PASS

- [ ] **Step 4.5: Commit**

```bash
git add lyra-memory/lyra_memory/dreaming_loop.py lyra-memory/tests/test_memory.py
git commit -m "feat(memory): embed episode content and insert into vec_episodes after each dream"
```

---

## Task 5: Update retrieval.py — semantic episode search

**Files:**
- Modify: `lyra_memory/retrieval.py`
- Modify: `tests/test_memory.py` (add `test_semantic_episode_search`)

- [ ] **Step 5.1: Write failing test**

Append to `tests/test_memory.py`:

```python
async def test_semantic_episode_search(tmp_db_path: Path):
    import lyra_memory.config as _cfg
    original_path = _cfg.DB_PATH
    _cfg.DB_PATH = tmp_db_path
    try:
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
        results = await search_episodes("analytical thinking and numbers", limit=1)
        assert len(results) == 1
        assert "mathematics" in results[0]["content"]
    finally:
        _cfg.DB_PATH = original_path
```

- [ ] **Step 5.2: Run test to confirm it fails**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_semantic_episode_search -v
```

Expected: FAIL — LIKE query returns no results for "analytical thinking and numbers" since it's not a substring of either episode

- [ ] **Step 5.3: Rewrite search_episodes in retrieval.py**

Full updated `retrieval.py` (only `search_episodes` changes; `build_context`, `build_system_prompt`, and `get_fact` are unchanged):

```python
from __future__ import annotations
import datetime
import aiosqlite
from lyra_memory import config
from lyra_memory.config import SEARCH_EPISODES_DEFAULT_LIMIT
from lyra_memory.db import load_vec_extension
from lyra_memory.embeddings import embed


async def build_context(memory: object) -> str:
    traits = await memory.identity_engine.get_top_traits()
    working = memory.working_memory.get_items()

    parts: list[str] = []

    if traits:
        parts.append(
            "## Persona Traits\n"
            + "\n".join(
                f"- [{t.stability}] {t.name}: {t.value} (confidence={t.confidence:.2f})"
                for t in traits
            )
        )

    if working:
        parts.append(
            "## Current Experience\n"
            + "\n".join(f"[{i.role or i.type}]: {i.content}" for i in working)
        )

    return "\n\n".join(parts)


async def build_system_prompt(memory: object) -> str:
    context = await build_context(memory)
    return f"{config.CORE_PROMPT}\n\n{context}".strip() if context else config.CORE_PROMPT


async def search_episodes(query: str, limit: int = SEARCH_EPISODES_DEFAULT_LIMIT) -> list[dict]:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] search_episodes query={query!r} limit={limit}")
    query_vec = await embed(query)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await load_vec_extension(conn)
        async with conn.execute(
            "SELECT v.rowid, e.content, e.ts "
            "FROM vec_episodes v "
            "JOIN episodes e ON e.id = v.rowid "
            "WHERE v.embedding MATCH ? "
            "ORDER BY v.distance "
            "LIMIT ?",
            (query_vec, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "content": r[1], "ts": r[2]} for r in rows]


async def get_fact(subject_key: str) -> dict | None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] get_fact key={subject_key!r}")
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT key, value, updated_at FROM facts WHERE key = ?",
            (subject_key,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return {"key": row[0], "value": row[1], "updated_at": row[2]}
```

- [ ] **Step 5.4: Run tests to verify**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_semantic_episode_search tests/test_memory.py::test_retrieval_assembles_all_layers tests/test_memory.py::test_retrieval_empty_state -v
```

Expected: all PASS

- [ ] **Step 5.5: Commit**

```bash
git add lyra-memory/lyra_memory/retrieval.py lyra-memory/tests/test_memory.py
git commit -m "feat(memory): replace LIKE episode search with sqlite-vec KNN semantic search"
```

---

## Task 6: Update candidate_pool.py — semantic dedup

**Files:**
- Modify: `lyra_memory/candidate_pool.py`
- Modify: `tests/test_memory.py` (update existing dedup tests + add semantic dedup test)

- [ ] **Step 6.1: Write new semantic dedup test**

Append to `tests/test_memory.py`:

```python
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
```

- [ ] **Step 6.2: Run test to confirm it fails**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_candidate_pool_semantic_deduplication -v
```

Expected: FAIL — two candidates are created instead of one (exact-match sees them as distinct)

- [ ] **Step 6.3: Update existing dedup tests**

The exact-string dedup tests used lowercase normalization. The new semantic dedup still handles near-identical text (identical strings have cosine distance ≈ 0), so `test_candidate_pool_deduplication` will pass unchanged.

However, `test_candidate_pool_distinct_patterns` uses "user_asks_deep_questions" and "ai_curiosity" — these are semantically distant enough that the threshold (0.3 cosine) won't merge them. No change needed there.

The tests that INSERT via `identity_engine` (e.g., `test_identity_engine_promotes_surface_trait`) use `pool.add_observation` in a loop with the **same trait_name**. These will continue to work since identical strings always land below any cosine threshold.

No changes needed to existing dedup tests.

- [ ] **Step 6.4: Rewrite candidate_pool.py**

```python
from __future__ import annotations
import math
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import CANDIDATE_DEDUP_THRESHOLD
from lyra_memory.embeddings import embed
from lyra_memory.models import Candidate

# cosine distance threshold converted to L2 threshold for normalized vectors:
# cosine_dist = L2_dist² / 2  ⟹  L2_threshold = sqrt(2 * cosine_threshold)
_L2_THRESHOLD = math.sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)


class CandidatePool:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add_observation(self, trait_name: str, trait_value: str, category: str) -> None:
        ts = time.time()
        vec_bytes = await embed(trait_name)

        async with self._conn.execute(
            "SELECT rowid, distance FROM vec_candidates "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
            (vec_bytes,),
        ) as cur:
            nearest = await cur.fetchone()

        if nearest and nearest[1] < _L2_THRESHOLD:
            candidate_id = nearest[0]
            async with self._conn.execute(
                "SELECT evidence_count FROM candidates WHERE id = ?", (candidate_id,)
            ) as cur:
                row = await cur.fetchone()
            new_count = row[0] + 1
            await self._conn.execute(
                "UPDATE candidates SET trait_value = ?, evidence_count = ?, last_seen = ? WHERE id = ?",
                (trait_value, new_count, ts, candidate_id),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INCREMENT id={candidate_id} → count={new_count}")
        else:
            cur = await self._conn.execute(
                "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category)"
                " VALUES (?, ?, 1, ?, ?)",
                (trait_name, trait_value, ts, category),
            )
            candidate_rowid = cur.lastrowid
            await self._conn.execute(
                "INSERT INTO vec_candidates(rowid, embedding) VALUES (?, ?)",
                (candidate_rowid, vec_bytes),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INSERT {trait_name!r} category={category!r}")

        await self._conn.commit()

    async def get_candidates(self, min_evidence: int = 1) -> list[Candidate]:
        async with self._conn.execute(
            "SELECT id, trait_name, trait_value, evidence_count, last_seen, category"
            " FROM candidates WHERE evidence_count >= ? ORDER BY evidence_count DESC",
            (min_evidence,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Candidate(id=r[0], trait_name=r[1], trait_value=r[2], evidence_count=r[3], last_seen=r[4], category=r[5])
            for r in rows
        ]
```

> **Note on `vec_candidates` query:** `vec_candidates` requires at least one row before a `MATCH` query works. When the table is empty, `sqlite-vec` raises an error. The `nearest` check already guards this implicitly because an empty table returns no rows (distance not evaluated) — but sqlite-vec may vary. If you see errors on first insert, add an early return: `if not await _has_any_vec_candidate(conn): insert directly`.

- [ ] **Step 6.5: Run new test to verify it passes**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_candidate_pool_semantic_deduplication -v
```

Expected: PASS

- [ ] **Step 6.6: Run full dedup test suite**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest tests/test_memory.py::test_candidate_pool_deduplication tests/test_memory.py::test_candidate_pool_distinct_patterns tests/test_memory.py::test_candidate_pool_min_evidence_filter -v
```

Expected: all PASS

- [ ] **Step 6.7: Run the full test suite**

```bash
cd lyra-memory && source venv/bin/activate
python -m pytest -v
```

Expected: all tests pass

- [ ] **Step 6.8: Commit**

```bash
git add lyra-memory/lyra_memory/candidate_pool.py lyra-memory/tests/test_memory.py
git commit -m "feat(memory): replace exact-string candidate dedup with semantic KNN dedup via sqlite-vec"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Covered by |
|-------------|-----------|
| `embeddings.py` with lazy all-MiniLM-L6-v2 | Task 2 |
| `embed(text) -> bytes` packed float32 | Task 2, Step 2.3 |
| Lazy load on first call | Task 2, Step 2.3 (`_get_model`) |
| `enable_load_extension` + clear macOS error | Task 3, Step 3.3 (`_setup_vec`) |
| `vec_episodes(episode_id, embedding FLOAT[384])` | Task 3, Step 3.3 |
| `vec_candidates(candidate_id, embedding FLOAT[384])` | Task 3, Step 3.3 |
| After dream insert → embed → insert vec_episodes | Task 4, Step 4.3 |
| `search_episodes` KNN replacing LIKE | Task 5, Step 5.3 |
| Same return shape `[{id, content, ts}]` | Task 5, Step 5.3 |
| Semantic dedup via KNN in `add_observation` | Task 6, Step 6.4 |
| `CANDIDATE_DEDUP_THRESHOLD` config | Task 1, Step 1.3 |
| Remove `_normalize` exact-match path | Task 6, Step 6.4 (method removed) |
| Executor for embedding calls | Tasks 2, done in `embeddings.py` |
| Timestamp logs on every write | Tasks 4, 6 (print statements preserved) |
| Test: semantic candidate dedup merges to 1 | Task 6, Step 6.1 |
| Test: paraphrased query finds episode | Task 5, Step 5.1 |
| `EMBED_MODEL`, `EMBED_DIM` config | Task 1, Step 1.3 |

**Gaps identified and resolved:**

- `vec_candidates` empty-table edge case noted in Task 6, Step 6.4 with guidance
- `search_episodes` needs `load_vec_extension` on its own connection — handled in Task 5, Step 5.3

**Type consistency verified:** `embed()` returns `bytes` throughout; `load_vec_extension` is exported from `db.py` and imported in `retrieval.py`; `cur.lastrowid` used for both `vec_episodes` and `vec_candidates` inserts.
