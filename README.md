# black-box

Core AI services for Lyra.

## Services

| Directory | Purpose |
|---|---|
| `lyra_ai/` | Core CLI assistant — talks to all Lyra services |
| `lyra-memory/` | Four-layer memory system (working, episodic, structured state, identity) |

## Setup

```bash
# 1. Install lyra-memory into its own venv
cd lyra-memory
python3 -m venv venv && source venv/bin/activate
pip install -e .
deactivate

# 2. Install lyra_ai — also install lyra-memory into this venv
#    (lyra_ai imports lyra_memory; both must be on the same Python path)
cd ../lyra_ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../lyra-memory   # cross-service dependency
pip install -e ".[dev]"

# 3. Run
lyra
```

## Environment Variables

| Variable | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | lyra_ai |
| `OPENROUTER_API_KEY` | lyra_ai, lyra-memory (dreaming loop) |
| `CEREBRAS_API_KEY` | lyra_ai |
| `EMBODIMENT_URL` | lyra_ai (default: http://localhost:8000) |
| `VOICE_URL` | lyra_ai (default: http://localhost:8001) |
| `LISTEN_URL` | lyra_ai (default: http://localhost:8002) |
| `VISION_URL` | lyra_ai (default: http://localhost:8003) |
```
