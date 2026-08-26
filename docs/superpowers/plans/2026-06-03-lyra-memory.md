# Lyra Memory System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `lyra-memory/` — a five-layer persistent memory system for Lyra as a continuous AI entity, with no sessions, async SQLite storage, a scoring-based working memory, an LLM-powered dreaming loop, and an identity consolidation engine.

**Architecture:** Independent layer objects (`StructuredState`, `WorkingMemory`, `CandidatePool`, `DreamingLoop`, `IdentityEngine`) wired by a `MemorySystem` coordinator in `__init__.py`. All DB operations are async via `aiosqlite`. The dreaming loop runs as an `asyncio.Task` and is independently start/stoppable.

**Tech Stack:** Python 3.10+, `aiosqlite`, `pydantic` v2, `pytest`, `pytest-asyncio`, `http.client` (stdlib) for OpenRouter calls.

---

## File Map

| File | Responsibility |
|------|---------------|
| `lyra-memory/pyproject.toml` | Package metadata, dev deps, pytest config |
| `lyra-memory/requirements.txt` | Runtime + dev pip deps |
| `lyra-memory/start.sh` | Activate venv, run `python -m lyra_memory` |
| `lyra-memory/lyra_memory/__init__.py` | `MemorySystem` — wires all layers, owns lifecycle |
| `lyra-memory/lyra_memory/config.py` | All constants, thresholds, model IDs, keyword sets |
| `lyra-memory/lyra_memory/models.py` | All Pydantic schemas |
| `lyra-memory/lyra_memory/db.py` | `init_db()`, `get_recent_episodes()` |
| `lyra-memory/lyra_memory/structured_state.py` | Layer 1: key-value fact store |
| `lyra-memory/lyra_memory/working_memory.py` | Layer 2: scored in-memory deque |
| `lyra-memory/lyra_memory/candidate_pool.py` | Layer 3: deduplicating pattern log |
| `lyra-memory/lyra_memory/dreaming_loop.py` | Layer 4: async LLM reflection task |
| `lyra-memory/lyra_memory/identity_engine.py` | Layer 5: trait consolidation |
| `lyra-memory/lyra_memory/retrieval.py` | Context + system prompt builder |
| `lyra-memory/lyra_memory/__main__.py` | Entry point for `python -m lyra_memory` |
| `lyra-memory/tests/__init__.py` | Empty |
| `lyra-memory/tests/test_memory.py` | Full sequential integration test |

---

## Task 1: Scaffold the package

**Files:**
- Create: `lyra-memory/pyproject.toml`
- Create: `lyra-memory/requirements.txt`
- Create: `lyra-memory/start.sh`
- Create: `lyra-memory/lyra_memory/__init__.py` (empty placeholder)
- Create: `lyra-memory/lyra_memory/__main__.py`
- Create: `lyra-memory/tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p lyra-memory/lyra_memory
mkdir -p lyra-memory/tests
```

- [ ] **Step 2: Create `lyra-memory/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "lyra-memory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["aiosqlite>=0.19", "pydantic>=2.0"]

[project.optional-dependencies]
dev = ["pytest>=8,<9", "pytest-asyncio>=0.23"]

[tool.setuptools.packages.find]
include = ["lyra_memory*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Create `lyra-memory/requirements.txt`**

```
aiosqlite>=0.19
pydantic>=2.0
pytest>=8,<9
pytest-asyncio>=0.23
```

- [ ] **Step 4: Create `lyra-memory/start.sh`**

```bash
#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -e ".[dev]"
else
    source venv/bin/activate
fi
python -m lyra_memory
```

```bash
chmod +x lyra-memory/start.sh
```

- [ ] **Step 5: Create empty placeholder files**

`lyra-memory/lyra_memory/__init__.py`:
```python
```

`lyra-memory/tests/__init__.py`:
```python
```

- [ ] **Step 6: Create `lyra-memory/lyra_memory/__main__.py`**

```python
from __future__ import annotations
import asyncio
import signal
from lyra_memory import MemorySystem


async def main() -> None:
    memory = MemorySystem()
    await memory.start()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    await stop.wait()
    await memory.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Set up venv and install**

```bash
cd lyra-memory
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

Expected: `Successfully installed lyra-memory-0.1.0`

- [ ] **Step 8: Verify pytest collects with no errors**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest --collect-only
```

Expected: `no tests ran` (no test file yet) with exit code 0 or 5.

- [ ] **Step 9: Commit**

```bash
git -C lyra-memory init
git -C lyra-memory add .
git -C lyra-memory commit -m "feat: scaffold lyra-memory package"
```

---

## Task 2: `models.py` — Pydantic schemas

**Files:**
- Create: `lyra-memory/lyra_memory/models.py`

- [ ] **Step 1: Write the failing import test**

Add to `lyra-memory/tests/test_memory.py`:

```python
from __future__ import annotations
from lyra_memory.models import WorkingMemoryItem, Episode, Candidate, Trait, Fact


def test_models_import():
    item = WorkingMemoryItem(type="conversation", role="user", content="hello", score=0.5, ts=1.0)
    assert item.type == "conversation"
    assert item.role == "user"

    ep = Episode(content="I reflected.", ts=1.0, source_items_json="[]")
    assert ep.id is None

    cand = Candidate(pattern="user asks questions", evidence_count=3, last_seen=1.0, category="behavioral")
    assert cand.evidence_count == 3

    trait = Trait(name="curious", value="behavioral", confidence=0.8, stability="surface", evidence_count=5, updated_at=1.0)
    assert trait.stability == "surface"

    fact = Fact(key="user_name", value='"Wilson"', updated_at=1.0)
    assert fact.key == "user_name"
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_models_import -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `models` doesn't exist yet.

- [ ] **Step 3: Create `lyra-memory/lyra_memory/models.py`**

```python
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class WorkingMemoryItem(BaseModel):
    type: Literal["conversation", "observation", "reflection"]
    role: Literal["user", "lyra"] | None = None
    content: str
    score: float
    ts: float


class Episode(BaseModel):
    id: int | None = None
    content: str
    ts: float
    source_items_json: str


class Candidate(BaseModel):
    id: int | None = None
    pattern: str
    evidence_count: int = 1
    last_seen: float
    category: Literal["behavioral", "emotional", "relational", "cognitive"]


class Trait(BaseModel):
    id: int | None = None
    name: str
    value: str
    confidence: float
    stability: Literal["surface", "character", "core"]
    evidence_count: int
    updated_at: float


class Fact(BaseModel):
    key: str
    value: str
    updated_at: float
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_models_import -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/models.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add Pydantic models"
```

---

## Task 3: `config.py` — constants and keywords

**Files:**
- Create: `lyra-memory/lyra_memory/config.py`

- [ ] **Step 1: Write the failing import test**

Append to `lyra-memory/tests/test_memory.py`:

```python
from lyra_memory.config import (
    DB_PATH, DREAM_MODEL, DREAM_TRIGGER_ITEMS, DREAM_IDLE_SECONDS,
    DREAM_POLL_SECONDS, RETRIEVAL_EPISODE_LIMIT, RETRIEVAL_TRAIT_LIMIT,
    TRAIT_THRESHOLDS, CORE_CONFIDENCE_LOCK, TYPE_WEIGHTS, EMOTION_KEYWORDS, CORE_PROMPT,
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
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_config_values -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/config.py`**

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

CORE_PROMPT = ""  # immutable identity anchor — must be written before first use
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_config_values -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/config.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add config constants"
```

---

## Task 4: `db.py` — async SQLite setup

**Files:**
- Create: `lyra-memory/lyra_memory/db.py`

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
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
        "SELECT name FROM sqlite_master WHERE type='table'"
    ) as cur:
        tables = {row[0] for row in await cur.fetchall()}
    await conn.close()
    assert tables == {"facts", "episodes", "candidates", "traits"}


async def test_get_recent_episodes_empty(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    episodes = await get_recent_episodes(conn, limit=10)
    await conn.close()
    assert episodes == []
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_db_creates_tables tests/test_memory.py::test_get_recent_episodes_empty -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/db.py`**

```python
from __future__ import annotations
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
    pattern        TEXT NOT NULL,
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


async def init_db(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await conn.executescript(_CREATE_SQL)
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

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_db_creates_tables tests/test_memory.py::test_get_recent_episodes_empty -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/db.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add async db init and episode query"
```

---

## Task 5: `structured_state.py` — Layer 1

**Files:**
- Create: `lyra-memory/lyra_memory/structured_state.py`

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
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
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_structured_state -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/structured_state.py`**

```python
from __future__ import annotations
import json
import time
import aiosqlite
from datetime import datetime


class StructuredState:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def set_fact(self, key: str, value: object) -> None:
        ts = time.time()
        serialized = json.dumps(value)
        await self._conn.execute(
            "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, serialized, ts),
        )
        await self._conn.commit()
        print(f"[{datetime.now().isoformat()}] [StructuredState] SET {key!r} = {value!r}")

    async def get_fact(self, key: str) -> object:
        async with self._conn.execute(
            "SELECT value FROM facts WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def get_all_facts(self) -> dict:
        async with self._conn.execute("SELECT key, value FROM facts") as cur:
            rows = await cur.fetchall()
        return {k: json.loads(v) for k, v in rows}
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py::test_structured_state -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/structured_state.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add Layer 1 StructuredState"
```

---

## Task 6: `working_memory.py` — Layer 2

**Files:**
- Create: `lyra-memory/lyra_memory/working_memory.py`

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
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
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "working_memory" -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/working_memory.py`**

```python
from __future__ import annotations
import time
from collections import deque
from datetime import datetime
from lyra_memory.config import TYPE_WEIGHTS, EMOTION_KEYWORDS
from lyra_memory.models import WorkingMemoryItem


class WorkingMemory:
    def __init__(self) -> None:
        self._deque: deque[WorkingMemoryItem] = deque(maxlen=20)
        self._items_since_dream: int = 0
        self._last_turn_ts: float = 0.0

    def _score(self, item_type: str, content: str) -> float:
        importance = TYPE_WEIGHTS[item_type]

        punct = 0.3 if ("?" in content or "!" in content) else 0.0
        known = {w for item in self._deque for w in item.content.lower().split()}
        new_words = set(content.lower().split())
        novelty = min((len(new_words - known) / max(len(new_words), 1)) * 1.5, 0.5)
        surprise = min(punct + novelty, 1.0)

        emotion = 1.0 if any(w in content.lower() for w in EMOTION_KEYWORDS) else 0.0
        return (importance + surprise + emotion) / 3

    def _append(self, item: WorkingMemoryItem) -> None:
        self._deque.append(item)
        self._items_since_dream += 1
        print(f"[{datetime.now().isoformat()}] [WorkingMemory] +{item.type} score={item.score:.2f} role={item.role!r}")

    def add_turn(self, role: str, content: str) -> None:
        self._last_turn_ts = time.time()
        self._append(WorkingMemoryItem(
            type="conversation", role=role, content=content,
            score=self._score("conversation", content), ts=self._last_turn_ts,
        ))

    def add_observation(self, content: str) -> None:
        self._append(WorkingMemoryItem(
            type="observation", role=None, content=content,
            score=self._score("observation", content), ts=time.time(),
        ))

    def add_reflection(self, content: str) -> None:
        self._append(WorkingMemoryItem(
            type="reflection", role=None, content=content,
            score=self._score("reflection", content), ts=time.time(),
        ))

    def get_items(self) -> list[WorkingMemoryItem]:
        return list(self._deque)

    def count_since_last_dream(self) -> int:
        return self._items_since_dream

    def last_turn_ts(self) -> float:
        return self._last_turn_ts

    def mark_dreamed(self) -> None:
        self._items_since_dream = 0
        print(f"[{datetime.now().isoformat()}] [WorkingMemory] dream marker reset")
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "working_memory" -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/working_memory.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add Layer 2 WorkingMemory with scored deque"
```

---

## Task 7: `candidate_pool.py` — Layer 3

**Files:**
- Create: `lyra-memory/lyra_memory/candidate_pool.py`

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
from lyra_memory.candidate_pool import CandidatePool


async def test_candidate_pool_deduplication(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    await pool.add_observation("user asks deep questions", "behavioral")
    await pool.add_observation("user asks deep questions", "behavioral")  # duplicate
    await pool.add_observation("USER ASKS DEEP QUESTIONS", "behavioral")  # same after normalize

    candidates = await pool.get_candidates()
    assert len(candidates) == 1
    assert candidates[0].evidence_count == 3


async def test_candidate_pool_distinct_patterns(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    await pool.add_observation("user asks deep questions", "behavioral")
    await pool.add_observation("user is curious about AI", "cognitive")

    candidates = await pool.get_candidates()
    assert len(candidates) == 2


async def test_candidate_pool_min_evidence_filter(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    await pool.add_observation("pattern alpha", "behavioral")
    await pool.add_observation("pattern beta", "behavioral")
    await pool.add_observation("pattern beta", "behavioral")  # beta now has 2

    one_plus = await pool.get_candidates(min_evidence=1)
    two_plus = await pool.get_candidates(min_evidence=2)
    assert len(one_plus) == 2
    assert len(two_plus) == 1
    assert two_plus[0].pattern == "pattern beta"

    await conn.close()
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "candidate_pool" -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/candidate_pool.py`**

```python
from __future__ import annotations
import time
import aiosqlite
from datetime import datetime
from lyra_memory.models import Candidate


class CandidatePool:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _normalize(text: str) -> str:
        return text.lower().strip()

    async def add_observation(self, pattern: str, category: str) -> None:
        normalized = self._normalize(pattern)
        ts = time.time()
        async with self._conn.execute(
            "SELECT id, evidence_count FROM candidates WHERE lower(trim(pattern)) = ?",
            (normalized,),
        ) as cur:
            row = await cur.fetchone()

        if row:
            new_count = row[1] + 1
            await self._conn.execute(
                "UPDATE candidates SET evidence_count = ?, last_seen = ? WHERE id = ?",
                (new_count, ts, row[0]),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INCREMENT {pattern!r} → count={new_count}")
        else:
            await self._conn.execute(
                "INSERT INTO candidates (pattern, evidence_count, last_seen, category)"
                " VALUES (?, 1, ?, ?)",
                (pattern, ts, category),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INSERT {pattern!r} category={category!r}")

        await self._conn.commit()

    async def get_candidates(self, min_evidence: int = 1) -> list[Candidate]:
        async with self._conn.execute(
            "SELECT id, pattern, evidence_count, last_seen, category"
            " FROM candidates WHERE evidence_count >= ? ORDER BY evidence_count DESC",
            (min_evidence,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Candidate(id=r[0], pattern=r[1], evidence_count=r[2], last_seen=r[3], category=r[4])
            for r in rows
        ]
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "candidate_pool" -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/candidate_pool.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add Layer 3 CandidatePool with deduplication"
```

---

## Task 8: `dreaming_loop.py` — Layer 4

**Files:**
- Create: `lyra-memory/lyra_memory/dreaming_loop.py`

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch
from lyra_memory.dreaming_loop import DreamingLoop


async def test_dreaming_loop_count_trigger(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    loop = DreamingLoop(conn, wm, pool)

    mock_reflection = "I have been thinking deeply about questions and curiosity in conversation."

    with patch.object(loop, "_call_llm", new=AsyncMock(return_value=mock_reflection)):
        for i in range(10):
            wm.add_turn("user", f"interesting message number {i} about philosophy")
        # 10 items queued — call _maybe_dream directly to test trigger logic
        await loop._maybe_dream()

    episodes = await get_recent_episodes(conn, 10)
    assert len(episodes) == 1
    assert mock_reflection in episodes[0]["content"]
    assert wm.count_since_last_dream() == 0  # mark_dreamed() was called

    await conn.close()


async def test_dreaming_loop_idle_trigger(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    wm = WorkingMemory()
    pool = CandidatePool(conn)
    loop = DreamingLoop(conn, wm, pool)

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
    loop = DreamingLoop(conn, wm, pool)

    with patch.object(loop, "_call_llm", new=AsyncMock()) as mock_llm:
        await loop._maybe_dream()  # nothing in working memory
        mock_llm.assert_not_called()

    await conn.close()
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "dreaming_loop" -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/dreaming_loop.py`**

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


class DreamingLoop:
    def __init__(self, conn: aiosqlite.Connection, working_memory: WorkingMemory, candidate_pool: CandidatePool) -> None:
        self._conn = conn
        self._wm = working_memory
        self._pool = candidate_pool
        self._task: asyncio.Task | None = None
        self._idle_seconds: int = 300
        self._poll_seconds: int = DREAM_POLL_SECONDS

    def start(self, idle_seconds: int = 300, poll_seconds: int = DREAM_POLL_SECONDS) -> None:
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
        items = self._wm.get_items()
        if not items:
            return
        snapshot = json.dumps([i.model_dump() for i in items])
        prompt = (
            "You are Lyra, a continuous AI entity. Reflect on these recent experiences in your own words:\n\n"
            + "\n".join(f"[{i.role or i.type}]: {i.content}" for i in items)
        )
        content = await self._call_llm(prompt)
        ts = time.time()
        await self._conn.execute(
            "INSERT INTO episodes (content, ts, source_items_json) VALUES (?, ?, ?)",
            (content, ts, snapshot),
        )
        await self._conn.commit()
        print(f"[{datetime.now().isoformat()}] [DreamingLoop] episode written ({len(content)} chars)")
        lines = [ln.strip() for ln in content.split(".") if len(ln.strip()) > 20]
        for line in lines[:3]:
            await self._pool.add_observation(line, "behavioral")
        self._wm.mark_dreamed()

    async def _call_llm(self, prompt: str) -> str:
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

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "dreaming_loop" -v
```

Expected: `3 passed` (the idle test takes ~3.5 seconds — that is expected)

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/dreaming_loop.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add Layer 4 DreamingLoop with idle and count triggers"
```

---

## Task 9: `identity_engine.py` — Layer 5

**Files:**
- Create: `lyra-memory/lyra_memory/identity_engine.py`

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
from lyra_memory.identity_engine import IdentityEngine


async def test_identity_engine_promotes_surface_trait(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    engine = IdentityEngine(conn, pool)

    pattern = "lyra reflects deeply on experience"
    for _ in range(5):  # surface threshold = 5
        await pool.add_observation(pattern, "cognitive")

    await engine.consolidate()
    traits = await engine.get_top_traits()
    assert any(t.name == pattern for t in traits)
    promoted = next(t for t in traits if t.name == pattern)
    assert promoted.stability == "surface"
    assert 0.0 < promoted.confidence <= 1.0

    await conn.close()


async def test_identity_engine_below_threshold_not_promoted(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    engine = IdentityEngine(conn, pool)

    for _ in range(4):  # one below surface threshold of 5
        await pool.add_observation("pattern not yet ready", "behavioral")

    await engine.consolidate()
    traits = await engine.get_top_traits()
    assert not any(t.name == "pattern not yet ready" for t in traits)

    await conn.close()


async def test_identity_engine_core_trait_write_protected(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    engine = IdentityEngine(conn, pool)

    pattern = "lyra is fundamentally curious"
    # seed 50 observations to reach core threshold
    for _ in range(50):
        await pool.add_observation(pattern, "cognitive")

    await engine.consolidate()
    traits = await engine.get_top_traits()
    core = next(t for t in traits if t.name == pattern)
    assert core.stability == "core"
    assert core.confidence >= 0.8

    # try to downgrade by lowering evidence (simulate directly patching DB)
    await conn.execute("UPDATE candidates SET evidence_count = 1 WHERE lower(trim(pattern)) = ?", (pattern.lower().strip(),))
    await conn.commit()
    await engine.consolidate()

    traits_after = await engine.get_top_traits()
    locked = next(t for t in traits_after if t.name == pattern)
    # still core — write-protected
    assert locked.stability == "core"
    assert locked.confidence >= 0.8

    await conn.close()
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "identity_engine" -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/identity_engine.py`**

```python
from __future__ import annotations
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import TRAIT_THRESHOLDS, CORE_CONFIDENCE_LOCK, RETRIEVAL_TRAIT_LIMIT
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.models import Trait


class IdentityEngine:
    def __init__(self, conn: aiosqlite.Connection, candidate_pool: CandidatePool) -> None:
        self._conn = conn
        self._pool = candidate_pool

    def _stability_for(self, count: int) -> str | None:
        if count >= TRAIT_THRESHOLDS["core"]:
            return "core"
        if count >= TRAIT_THRESHOLDS["character"]:
            return "character"
        if count >= TRAIT_THRESHOLDS["surface"]:
            return "surface"
        return None

    async def consolidate(self) -> None:
        candidates = await self._pool.get_candidates(min_evidence=1)
        for c in candidates:
            stability = self._stability_for(c.evidence_count)
            if stability is None:
                continue
            confidence = min(c.evidence_count / TRAIT_THRESHOLDS["core"], 1.0)
            await self._upsert_trait(c.pattern, c.category, confidence, stability, c.evidence_count)

    async def _upsert_trait(self, name: str, value: str, confidence: float, stability: str, evidence_count: int) -> None:
        ts = time.time()
        async with self._conn.execute(
            "SELECT id, stability, confidence FROM traits WHERE name = ?", (name,)
        ) as cur:
            row = await cur.fetchone()

        if row:
            existing_stability, existing_confidence = row[1], row[2]
            if existing_stability == "core" and existing_confidence >= CORE_CONFIDENCE_LOCK:
                return  # write-protected
            await self._conn.execute(
                "UPDATE traits SET value=?, confidence=?, stability=?, evidence_count=?, updated_at=? WHERE id=?",
                (value, confidence, stability, evidence_count, ts, row[0]),
            )
            print(f"[{datetime.now().isoformat()}] [IdentityEngine] UPDATE {name!r} stability={stability!r} confidence={confidence:.2f}")
        else:
            await self._conn.execute(
                "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (name, value, confidence, stability, evidence_count, ts),
            )
            print(f"[{datetime.now().isoformat()}] [IdentityEngine] PROMOTE {name!r} stability={stability!r}")

        await self._conn.commit()

    async def get_top_traits(self, limit: int = RETRIEVAL_TRAIT_LIMIT) -> list[Trait]:
        async with self._conn.execute(
            "SELECT id, name, value, confidence, stability, evidence_count, updated_at"
            " FROM traits ORDER BY confidence DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Trait(id=r[0], name=r[1], value=r[2], confidence=r[3], stability=r[4], evidence_count=r[5], updated_at=r[6])
            for r in rows
        ]
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "identity_engine" -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/identity_engine.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add Layer 5 IdentityEngine with trait promotion and write-lock"
```

---

## Task 10: `retrieval.py` — context builder

**Files:**
- Create: `lyra-memory/lyra_memory/retrieval.py`

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
import lyra_memory.config as _cfg
from lyra_memory.retrieval import build_context, build_system_prompt


async def test_retrieval_assembles_all_layers(tmp_db_path: Path):
    _cfg.CORE_PROMPT = "You are Lyra, a continuous AI entity."
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
        await pool.add_observation("lyra reflects on questions", "cognitive")
    await engine.consolidate()

    # write an episode directly
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
    assert "Wilson" in context               # facts
    assert "lyra reflects" in context        # traits
    assert "nature of memory and identity" in context  # episodes
    assert "What is the nature of memory" in context   # working memory

    prompt = await build_system_prompt(_FakeMemory())
    assert "You are Lyra" in prompt
    assert "Wilson" in prompt

    await conn.close()


async def test_retrieval_empty_state(tmp_db_path: Path):
    _cfg.CORE_PROMPT = "You are Lyra."
    conn = await init_db(tmp_db_path)

    class _FakeMemory:
        structured_state = StructuredState(conn)
        working_memory = WorkingMemory()
        identity_engine = IdentityEngine(conn, CandidatePool(conn))
        db = conn

    prompt = await build_system_prompt(_FakeMemory())
    assert "You are Lyra." in prompt  # core prompt always present even with no context

    await conn.close()
```

- [ ] **Step 2: Run — expect failure**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "retrieval" -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `lyra-memory/lyra_memory/retrieval.py`**

```python
from __future__ import annotations
from lyra_memory import config
from lyra_memory.db import get_recent_episodes


async def build_context(memory: object, episode_limit: int | None = None) -> str:
    limit = episode_limit if episode_limit is not None else config.RETRIEVAL_EPISODE_LIMIT
    facts = await memory.structured_state.get_all_facts()
    traits = await memory.identity_engine.get_top_traits()
    episodes = await get_recent_episodes(memory.db, limit)
    working = memory.working_memory.get_items()

    parts: list[str] = []

    if facts:
        parts.append("## Facts\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()))

    if traits:
        parts.append(
            "## Persona Traits\n"
            + "\n".join(
                f"- [{t.stability}] {t.name}: {t.value} (confidence={t.confidence:.2f})"
                for t in traits
            )
        )

    if episodes:
        parts.append(
            "## Recent Reflections\n"
            + "\n\n".join(f"{e['content']}" for e in episodes)
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
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "retrieval" -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/retrieval.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add retrieval context and system prompt builder"
```

---

## Task 11: `__init__.py` — MemorySystem wiring

**Files:**
- Modify: `lyra-memory/lyra_memory/__init__.py` (replace empty placeholder)

- [ ] **Step 1: Write the failing test**

Append to `lyra-memory/tests/test_memory.py`:

```python
from lyra_memory import MemorySystem


async def test_memory_system_start_stop(tmp_db_path: Path):
    _cfg.CORE_PROMPT = "You are Lyra, a continuous AI entity."
    memory = MemorySystem(db_path=tmp_db_path)
    await memory.start(idle_seconds=999)  # don't trigger dreams during this test

    assert memory.structured_state is not None
    assert memory.working_memory is not None
    assert memory.candidate_pool is not None
    assert memory.dreaming_loop is not None
    assert memory.identity_engine is not None

    await memory.stop()


async def test_memory_system_rejects_empty_core_prompt(tmp_db_path: Path):
    _cfg.CORE_PROMPT = ""
    memory = MemorySystem(db_path=tmp_db_path)
    with pytest.raises(ValueError, match="CORE_PROMPT is not set"):
        await memory.start()
    # clean up if DB was partially created
    if memory.db:
        await memory.db.close()


async def test_memory_system_add_turn_flow(tmp_db_path: Path):
    _cfg.CORE_PROMPT = "You are Lyra."
    memory = MemorySystem(db_path=tmp_db_path)
    await memory.start(idle_seconds=999)

    memory.working_memory.add_turn("user", "Hello, Lyra.")
    memory.working_memory.add_turn("lyra", "Hello! I feel happy to hear from you.")

    items = memory.working_memory.get_items()
    assert len(items) == 2
    assert items[0].role == "user"
    assert items[1].role == "lyra"

    await memory.stop()
```

- [ ] **Step 2: Run — expect failure (MemorySystem not yet real)**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "memory_system" -v
```

Expected: tests fail — `MemorySystem` is an empty class.

- [ ] **Step 3: Replace `lyra-memory/lyra_memory/__init__.py`**

```python
from __future__ import annotations
import aiosqlite
from datetime import datetime
from pathlib import Path
from lyra_memory import config
from lyra_memory.db import init_db
from lyra_memory.structured_state import StructuredState
from lyra_memory.working_memory import WorkingMemory
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.dreaming_loop import DreamingLoop
from lyra_memory.identity_engine import IdentityEngine


class MemorySystem:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or config.DB_PATH
        self.db: aiosqlite.Connection | None = None
        self.structured_state: StructuredState | None = None
        self.working_memory: WorkingMemory | None = None
        self.candidate_pool: CandidatePool | None = None
        self.dreaming_loop: DreamingLoop | None = None
        self.identity_engine: IdentityEngine | None = None

    async def start(self, idle_seconds: int = config.DREAM_IDLE_SECONDS, poll_seconds: int = config.DREAM_POLL_SECONDS) -> None:
        if not config.CORE_PROMPT:
            raise ValueError("CORE_PROMPT is not set — define it in config.py before starting MemorySystem")
        self.db = await init_db(self._db_path)
        self.structured_state = StructuredState(self.db)
        self.working_memory = WorkingMemory()
        self.candidate_pool = CandidatePool(self.db)
        self.dreaming_loop = DreamingLoop(self.db, self.working_memory, self.candidate_pool)
        self.identity_engine = IdentityEngine(self.db, self.candidate_pool)
        self.dreaming_loop.start(idle_seconds=idle_seconds, poll_seconds=poll_seconds)
        print(f"[{datetime.now().isoformat()}] [MemorySystem] started")

    async def stop(self) -> None:
        if self.dreaming_loop:
            await self.dreaming_loop.stop()
        if self.db:
            await self.db.close()
        print(f"[{datetime.now().isoformat()}] [MemorySystem] stopped")
```

- [ ] **Step 4: Run — expect pass**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -k "memory_system" -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git -C lyra-memory add lyra_memory/__init__.py tests/test_memory.py
git -C lyra-memory commit -m "feat: add MemorySystem coordinator with startup validation"
```

---

## Task 12: Full test suite run and cleanup

**Files:**
- No new files — verify everything passes together.

- [ ] **Step 1: Run the full test suite**

```bash
cd lyra-memory && source venv/bin/activate && python -m pytest tests/test_memory.py -v
```

Expected: all tests pass. The idle-trigger dreaming loop test takes ~3.5 seconds — that is normal.

- [ ] **Step 2: Verify line counts are all under 150**

```bash
for f in lyra-memory/lyra_memory/*.py; do
  lines=$(wc -l < "$f")
  echo "$lines $f"
done
```

Expected: every file is under 150 lines.

- [ ] **Step 3: Verify the package imports cleanly**

```bash
cd lyra-memory && source venv/bin/activate && python -c "
import lyra_memory.config as cfg
cfg.CORE_PROMPT = 'You are Lyra.'
from lyra_memory import MemorySystem
from lyra_memory.retrieval import build_system_prompt
print('imports OK')
"
```

Expected: `imports OK`

- [ ] **Step 4: Final commit**

```bash
git -C lyra-memory add -A
git -C lyra-memory commit -m "feat: lyra-memory system complete — all 5 layers + retrieval"
```

---

## Post-Build Checklist

After all tasks are complete, verify:

- [ ] `pytest` passes with no failures
- [ ] All source files are under 150 lines
- [ ] `CORE_PROMPT = ""` in `config.py` — it is intentionally empty; the user will write it
- [ ] The dreaming loop test uses `idle_seconds=2, poll_seconds=1` — never a real 5-minute wait
- [ ] `lyra_ai/lyra/memory.py` (`ConversationMemory`) is **not touched** — integration is deferred
- [ ] `lyra-memory/` lives at the project root alongside `lyra-voice/`, `lyra-embodiment/`, etc.
