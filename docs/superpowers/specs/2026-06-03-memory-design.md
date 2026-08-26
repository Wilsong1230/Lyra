# Lyra Memory System Design

**Date:** 2026-06-03  
**Status:** Approved  
**Module:** `lyra-memory/`

---

## Overview

Lyra is a continuous AI entity, not a session-based assistant. The memory system reflects this: there are no sessions, only a single persistent timeline of experience. Conversation turns, observations, and reflections all flow through the same five-layer pipeline — accumulating, consolidating, and shaping Lyra's identity over time.

This module replaces `lyra_ai/lyra/memory.py` (`ConversationMemory`) entirely. It is structured as a top-level `lyra-memory/` directory with its own venv, matching the microservice pattern of the rest of the project, so it can be hot-reloaded independently while the main loop is running.

---

## Package Structure

```
lyra-memory/
├── requirements.txt          # aiosqlite, pydantic
├── start.sh                  # python -m lyra_memory (isolated dev/test)
├── lyra_memory/
│   ├── __init__.py           # MemorySystem — wires all layers, owns lifecycle
│   ├── config.py             # constants, thresholds, model strings, core prompt,
│   │                         # EMOTION_KEYWORDS set, RETRIEVAL_EPISODE_LIMIT
│   ├── models.py             # all Pydantic schemas
│   ├── db.py                 # aiosqlite setup, creates all tables on init
│   ├── structured_state.py   # Layer 1: mutable + stable facts
│   ├── working_memory.py     # Layer 2: in-memory rolling deque, scored observations
│   ├── candidate_pool.py     # Layer 3: behavioral pattern log
│   ├── dreaming_loop.py      # Layer 4: async LLM reflection background task
│   ├── identity_engine.py    # Layer 5: trait consolidation
│   └── retrieval.py          # context builder — assembles full LLM system prompt
└── tests/
    └── test_memory.py
```

---

## Data Flow

```
add_turn(role, content)
        ↓
  WorkingMemory (deque, scored, in-memory only)
        ↓ [10 items since last dream OR 5 min idle with >0 items]
  DreamingLoop ──→ LLM (meta-llama/llama-3.1-8b-instruct:free via OpenRouter)
               ├──→ Episode stored in SQLite
               └──→ CandidatePool (behavioral pattern observations)
                          ↓ [weekly or manual trigger]
                   IdentityEngine → persona traits (SQLite)

retrieval.build_context():
  StructuredState.get_all_facts()    ← always injected in full, always current
  + IdentityEngine top traits        ← who Lyra is
  + last N episodes                  ← RETRIEVAL_EPISODE_LIMIT = 10 (configurable)
  + WorkingMemory current items      ← what's happening right now
        ↓
  build_system_prompt() → str        ← immutable core prompt + assembled context
```

**Important:** Only the last `RETRIEVAL_EPISODE_LIMIT` episodes are injected into context. All episodes are preserved permanently in SQLite — older ones influence identity via the identity engine (compressed into traits) rather than being replayed directly.

---

## Layer 1 — StructuredState (`structured_state.py`)

SQLite-backed key-value store for facts about Lyra and her environment. Holds both stable facts (Lyra's name, default voice, core configuration) and mutable facts (current project, user preferences, last active timestamp, emotional baseline). The caller decides what is stable — the API makes no distinction.

**Schema:**
```sql
facts (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)
```
Values are JSON-serialized to support any Pydantic-serializable type.

**API:** `set_fact(key, value)`, `get_fact(key) → Any`, `get_all_facts() → dict`

Every write logs to console with timestamp.

---

## Layer 2 — WorkingMemory (`working_memory.py`)

In-memory `deque(maxlen=20)`. Intentionally not persisted — dies on crash by design. Each item is a `WorkingMemoryItem`. Both user turns AND Lyra's own responses are captured as `type="conversation"`.

**WorkingMemoryItem schema:**
```python
class WorkingMemoryItem(BaseModel):
    type: Literal["conversation", "observation", "reflection"]
    role: Literal["user", "lyra"] | None  # None for non-conversation types
    content: str
    score: float          # 0.0–1.0, computed on write
    ts: float
```

**Scoring:**
```python
# Importance — type weight
TYPE_WEIGHTS = {"conversation": 1.0, "observation": 0.6, "reflection": 0.3}
importance = TYPE_WEIGHTS[item.type]

# Surprise — three layers
punct    = 0.3 if ("?" in content or "!" in content) else 0.0
known    = {w for item in deque for w in item.content.lower().split()}
new_words = set(content.lower().split())
novelty  = min((len(new_words - known) / max(len(new_words), 1)) * 1.5, 0.5)
surprise = min(punct + novelty, 1.0)

# Emotion — extensible keyword set defined in config.py
emotion  = 1.0 if any(w in content.lower() for w in EMOTION_KEYWORDS) else 0.0

score    = (importance + surprise + emotion) / 3
```

**`EMOTION_KEYWORDS`** lives in `config.py` as a plain `set[str]` — add words freely without touching any other file.

**API:** `add_turn(role, content)`, `add_observation(content)`, `add_reflection(content)`, `get_items() → list[WorkingMemoryItem]`, `count_since_last_dream() → int`, `mark_dreamed()`

---

## Layer 3 — CandidatePool (`candidate_pool.py`)

Append-only but deduplicating behavioral pattern log. When a new observation matches an existing pattern (exact match on lowercased, whitespace-stripped text), `evidence_count` is incremented rather than inserting a duplicate.

**Schema:**
```sql
candidates (
    id             INTEGER PRIMARY KEY,
    pattern        TEXT,
    evidence_count INTEGER DEFAULT 1,
    last_seen      REAL,
    category       TEXT    -- "behavioral"|"emotional"|"relational"|"cognitive"
)
```

**API:** `add_observation(pattern, category)`, `get_candidates(min_evidence=1) → list[Candidate]`

Every write logs to console with timestamp.

---

## Layer 4 — DreamingLoop (`dreaming_loop.py`)

Async background task (`asyncio.Task`). Polls every 30 seconds. Triggers a dream when:
- `items_since_last_dream >= 10`, OR
- `time_since_last_turn >= 300s` AND `items_since_last_dream > 0`

On trigger: snapshots current working memory, calls OpenRouter with the snapshot as input, prompts Lyra to reflect in her own words. LLM response is stored as an episode. Behavioral observations extracted from the reflection are added to CandidatePool. `WorkingMemory.mark_dreamed()` resets the counter.

If stopped mid-dream, working memory is not cleared. Next start resumes from current state.

**Schema (episodes):**
```sql
episodes (
    id                 INTEGER PRIMARY KEY,
    content            TEXT,
    ts                 REAL,
    source_items_json  TEXT   -- JSON snapshot of WorkingMemoryItems that triggered this
)
```

**API:** `start(idle_seconds: int = DREAM_IDLE_SECONDS)`, `stop()` — independently startable/stoppable without affecting other layers. `idle_seconds` is overridable at start time so tests can pass a small value (e.g. `start(idle_seconds=2)`) without waiting the full 5 minutes. Tests must use this override — never mock the timer.

Every episode write logs to console with timestamp.

**Model:** `meta-llama/llama-3.1-8b-instruct:free` via OpenRouter (defined in `config.py` as `DREAM_MODEL`).

---

## Layer 5 — IdentityEngine (`identity_engine.py`)

Reads CandidatePool and promotes patterns to traits based on evidence thresholds. Trait stability tiers resist regression once established.

**Thresholds (in `config.py`):**
```python
TRAIT_THRESHOLDS = {"surface": 5, "character": 15, "core": 50}
CORE_CONFIDENCE_LOCK = 0.8   # core traits above this confidence are write-protected
```

**Schema (traits):**
```sql
traits (
    id             INTEGER PRIMARY KEY,
    name           TEXT,
    value          TEXT,
    confidence     REAL,   -- 0.0–1.0
    stability      TEXT,   -- "surface" | "character" | "core"
    evidence_count INTEGER,
    updated_at     REAL
)
```

**API:** `consolidate()`, `get_top_traits(limit: int = RETRIEVAL_TRAIT_LIMIT) → list[Trait]` — `consolidate()` reads all candidates, upserts traits, applies write-protection for locked core traits. Manually triggerable for testing, intended to run weekly in production. `get_top_traits()` returns traits sorted by confidence descending, used by retrieval.

Every trait promotion logs to console with timestamp.

---

## retrieval.py

Assembles full LLM context from all layers. Called by `lyra_ai/lyra/assistant.py` (not yet wired — this is the intended future seam).

```python
async def build_context(
    memory: MemorySystem,
    episode_limit: int = RETRIEVAL_EPISODE_LIMIT,   # default 10
) -> str:
    facts    = await memory.structured_state.get_all_facts()
    traits   = await memory.identity_engine.get_top_traits()
    episodes = await memory.db.get_recent_episodes(limit=episode_limit)
    working  = memory.working_memory.get_items()
    # assembles into a structured string: facts → traits → episodes → working memory

async def build_system_prompt(memory: MemorySystem) -> str:
    # CORE_PROMPT (immutable, from config.py) + build_context()
```

`RETRIEVAL_EPISODE_LIMIT = 10` is set in `config.py`. Older episodes are not injected into context — they inform identity via traits only.

---

## MemorySystem (`__init__.py`)

Owns all layer instances and lifecycle.

```python
class MemorySystem:
    structured_state: StructuredState
    working_memory: WorkingMemory
    candidate_pool: CandidatePool
    dreaming_loop: DreamingLoop
    identity_engine: IdentityEngine

    async def start() -> None   # init DB, validate CORE_PROMPT, start dreaming loop
    async def stop()  -> None   # stop dreaming loop, close DB
```

**Startup check:** `start()` validates that `CORE_PROMPT` is non-empty before proceeding. If empty, raises `ValueError("CORE_PROMPT is not set — define it in config.py before starting MemorySystem")`. This is a hard stop, not a warning, because an empty core prompt means Lyra has no identity anchor and every LLM call will produce undefined behavior.

---

## models.py

All Pydantic schemas in one place:
- `WorkingMemoryItem`
- `Episode`
- `Candidate`
- `Trait`
- `Fact`

---

## db.py

`aiosqlite` setup. `init_db(path) -> aiosqlite.Connection` creates all four tables on first run. Single shared connection passed to all layers via `MemorySystem`.

DB path: `~/.lyra/memory.db` (separate from existing `~/.lyra/history.db` until migration).

---

## config.py

```python
DB_PATH = Path.home() / ".lyra" / "memory.db"
DREAM_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DREAM_TRIGGER_ITEMS = 10
DREAM_IDLE_SECONDS = 300
DREAM_POLL_SECONDS = 30
RETRIEVAL_EPISODE_LIMIT = 10
TRAIT_THRESHOLDS = {"surface": 5, "character": 15, "core": 50}
CORE_CONFIDENCE_LOCK = 0.8
RETRIEVAL_TRAIT_LIMIT = 10   # top N traits injected into context, sorted by confidence
TYPE_WEIGHTS = {"conversation": 1.0, "observation": 0.6, "reflection": 0.3}
EMOTION_KEYWORDS: set[str] = {
    "feel", "feeling", "felt",
    "love", "hate", "fear", "afraid", "angry", "sad", "happy",
    "excited", "lonely", "anxious", "proud", "hurt", "joy",
    "wonder", "curious", "confused", "grateful", "frustrated",
}
CORE_PROMPT = ""  # immutable identity anchor — must be written before first use
```

---

## test_memory.py

Exercises all five layers in one sequential run:
1. Init DB and `MemorySystem`
2. Set and retrieve facts (Layer 1)
3. Add conversation turns (both roles) and observations, verify scoring (Layer 2)
4. Add candidates, verify deduplication (Layer 3)
5. Start dreaming loop, add 10 items, wait for dream trigger, verify episode written (Layer 4)
6. Run `consolidate()`, verify trait promotion (Layer 5)
7. Call `build_system_prompt()`, verify all layers represented in output (retrieval)
8. Stop `MemorySystem`

---

## Integration Seam (not wired yet)

When the CLI is ready to connect:
- `lyra_ai/lyra/assistant.py` calls `memory.add_turn(role, content)` for every turn (both user and Lyra)
- `build_system_prompt(memory)` replaces the current static system prompt + `get_history()` call
- `lyra_ai/lyra/memory.py` (`ConversationMemory`) is deleted

`lyra-memory` will be added to `lyra_ai/pyproject.toml` as a path dependency at that point.

---

## Constraints

- All DB operations async via `aiosqlite`
- Every write logs to console with timestamp
- No FastAPI — pure internal module
- Each source file under 150 lines
- Dreaming loop independently startable/stoppable

---

## Future Work

**Embedding-based similarity for CandidatePool** — the current deduplication strategy uses exact match on normalized text, which will miss semantically identical patterns phrased differently (e.g. "user asks technical questions" vs "user tends to ask about technical topics"). Replace with vector embedding similarity (cosine distance threshold ~0.85) once an embedding model is available in the stack. Candidate: `sentence-transformers/all-MiniLM-L6-v2` locally or OpenRouter's embedding endpoint. When implemented, the `candidates` table will need an `embedding BLOB` column and a similarity search helper in `db.py`.
