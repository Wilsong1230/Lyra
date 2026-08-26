# Lyra

A multi-service AI assistant with a physical presence — an animated avatar that
speaks, listens, sees, and remembers.

Everything lives in this one repository. Clone it, run `./bootstrap.sh`, fill in
`.env`, and Lyra runs.

## Quick start

```bash
git clone https://github.com/Wilsong1230/Lyra.git
cd Lyra
./bootstrap.sh
$EDITOR .env          # add your API keys
./lyra-embodiment/start.sh &
./lyra-voice/start.sh &
lyra_ai/venv/bin/lyra
```

## Layout

| Path | Port | Purpose |
|---|---|---|
| `lyra_ai/` | — | Core CLI (`lyra`); orchestrates every service |
| `lyra-memory/` | — | Four-layer memory: working, episodic, structured state, identity |
| `lyra-embodiment/` | 8000 | Three.js avatar, 8 emotional states; REST + MCP |
| `lyra-voice/` | 8001 | TTS via Kokoro (default) or Coqui XTTS |
| `lyra-listen/` | 8002 | STT via Whisper; push-to-talk |
| `lyra-vision/` | 8003 | Screen/webcam capture → Gemini or OpenRouter |
| `lyra-mcp/` | stdio | MCP server aggregating the services for Claude Desktop |
| `docs/` | — | Specs, design notes, and `PICKUP.md` session handoffs |

Each service keeps its own venv under `<service>/venv/` — they have conflicting
dependency trees, so a single shared venv is not an option. `bootstrap.sh`
creates all of them.

## Development

```bash
lyra_ai/venv/bin/python -m pytest lyra_ai/tests      # core CLI tests
lyra-voice/venv/bin/python -m pytest lyra-voice      # per-service tests
```

## Secrets

`.env` and `litellm_config.yaml` are gitignored. Copy them from `.env.example`
and `litellm_config.example.yaml` on each new machine and paste in your keys —
they are the only things this repo does not carry for you.
