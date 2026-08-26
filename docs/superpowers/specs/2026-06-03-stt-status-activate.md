# STT Status & Activate Endpoints — Design Spec

**Date:** 2026-06-03
**Scope:** `lyra-listen/server.py` — two new endpoints added, no other files touched

---

## Overview

Two endpoints are added to the existing Whisper STT service (`server.py`, port 8002):

- **`GET /status`** — returns the current recording state so `ambient_service.py` can pause inference while STT is active.
- **`POST /activate`** — triggered by `wakeword_service.py` on "Hey Lyra" detection; starts recording immediately and notifies the avatar to enter the "listening" state.

The existing endpoints (`/health`, `/transcribe`, `/record/start`, `/record/stop`) are **not modified**.

---

## GET /status

Returns `{"recording": bool}`.

Reads `_recording` directly — same value already exposed in `/health`. This endpoint exists as a dedicated, stable contract for `ambient_service.py` (which polls it every second) so that `/health` can evolve independently.

**Response:**
```json
{"recording": true}
```

---

## POST /activate

Starts recording by calling `record_start()` directly (it is a plain synchronous function with no request dependencies). Returns the same response as `/record/start`:

- `{"status": "recording"}` — recording started successfully
- `{"status": "already_recording"}` — idempotent; recording was already in progress

On successful activation (status is `"recording"`), posts `{"state": "listening"}` to `EMBODIMENT_URL/state` to sync the avatar. The call is wrapped in `try/except`; a missing embodiment service never prevents activation.

**Why notify the avatar here:** `wakeword_service.py` already posts `{"state": "curious"}` the moment the wake word fires. The avatar should transition to `"listening"` once recording actually starts — that happens here, not in the wakeword service.

---

## What Does Not Change

- `/record/start` — unchanged; still available for push-to-talk use
- `/record/stop` — unchanged
- `/health` — unchanged
- `/transcribe` — unchanged

---

## Testing

Two new test cases added to `tests/test_server.py`:

1. `GET /status` returns `{"recording": false}` when idle
2. `POST /activate` when not recording: starts recording, calls embodiment `/state` with `"listening"`, returns `{"status": "recording"}`
3. `POST /activate` when already recording: returns `{"status": "already_recording"}`, does not call embodiment again

The existing fixture already patches `sd.InputStream` and `whisper.load_model` — the new tests reuse that pattern.
