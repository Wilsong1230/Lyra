# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Lyra is a multi-service AI assistant with a physical presence. It is a **monorepo**: five microservices plus two core Python packages, each in its own directory with its own Python venv (their dependency trees conflict, so a shared venv is not possible).

Run `./bootstrap.sh` from the repo root to create every venv on a fresh machine.

| Service | Port | Directory | Purpose |
|---|---|---|---|
| lyra-embodiment | 8000 | `lyra-embodiment/` | Three.js avatar with 8 emotional states; exposes REST + MCP |
| lyra-voice | 8001 | `lyra-voice/` | TTS via Kokoro (default) or Coqui XTTS; plays audio + syncs avatar |
| lyra-listen | 8002 | `lyra-listen/` | STT via Whisper; push-to-talk recording endpoints |
| lyra-vision | 8003 | `lyra-vision/` | Screen/webcam capture → Gemini or OpenRouter multimodal API |
| lyra-mcp | stdio | `lyra-mcp/` | MCP server aggregating all four services for Claude Desktop |
| lyra_ai | — | `lyra_ai/` | Core CLI (`lyra`); talks to all services; installable Python package |
| lyra-memory | — | `lyra-memory/` | Turn-substrate memory: atoms, cold passes, retrieval assembly, identity |

## Commands

### First-time setup (any machine)
```bash
./bootstrap.sh                # creates every venv, seeds .env from .env.example
$EDITOR .env                  # paste in API keys
```

### lyra_ai (core CLI)
```bash
cd lyra_ai
pip install -e ".[dev]" -e ../lyra-memory   # install with dev deps
python -m pytest              # run tests
python -m pytest tests/test_assistant.py  # run a single test file
lyra                          # run the assistant (auto-selects backend)
lyra --backend anthropic      # use a specific backend
lyra --list-backends          # show which backends are available
lyra --list-models            # list models for chosen backend
```

### Services (each is the same pattern)
```bash
cd lyra-embodiment            # or lyra-voice, lyra-listen, lyra-mcp, lyra-vision
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
./start.sh                    # start the service
python -m pytest              # run tests (from inside the service dir)
```

## Architecture

### Service communication
All services communicate over HTTP. The CLI (`lyra_ai/lyra/cli.py`) hardcodes service URLs via env vars:
- `EMBODIMENT_URL` → :8000, `VOICE_URL` → :8001, `LISTEN_URL` → :8002, `VISION_URL` → :8003

Services call each other too — voice and listen both POST to `EMBODIMENT_URL/state` to sync the avatar while speaking or listening.

### Vision tool protocol
The assistant uses an in-band tool protocol (no native function calling). When the model needs to see, it outputs `[TOOL:see:screen]` or `[TOOL:see:webcam]` on its own line. `Assistant._parse_tool_call()` detects this, calls `lyra-vision`, and injects the result as a user turn. This loop runs up to 3 times per message.

### Backends (`lyra_ai/lyra/backends.py`)
`Backend` is an abstract base with `chat()` and `stream_chat()`. Concrete implementations: `AnthropicBackend`, `OpenRouterBackend`, `CerebrasBackend`, `OllamaBackend`. All use raw `http.client` — no SDK dependencies. `auto_select_backend()` tries them in priority order based on which env vars are set.

### Memory (`lyra_ai/lyra/memory.py`)
`ConversationMemory` persists turns to SQLite at `~/.lyra/history.db`. Sessions are UUID strings. History is capped at 40 turns per retrieval.

### Long-term memory (`lyra-memory/`)
See `lyra-memory/MEMORY_SPEC.md` — it is the contract, not a description, and
the code is organised to match its build order.

**Invariant: hot path appends, cold passes enrich, nothing is ever replaced.**
`atoms` is the substrate and is permanent; `session_id`, `salience`,
`outcome_id`, `retrievability` are all nullable, batch-written, and
re-derivable, so a crashed cold pass leaves the store correct.

- **Hot path** (`atoms.py`): embed, then three inserts in one transaction —
  `atoms`, `vec_atoms`, `atoms_fts`. No enrichment. Budget <50ms.
- **Cold passes**: `sessions.py` (segmentation + `gap_since_prev`),
  `entities.py`, `outcomes.py` (salience, revisable), `dreaming_loop.py`
  (dream + facts + commitments). Each independently testable against a frozen
  `atoms` table. Backed up before every run; 30-day retention.
- **Retrieval** (`retrieval.py`): pinned block order — facts, traits,
  commitments, recall, recent — with BYTE budgets, not `k`. Three paths
  (vec KNN with a similarity floor, FTS5/BM25, plain recency in a separate
  pool), merged and deduped, with her own turns down-weighted. `context_log`
  records what was injected and, equally important, the **misses**.
- **Failure policy is LOUD.** No `try/except` around ingest or `build_context`;
  the schema version is asserted at startup. Fail-open makes a broken store and
  an empty store behaviourally identical.
- **`facts` is not a KV store.** It is subject-keyed, permanent and
  correctable, with provenance back to an atom. Scratch state (affect
  snapshots) lives in `kv_state`.
- **`query_memory`** (`introspect.py`) exposes content, never mechanism — no
  thresholds, evidence counts, or distance-to-promotion. Do not add prompt
  language telling her when to use it; whether she reaches for it is the
  measurement.

### MCP server (`lyra-mcp/mcp_server.py`)
Wraps all four services as MCP tools: `set_emotion`, `get_state`, `speak`, `transcribe`, `list_voices`, `lyra_see`. Connect to Claude Desktop via stdio transport.

## Environment Variables

| Variable | Default | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | lyra_ai |
| `OPENROUTER_API_KEY` | — | lyra_ai, lyra-vision |
| `CEREBRAS_API_KEY` | — | lyra_ai |
| `GOOGLE_API_KEY` | — | lyra-vision (Gemini) |
| `WHISPER_MODEL` | `base` | lyra-listen |
| `TTS_ENGINE` | `kokoro` | lyra-voice (`kokoro` or `coqui`) |
| `KOKORO_VOICE` | `bf_emma` | lyra-voice |
| `EMBODIMENT_URL` | `http://localhost:8000` | lyra-voice, lyra-listen, lyra-mcp, lyra_ai |

The CLI loads `.env` from the working directory or `~/.env` before importing anything.

`.env` and `litellm_config.yaml` are gitignored; `.env.example` and
`litellm_config.example.yaml` are the committed templates.
