# Lyra Voice + Unified MCP Gateway — Design Spec

**Date:** 2026-06-02
**Scope:** `lyra-voice` service (Whisper STT + Kokoro TTS) and `lyra-mcp` unified MCP gateway. Does not include lyra_ai tool-calling integration (separate future spec).

---

## Background

`lyra-embodiment` is a FastAPI service (port 8000) that drives a visual atom avatar through seven emotional states. It currently ships its own `mcp_server.py`. As Lyra grows with new modules (voice, memory, sensors), each module having its own MCP server becomes unmanageable. This spec introduces a unified MCP gateway and the first new module: voice.

---

## Repository Structure

```
Lyra/
  lyra-embodiment/        # FastAPI :8000 — emotion/avatar (internals unchanged)
    server.py
    static/index.html
    requirements.txt
    start.sh
    # mcp_server.py DELETED — tools migrate to lyra-mcp

  lyra-voice/             # FastAPI :8001 — STT/TTS (new)
    server.py
    cli.py
    requirements.txt
    start.sh

  lyra-mcp/               # Unified MCP gateway (new)
    mcp_server.py
    requirements.txt
    start.sh
```

---

## lyra-voice Service

### Responsibilities
- Run Kokoro TTS (`bf_emma` voice by default) to synthesize speech from text
- Run Whisper STT (`whisper-base` by default) to transcribe audio files
- Play synthesized audio locally via `sounddevice`
- Return WAV bytes to callers for remote playback
- Optionally sync Lyra's emotion state to `speaking`/`idle` via lyra-embodiment

### HTTP Endpoints

**`POST /speak`**
```json
{ "text": "string", "sync_emotion": true }
```
- Synthesizes speech with Kokoro using `bf_emma`
- If `sync_emotion=true`: calls `lyra-embodiment :8000/state` → `speaking` before playback, `idle` after
- Plays audio locally via `sounddevice` in a background thread
- Returns WAV bytes (`Content-Type: audio/wav`)
- Emotion sync failure is non-fatal — logs a warning and continues

**`POST /transcribe`**
```json
multipart/form-data: { "file": <audio file> }
```
- Runs Whisper on the uploaded audio file
- Returns `{ "text": "string" }`

**`GET /health`**
```json
{ "status": "ok", "tts_voice": "bf_emma", "stt_model": "whisper-base" }
```

**`GET /voices`**
- Returns list of available Kokoro voices

### Configuration (env vars)
| Variable | Default | Description |
|---|---|---|
| `KOKORO_VOICE` | `bf_emma` | Kokoro voice ID |
| `WHISPER_MODEL` | `whisper-base` | Whisper model size |
| `EMBODIMENT_URL` | `http://localhost:8000` | lyra-embodiment base URL |

### CLI (`cli.py`)

Manual control via `typer`:
```bash
python cli.py speak "Hello, this is Lyra."
python cli.py speak "Hello." --no-sync-emotion
python cli.py transcribe path/to/audio.wav
python cli.py voices
python cli.py health
```

### Dependencies
```
fastapi
uvicorn
kokoro
whisper (openai-whisper)
sounddevice
numpy
typer
httpx
```

---

## lyra-mcp Unified Gateway

### Responsibilities
- Single MCP server exposing all Lyra tools across all services
- No business logic — thin HTTP wrappers only
- Replaces `lyra-embodiment/mcp_server.py`

### Tools

**Emotion (from lyra-embodiment)**
- `set_emotion(state: str) → str` — sets avatar emotional state
- `get_state() → str` — returns current state and color

**Voice (from lyra-voice)**
- `speak(text: str, sync_emotion: bool = True) → str` — synthesizes and plays speech
- `transcribe(audio_path: str) → str` — transcribes a local audio file path
- `list_voices() → str` — returns available Kokoro voices

### Configuration (env vars)
| Variable | Default |
|---|---|
| `EMBODIMENT_URL` | `http://localhost:8000` |
| `VOICE_URL` | `http://localhost:8001` |

### Resilience
- Each tool catches `httpx.ConnectError` and `httpx.TimeoutException` independently
- A down service returns a clear error string from that tool only — other tools continue working
- No auto-start logic (unlike the current `mcp_server.py`) — services are expected to be running

### Dependencies
```
mcp
httpx
```

---

## Migration: lyra-embodiment

- `lyra-embodiment/mcp_server.py` is deleted
- `lyra-embodiment/server.py` and `static/index.html` are untouched
- `lyra-embodiment/requirements.txt` drops `mcp`

---

## Out of Scope

- Continuous microphone listening (future)
- lyra_ai tool-calling / MCP client integration (separate spec)
- Game engine / Unity / Unreal support (roadmap item)
- Additional Kokoro voices beyond `bf_emma` default
