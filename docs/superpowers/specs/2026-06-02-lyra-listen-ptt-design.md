# Lyra Listen + Push-to-Talk Design Spec

**Date:** 2026-06-02
**Scope:** New `lyra-listen` service (Whisper STT + mic recording), PTT input in lyra_ai CLI, listening state in lyra-embodiment, Whisper removal from lyra-voice, lyra-mcp routing update.

**Out of Scope:** Always-on open mic (future), YAMNet environmental audio classification (future).

---

## Background

lyra-voice currently bundles both TTS (Kokoro/Coqui) and STT (Whisper) in one service. The goal is a modular architecture where each capability is independently restartable. This spec splits STT into its own service (`lyra-listen`) and adds push-to-talk input to the lyra_ai CLI.

---

## Repository Structure

```
lyra-listen/             ← NEW (port 8002)
  server.py              — Whisper STT + mic recording endpoints
  requirements.txt
  start.sh

lyra-voice/              ← SIMPLIFIED
  server.py              — TTS only, /transcribe removed, Whisper removed
  requirements.txt       — openai-whisper removed

lyra-mcp/                ← UPDATED
  mcp_server.py          — transcribe tool routes to lyra-listen:8002

lyra-embodiment/         ← UPDATED
  server.py              — "listening" state added
  static/index.html      — listening behavior + amber color

lyra_ai/                 ← UPDATED
  lyra/cli.py            — Ctrl+R PTT, readchar-based input handler
  pyproject.toml         — readchar dependency added
```

---

## lyra-listen Service

### Responsibilities
- Load Whisper at startup
- Record from the default microphone on demand
- Transcribe uploaded audio files
- Notify lyra-embodiment of listening state transitions

### Endpoints

**`POST /record/start`**
- Starts mic recording in a background thread via `sounddevice` at 16000 Hz
- Returns `{"status": "recording"}`
- No-op if already recording

**`POST /record/stop`**
```json
{ "sync_emotion": true }
```
- Stops the recording thread
- Writes captured audio to a temp file
- Runs Whisper transcription
- If `sync_emotion=true`: POSTs `idle` to lyra-embodiment after transcription
- Returns `{"text": "..."}`
- Returns `{"text": ""}` on silence or error (never 500s)

**`POST /transcribe`**
```
multipart/form-data: { "file": <audio file> }
```
- Accepts an uploaded audio file, transcribes it with Whisper
- Returns `{"text": "..."}`
- Same contract as the removed lyra-voice endpoint — MCP callers are unaffected

**`GET /health`**
```json
{ "status": "ok", "stt_model": "base", "recording": false }
```

### Configuration
| Variable | Default | Description |
|---|---|---|
| `WHISPER_MODEL` | `base` | Whisper model size |
| `EMBODIMENT_URL` | `http://localhost:8000` | lyra-embodiment base URL |

### Dependencies
```
fastapi, uvicorn, openai-whisper, sounddevice, numpy, scipy, httpx, python-multipart, pytest
```

---

## lyra-voice Simplification

- `/transcribe` endpoint removed
- Whisper model loading removed from lifespan
- `openai-whisper` removed from `requirements.txt`
- `_stt` global removed from `server.py`

---

## lyra-mcp Update

The `transcribe` tool's HTTP target changes from `VOICE_URL` to `LISTEN_URL`:

```python
LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")

@mcp.tool()
def transcribe(audio_path: str) -> str:
    # reads file, POSTs to lyra-listen:8002/transcribe
```

New env var `LISTEN_URL` added to `start.sh`.

---

## lyra-embodiment: Listening State

### server.py
```python
"listening": "#FF9500"
```

### index.html — behavior
```javascript
listening: { speed: [0.6, 0.6, 0.6], drift: false, lock: false, tight: true,
             pulse: false, flicker: false, bright: false, dim: true }
```

Rings contract inward and slow, nucleus dims — attentive rather than active. Amber color is visually distinct from all existing states.

### index.html — tilt
```javascript
listening: [[Math.PI/2, 0], [Math.PI/9, 0], [-Math.PI/9, 0]]
```
Same as idle — stable, not drifting.

---

## lyra_ai CLI: Push-to-Talk

### Input Handler

`input("you> ")` is replaced with a `readchar`-based character loop. All normal typing (printable chars, backspace, Enter) works identically to before. `Ctrl+R` (ASCII `\x12`) intercepts and enters PTT mode.

### PTT Flow

```
you> [user presses Ctrl+R]
[recording... press Enter to send]
  → POST lyra-listen:8002/record/start
  → POST lyra-embodiment:8000/state  {"state": "listening"}
[user speaks]
[user presses Enter]
  → POST lyra-listen:8002/record/stop  {"sync_emotion": false}
  → POST lyra-embodiment:8000/state  {"state": "thinking"}
you> <transcribed text printed here>
  → proceeds through normal chat flow
  → voice server handles speaking/idle transitions
```

If transcription returns empty string, CLI prints `[no speech detected]` and returns to prompt without sending.

### Dependencies Added
- `readchar>=4.0.0` added to `pyproject.toml`

---

## Migration: lyra-voice

1. Remove `_stt` global and Whisper load from lifespan
2. Remove `/transcribe` endpoint
3. Remove `openai-whisper` from `requirements.txt`
4. Rebuild venv

---

## Out of Scope

- Always-on open mic / continuous listening (next feature after PTT)
- YAMNet environmental audio classification (future lyra-listen extension)
- Wake word detection
- Multi-speaker transcription
