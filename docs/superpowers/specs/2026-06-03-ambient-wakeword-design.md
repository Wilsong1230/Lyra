# Ambient & Wake Word Services — Design Spec

**Date:** 2026-06-03
**Scope:** `lyra-listen/` — two new files added alongside the existing Whisper STT service

---

## Overview

Two new FastAPI services extend `lyra-listen` with passive audio awareness:

- **`ambient_service.py`** (port 8004) — continuously classifies environmental sound using YAMNet, surfacing detections above 75% confidence for a fixed set of 13 target categories.
- **`wakeword_service.py`** (port 8005) — listens for "Hey Lyra" using an openwakeword `.onnx` model, triggering STT activation and avatar state change on detection.

The existing `server.py` (Whisper STT, port 8002) is **not modified**.

---

## ambient_service.py

### Startup

On app lifespan, the YAMNet model is loaded from TensorFlow Hub (`google/yamnet/1`) and the class map is fetched. This happens eagerly at startup so the first detection has no hidden latency. The recording loop does **not** auto-start — it waits for `POST /ambient/start`.

### Recording Loop

A single daemon thread runs a time-corrected 1-second loop:

1. Record 1s of audio at 16kHz via `sounddevice.rec()` (blocking)
2. Note wall-clock start time
3. Poll `GET localhost:8002/status` — if `recording: true`, skip inference and loop immediately
4. Run YAMNet inference on the chunk
5. If top class matches a target label and score ≥ 0.75, append to history
6. Sleep `max(0, 1.0 - elapsed)` before next iteration

This eliminates drift: each cycle self-corrects to approximately 1 second wall-clock regardless of inference duration (~50ms on CPU for a 1s clip).

### STT Pause Behavior

The service polls `GET localhost:8002/status` before each inference pass. If the endpoint returns a connection error or non-200 (e.g., the STT service hasn't yet implemented `/status`), the service logs a one-time warning and treats the STT as inactive — inference continues normally. This allows the ambient service to run independently today and automatically gain pause behavior once `/status` is added to the STT service.

### YAMNet Label Mapping

YAMNet scores all 521 AudioSet classes per chunk. Scores are averaged across frames and `argmax` is taken to get the top class. The top class name (lowercased) is checked against a substring map:

| Substring match | Surfaced label |
|---|---|
| `"music"` | `music` |
| `"dog"` | `dog` |
| `"knock"` | `door knock` |
| `"slam"` | `door slam` |
| `"ringtone"`, `"telephone bell"` | `phone ringing` |
| `"breaking"`, `"shatter"` | `glass breaking` |
| `"laughter"` | `laughter` |
| `"applause"` | `applause` |
| `"alarm"` | `alarm` |
| `"siren"` | `siren` |
| `"rain"` | `rain` |
| `"thunder"` | `thunder` |
| `"typing"`, `"computer keyboard"` | `keyboard typing` |

If the top class doesn't match any substring, the chunk is discarded silently regardless of confidence.

### State

- `_latest: dict | None` — most recent detection: `{"sound": str, "confidence": float, "timestamp": str}`
- `_history: deque(maxlen=10)` — last 10 detections
- `_running: bool` — loop control flag

All state is written from the single background thread and read from FastAPI routes. No locking is needed; Python's GIL protects individual dict/deque operations.

### Endpoints

| Method | Path | Response |
|---|---|---|
| `GET` | `/ambient` | Latest detection or `{"sound": null}` if none yet |
| `GET` | `/ambient/history` | `{"history": [...]}` — last 10 detections |
| `POST` | `/ambient/start` | Starts the loop thread (no-op if already running) |
| `POST` | `/ambient/stop` | Sets `_running = False`, joins thread |

CORS enabled (`allow_origins=["*"]`).

---

## wakeword_service.py

### Startup & Graceful Failure

On app lifespan, the service reads `WAKEWORD_MODEL_PATH` and optionally `WAKEWORD_THRESHOLD` (default `0.5`) from environment (loaded from `.env`). No API key is required. If `WAKEWORD_MODEL_PATH` is missing or the `.onnx` file does not exist at the given path, it logs a clear error message and sets `_model = None`. The service starts normally — all endpoints respond, `/wakeword/status` returns `{"listening": false}` — so nothing else in the stack crashes. The detection thread does **not** auto-start; it waits for `POST /wakeword/start`.

### Detection Thread

A daemon thread opens a `sounddevice.InputStream` at 16kHz with a fixed frame size of 1280 samples (80ms). On each frame:

1. Call `_model.predict(chunk)` — returns a score dict `{"model_name": float}`
2. If any score exceeds `WAKEWORD_THRESHOLD`, detection fires:
   - `POST localhost:8002/activate` — notifies STT to begin listening
   - `POST localhost:8000/state {"state": "curious"}` — updates avatar
3. Both calls are wrapped in `try/except`; a missing service never kills the thread
4. Increment `_detections_today` (with midnight reset logic)

### Daily Counter Reset

Before each increment, if `date.today() != _detection_date`, reset `_detections_today = 0` and update `_detection_date`. No scheduler or cron needed.

### Endpoints

| Method | Path | Response |
|---|---|---|
| `GET` | `/wakeword/status` | `{"listening": bool, "detections_today": int}` |
| `POST` | `/wakeword/start` | Starts detection thread (no-op if already running) |
| `POST` | `/wakeword/stop` | Stops detection thread |

CORS enabled (`allow_origins=["*"]`).

---

## start.sh Changes

The existing `server:app` line gains a `&` to background it; the two new services follow. The last process runs in the foreground so the script blocks naturally:

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8002 &
venv/bin/uvicorn ambient_service:app --host 0.0.0.0 --port 8004 &
venv/bin/uvicorn wakeword_service:app --host 0.0.0.0 --port 8005
```

---

## Dependencies

New packages must be installed manually into the existing venv (cannot modify `requirements.txt`):

```bash
venv/bin/pip install tensorflow tensorflow-hub openwakeword python-dotenv
```

---

## Environment Variables

Add to `lyra-listen/.env`:

```
WAKEWORD_MODEL_PATH=./hey_lyra.onnx
```

Optional overrides (defaults shown):

```
WAKEWORD_THRESHOLD=0.5
LISTEN_URL=http://localhost:8002
EMBODIMENT_URL=http://localhost:8000
```

---

## Audio Device Requirements

All three services (`server.py`, `ambient_service.py`, `wakeword_service.py`) open independent `sounddevice.InputStream` instances on the same microphone. This works correctly on:

- **macOS** — CoreAudio multiplexes the device; all streams receive audio simultaneously.
- **Linux with PulseAudio** — same behaviour; PulseAudio virtualises the device.

**Not supported:** bare ALSA on Linux without PulseAudio. Some ALSA configurations grant exclusive device access to the first opener, which would starve the remaining services. A modern desktop environment (GNOME, KDE, or any distro with PulseAudio/PipeWire) satisfies this requirement automatically.

The wakeword service intentionally does **not** pause when STT is active. "Hey Lyra" must be detectable at any time, including mid-response. Unlike `ambient_service.py`, it never polls `/status`.

## Future Work

- Add `/status` and `/activate` endpoints to the STT service (`server.py`) to fully enable ambient pause and wakeword-triggered recording.
- Consider `/ambient/subscribe` (SSE) for real-time push to the CLI or MCP server.
