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
| lyra-memory | — | `lyra-memory/` | Four-layer memory: working, episodic, structured state, identity |

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
python -m lyra_core --init-store          # first run only: create an empty memory store, then serve
python -m lyra_core                       # the daemon: one core, one store, listens on 127.0.0.1:8010
python -m lyra_core --backend anthropic   # use a specific backend
python -m lyra_core --list-backends       # show which backends are available
lyra                          # attach to the running daemon (exits nonzero if none is listening)
lyra --session <id>           # resume a session
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
