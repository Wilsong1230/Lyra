# Lyra Memory System — Specification

## Overview

Lyra's memory system is a four-layer stack that turns live conversation into persistent identity. Each layer operates on a different timescale and has a different purpose. The layers are: **working memory** (hot, in-process), **episodic memory** (narrative, SQLite), **structured state** (facts, SQLite), and **identity / traits** (plastic personality, SQLite).

All persistence is in a single SQLite file at `~/.lyra/memory.db`. The entire system is async-first (aiosqlite throughout). Entry point is `MemorySystem` in `__init__.py`.

Semantic search is powered by **sqlite-vec** (vec0 virtual tables) and **sentence-transformers** (`all-MiniLM-L6-v2`, 384-dim). Two vec tables — `vec_episodes` and `vec_candidates` — are linked by rowid to their regular-table counterparts and enable embedding-based KNN queries.

---

## Architecture Diagram

```
   ┌──────────────────────────────────────────────────────────┐
   │                      LIVE SESSION                        │
   │                                                          │
   │  conversations / observations / reflections              │
   │                    │                                     │
   │                    ▼                                     │
   │           ┌─────────────────┐                            │
   │           │  WorkingMemory  │  deque[20], scored items   │
   │           └────────┬────────┘                            │
   │                    │ (10+ items OR 5min idle)            │
   │                    ▼                                     │
   │           ┌─────────────────┐                            │
   │           │  DreamingLoop   │  LLM reflection → SQLite   │
   │           └────┬────────────┘                            │
   │                │                                         │
   │    ┌───────────┼────────────────────┐                    │
   │    ▼           ▼                    ▼                    │
   │ episodes   candidates           (external writes)        │
   │ (SQLite)   (SQLite)             to facts (SQLite)        │
   │    │           │                                         │
   │    ▼           ▼                                         │
   │ vec_episodes  vec_candidates                             │
   │ (sqlite-vec)  (sqlite-vec)                               │
   │                │                                         │
   │                ▼                                         │
   │        IdentityEngine.consolidate()                      │
   │                │                                         │
   │                ▼                                         │
   │            traits (SQLite)                               │
   └──────────────────────────────────────────────────────────┘

   At inference:
   build_system_prompt() = CORE_PROMPT + top traits + working memory
   Episodes / facts accessed on demand via search_episodes() or get_fact().
```

---

## Layer 1 — Working Memory

**File:** `working_memory.py`  
**Class:** `WorkingMemory`  
**Persistence:** In-process only (lost on restart)

### Purpose
Hot buffer for the current session. Holds the last 20 items across conversations, observations, and reflections. Feeds the dreaming loop and the system prompt.

### Item Types

| Type | Source | Importance Weight |
|------|--------|-------------------|
| `conversation` | `add_turn(role, content)` | 1.0 |
| `observation` | `add_observation(content)` | 0.6 |
| `reflection` | `add_reflection(content)` | 0.3 |

### Scoring Formula

Each item is scored on insertion:

```
score = (importance + surprise + emotion) / 3
```

- **importance** — base weight from item type (see table above)
- **surprise** — `min(punct + novelty, 1.0)` where `punct = 0.3` if `?` or `!` present, and `novelty = min(new_words_not_seen / total_words * 1.5, 0.5)`
- **emotion** — `1.0` if any word in `EMOTION_KEYWORDS` is present, else `0.0`

### Dream Trigger Counters

- `_items_since_dream` — incremented on every item added; reset by `mark_dreamed()`
- `_last_turn_ts` — Unix timestamp of the most recent `add_turn()` call

### Key Methods

| Method | Description |
|--------|-------------|
| `add_turn(role, content)` | Add a conversation turn. `role` is `"user"` or `"lyra"` |
| `add_observation(content)` | Add a non-conversational observation |
| `add_reflection(content)` | Add a self-generated reflection |
| `get_items()` | Return all items in the deque as a list |
| `count_since_last_dream()` | How many items have accumulated since the last dream |
| `last_turn_ts()` | Timestamp of the last conversation turn |
| `mark_dreamed()` | Reset the `_items_since_dream` counter after a dream fires |

---

## Layer 2 — Episodic Memory

**File:** `dreaming_loop.py`, `db.py`  
**Class:** `DreamingLoop`  
**Tables:** `episodes`, `vec_episodes`  
**Persistence:** SQLite, permanent

### Purpose
Converts the raw deque of working memory items into a durable narrative reflection. Each "dream" is an LLM-generated first-person narrative stored as an episode. Episodes are the long-term experiential record — Lyra's autobiography.

### Dream Trigger Conditions (checked every `DREAM_POLL_SECONDS = 30s`)

| Condition | Default Threshold |
|-----------|-------------------|
| Accumulated items since last dream | ≥ 10 (`DREAM_TRIGGER_ITEMS`) |
| Idle time since last conversation turn | ≥ 300s (`DREAM_IDLE_SECONDS`) |

Either condition alone fires a dream.

### Dream Pipeline

1. Snapshot all current working memory items as JSON
2. Build a first-person reflection prompt from the items
3. Call `meta-llama/llama-3.1-8b-instruct:free` via OpenRouter (`DREAM_MODEL`)
4. Write the LLM response and its embedding into `episodes` + `vec_episodes` in a single transaction
5. Run a second LLM call to extract structured behavioral observations; push up to 3 into `CandidatePool`
6. Call `working_memory.mark_dreamed()` to reset the counter

### Atomicity Guarantee

The episode row and its `vec_episodes` row are committed together. If embedding fails, neither row is written — there are no episodes without a corresponding vector.

### SQLite Schema

```sql
CREATE TABLE episodes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content           TEXT NOT NULL,          -- LLM narrative text
    ts                REAL NOT NULL,          -- Unix timestamp of dream
    source_items_json TEXT NOT NULL           -- JSON snapshot of working memory items
);

CREATE VIRTUAL TABLE vec_episodes USING vec0(
    embedding FLOAT[384]   -- unit-normalized all-MiniLM-L6-v2 vector; rowid == episodes.id
);
```

### LLM Call

- Transport: raw `http.client.HTTPSConnection` (no SDK), run in executor to avoid blocking the event loop
- Model: `meta-llama/llama-3.1-8b-instruct:free` (configurable via `DREAM_MODEL`)
- Endpoint: `OPENROUTER_BASE` + `/api/v1/chat/completions`
- Requires: `OPENROUTER_API_KEY` env var

---

## Layer 3 — Structured State (Facts)

**File:** `structured_state.py`  
**Class:** `StructuredState`  
**Table:** `facts`  
**Persistence:** SQLite, permanent

### Purpose
Explicit, precise key-value store for hard facts: API configs, user preferences, active project state, system settings. Values are JSON-serialized so any Python type can be stored.

### SQLite Schema

```sql
CREATE TABLE facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,     -- JSON-serialized value
    updated_at REAL NOT NULL      -- Unix timestamp of last write
);
```

### Key Methods

| Method | Description |
|--------|-------------|
| `set_fact(key, value)` | Upsert a fact. `value` is JSON-serialized. Updates `updated_at` |
| `get_fact(key)` | Retrieve one fact by exact key. Returns deserialized Python value or `None` |
| `get_all_facts()` | Return all facts as a `dict[str, Any]` |

### MCP Access
Facts are queried on demand via the `get_fact` MCP tool. Pass the exact key string.

---

## Layer 4 — Identity Engine (Traits)

**Files:** `candidate_pool.py`, `identity_engine.py`  
**Classes:** `CandidatePool`, `IdentityEngine`  
**Tables:** `candidates`, `vec_candidates`, `traits`  
**Persistence:** SQLite, permanent

### Purpose
Builds a persistent, plastic personality from accumulated behavioral evidence. Candidates are raw observations; traits are promoted candidates with enough evidence to be considered stable personality characteristics.

### Two-Stage Promotion Pipeline

```
observations (from dream structured output)
        │
        ▼
   candidates table + vec_candidates
   (trait_name + trait_value + evidence_count)
        │  [IdentityEngine.consolidate()]
        ▼
   traits table
   (name + value + confidence + stability)
```

### CandidatePool

Tracks how many times a behavioral pattern has been observed. Deduplication is **semantic**: each new `trait_name` is embedded and compared against existing candidates via KNN. If the nearest neighbor's cosine distance is below `CANDIDATE_DEDUP_THRESHOLD` (default `0.3`), the existing candidate's `evidence_count` is incremented instead of inserting a new row.

**Dedup math:** Vectors are unit-normalized (from `all-MiniLM-L6-v2` with `normalize_embeddings=True`). sqlite-vec returns L2 distance. The config threshold is in cosine distance; it is converted at startup: `L2_threshold = sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)`.

**Schema:**
```sql
CREATE TABLE candidates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trait_name     TEXT NOT NULL,
    trait_value    TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    last_seen      REAL NOT NULL,
    category       TEXT NOT NULL   -- 'behavioral' | 'emotional' | 'relational' | 'cognitive'
);

CREATE VIRTUAL TABLE vec_candidates USING vec0(
    embedding FLOAT[384]   -- embedding of trait_name; rowid == candidates.id
);
```

### IdentityEngine — Promotion Thresholds

| Stability Level | Min Evidence | Notes |
|----------------|-------------|-------|
| `surface` | 5 | Newly noticed pattern |
| `character` | 15 | Recurring, consistent behavior |
| `core` | 50 | Deeply ingrained, write-protected |

**Confidence formula:** `min(evidence_count / 50, 1.0)` — scales linearly to the core threshold.

**Write protection:** Once a trait reaches `core` stability and `confidence ≥ 0.8` (`CORE_CONFIDENCE_LOCK`), it is permanently locked and cannot be overwritten by consolidation.

**Schema:**
```sql
CREATE TABLE traits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    value          TEXT NOT NULL,
    confidence     REAL NOT NULL,   -- 0.0–1.0
    stability      TEXT NOT NULL,   -- 'surface' | 'character' | 'core'
    evidence_count INTEGER NOT NULL,
    updated_at     REAL NOT NULL
);
```

### consolidate()

Called explicitly (not on a timer) to promote any candidate that has crossed a stability threshold into the traits table. Existing traits are updated unless write-protected.

---

## Semantic Search

**File:** `embeddings.py`, `db.py`, `retrieval.py`, `candidate_pool.py`

### Embedding Model

`all-MiniLM-L6-v2` via sentence-transformers. 384-dim float32, unit-normalized. Lazy-loaded on first call to `embed()` and cached for the process lifetime.

```python
from lyra_memory.embeddings import embed

vec_bytes: bytes = await embed("some text")  # 384 * 4 = 1536 bytes, packed float32
```

`embed()` is async and runs the model in a thread-pool executor so it never blocks the event loop.

### sqlite-vec Extension

Loaded per connection at `init_db()` time via `load_vec_extension(conn)` in `db.py`. Also called inside `search_episodes()` which opens its own connection. Requires a Python build that supports `sqlite3.enable_load_extension` — macOS system Python does not; use Homebrew or python.org Python instead.

```python
from lyra_memory.db import load_vec_extension
await load_vec_extension(conn)  # must be called before any vec0 queries
```

### KNN Query Syntax (sqlite-vec)

```sql
SELECT rowid, distance
FROM vec_episodes
WHERE embedding MATCH ?     -- ? is packed float32 bytes from embed()
  AND k = ?                 -- number of nearest neighbors (not SQL LIMIT)
ORDER BY distance
```

### Episode Search

`search_episodes(query, limit, path)` embeds the query text and returns the `limit` nearest episodes by cosine distance:

```python
results = await search_episodes("curious about mathematics", limit=3)
# returns [{"id": ..., "content": ..., "ts": ...}, ...]
```

No word overlap with stored episodes is required — semantic paraphrases work.

---

## Retrieval at Inference Time

**File:** `retrieval.py`

### What is auto-injected into the system prompt

```
CORE_PROMPT          (immutable, from config.py)
  +
Persona Traits       (top N by confidence, from identity_engine.get_top_traits())
  +
Current Experience   (current working memory items)
```

Historical episodes and facts are **not** injected automatically. Lyra fetches them on demand via `search_episodes` or `get_fact` when she needs to recall something specific.

### build_context(memory)

Returns the dynamic portion of the system prompt (traits + working memory). Takes the live `MemorySystem` object.

### build_system_prompt(memory)

Returns `CORE_PROMPT + "\n\n" + build_context()`. This is the full string passed to the LLM.

### On-Demand Retrieval Functions

| Function | When to use |
|----------|-------------|
| `search_episodes(query, limit=5, path=None)` | Recall past experiences by meaning. Returns `[{id, content, ts}]`. Pass `path` to override DB path (useful in tests). |
| `get_fact(subject_key)` | Look up a precise stored value. Returns `{key, value, updated_at}` or `None` |

---

## MemorySystem — Startup & Lifecycle

**File:** `__init__.py`  
**Class:** `MemorySystem`

```python
mem = MemorySystem()          # optionally pass db_path=Path(...)
await mem.start()             # initializes DB (loads sqlite-vec), starts dreaming loop
await mem.add_turn("user", "Hello")
await mem.add_turn("lyra", "Hi there")
prompt = await build_system_prompt(mem)
await mem.stop()              # stops dreaming loop, closes DB
```

`CORE_PROMPT` must be set in `config.py` before calling `start()` — it raises `ValueError` if empty.

---

## Configuration Reference (`config.py`)

| Key | Default | Description |
|-----|---------|-------------|
| `DB_PATH` | `~/.lyra/memory.db` | SQLite database location |
| `DREAM_MODEL` | `meta-llama/llama-3.1-8b-instruct:free` | OpenRouter model for dreaming |
| `OPENROUTER_BASE` | `https://openrouter.ai/api/v1` | OpenRouter base URL |
| `DREAM_TRIGGER_ITEMS` | `10` | Items since last dream to trigger dreaming |
| `DREAM_IDLE_SECONDS` | `300` | Idle seconds since last turn to trigger dreaming |
| `DREAM_POLL_SECONDS` | `30` | How often DreamingLoop checks trigger conditions |
| `RETRIEVAL_EPISODE_LIMIT` | `10` | Available for manual use |
| `RETRIEVAL_TRAIT_LIMIT` | `10` | Max traits injected into system prompt |
| `SEARCH_EPISODES_DEFAULT_LIMIT` | `5` | Default K for `search_episodes` KNN query |
| `TRAIT_THRESHOLDS` | `{surface:5, character:15, core:50}` | Promotion thresholds |
| `CORE_CONFIDENCE_LOCK` | `0.8` | Confidence at which core traits become write-protected |
| `TYPE_WEIGHTS` | `{conversation:1.0, observation:0.6, reflection:0.3}` | Base importance by item type |
| `EMOTION_KEYWORDS` | _(set of ~25 words)_ | Words that trigger the emotion score boost |
| `EMBED_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model for embeddings |
| `EMBED_DIM` | `384` | Embedding dimension; must match chosen model |
| `CANDIDATE_DEDUP_THRESHOLD` | `0.3` | Cosine distance below which two trait names are considered the same observation |
| `CORE_PROMPT` | `""` | Must be set before `MemorySystem.start()` |

---

## Data Models (`models.py`)

| Model | Fields |
|-------|--------|
| `WorkingMemoryItem` | `type`, `role`, `content`, `score`, `ts` |
| `Episode` | `id`, `content`, `ts`, `source_items_json` |
| `Candidate` | `id`, `trait_name`, `trait_value`, `evidence_count`, `last_seen`, `category` |
| `Trait` | `id`, `name`, `value`, `confidence`, `stability`, `evidence_count`, `updated_at` |
| `Fact` | `key`, `value`, `updated_at` |

All models are Pydantic `BaseModel` subclasses.

---

## File Map

```
lyra-memory/
├── lyra_memory/
│   ├── __init__.py          # MemorySystem — entry point, lifecycle
│   ├── config.py            # All tuneable constants + CORE_PROMPT
│   ├── db.py                # aiosqlite init + sqlite-vec loader + get_recent_episodes
│   ├── embeddings.py        # Lazy all-MiniLM-L6-v2 model + async embed()
│   ├── models.py            # Pydantic data models
│   ├── working_memory.py    # WorkingMemory — hot deque, scoring
│   ├── dreaming_loop.py     # DreamingLoop — async LLM reflection loop
│   ├── candidate_pool.py    # CandidatePool — semantic KNN dedup + vec_candidates
│   ├── identity_engine.py   # IdentityEngine — trait promotion + write-lock
│   ├── structured_state.py  # StructuredState — key/value facts store
│   └── retrieval.py         # build_system_prompt + semantic search_episodes + get_fact
├── docs/
│   └── superpowers/
│       └── plans/
│           └── 2026-06-04-semantic-search.md   # implementation plan
└── MEMORY_SPEC.md           # this document
```

## Dependencies

```toml
dependencies = [
    "aiosqlite>=0.19",
    "pydantic>=2.0",
    "sentence-transformers>=3.0",   # embedding model
    "sqlite-vec>=0.1.0",            # vec0 virtual tables for KNN search
]
```

**Runtime requirement:** Python must be built with `sqlite3` extension loading enabled. macOS system Python does not support this — use Homebrew Python (`brew install python`) or python.org instead.
