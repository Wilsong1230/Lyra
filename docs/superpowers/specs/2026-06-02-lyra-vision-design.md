# Lyra Vision Service — Design Spec
*2026-06-02*

## Overview

A new `lyra-vision` service that gives Lyra the ability to see — either the user's screen or webcam — by capturing a frame, sending it to a vision model via OpenRouter, and returning a text description as MCP tool output.

---

## Architecture

Two components, following the established Lyra pattern:

**`lyra-vision/`** — standalone FastAPI service on port 8003. Owns capture and inference. No dependency on any other Lyra service.

**`lyra-mcp/mcp_server.py`** — gains one new tool: `lyra_see()`. Calls `/see` on lyra-vision and manages avatar state transitions around the call.

---

## Components

### `lyra-vision/vision_service.py`

FastAPI app with a single endpoint:

```
POST /see
Body: { "source": "screen" | "webcam", "prompt": str }
Response: { "description": str }
```

**Capture:**
- `screen` → `PIL.ImageGrab.grab()` (primary monitor, full screen)
- `webcam` → `cv2.VideoCapture(0)`, single frame, immediately released

**Inference:**
- Frame encoded as base64 PNG
- POST to OpenRouter with model `google/gemini-2.0-flash-exp:free`
- User-supplied `prompt` passed as the text instruction alongside the image
- Default prompt if none supplied: `"Describe what you see."`

**Configuration (`.env`):**
```
OPENROUTER_API_KEY=...
```
Model is hardcoded — no config needed at this stage.

### `lyra-mcp/mcp_server.py` — `lyra_see()` tool

```python
def lyra_see(source: str = "screen", prompt: str = "Describe what you see.") -> str
```

Sequence:
1. `set_emotion("processing")` via embodiment
2. POST to `http://localhost:8003/see` with `source` and `prompt`, timeout=30s
3. `set_emotion("curious")` on success
4. Return `description` string to Lyra

`VISION_URL` env var (default `http://localhost:8003`) follows same pattern as `EMBODIMENT_URL` and `VOICE_URL`.

---

## Data Flow

```
User says something implying vision
  → Lyra calls lyra_see(source, prompt)
  → mcp_server sets avatar to "processing"
  → mcp_server POSTs to lyra-vision /see
  → vision_service captures frame
  → vision_service encodes as base64 PNG
  → vision_service calls OpenRouter (Gemini 2.0 Flash)
  → vision_service returns { "description": "..." }
  → mcp_server sets avatar to "curious"
  → mcp_server returns description to Lyra
  → Lyra responds based on description
  → Lyra calls set_emotion("idle") via normal flow
```

---

## Error Handling

All errors return a string (never raise to Lyra):

| Failure | Returned string |
|---------|----------------|
| Webcam unavailable | `"Error: could not access webcam"` |
| OpenRouter fails (timeout, bad key, model error) | `"Error: vision request failed — {detail}"` |
| lyra-vision unreachable | `"Error: could not reach lyra-vision — {e}"` |

HTTP timeout on MCP→vision call: 30s (matches `speak()`).

---

## Files

| File | Status | Purpose |
|------|--------|---------|
| `lyra-vision/vision_service.py` | New | FastAPI + capture + OpenRouter |
| `lyra-vision/requirements.txt` | New | fastapi, uvicorn, httpx, pillow, opencv-python, python-dotenv |
| `lyra-vision/start.sh` | New | `uvicorn vision_service:app --port 8003` |
| `lyra-mcp/mcp_server.py` | Modified | Add `lyra_see()` tool + `VISION_URL` |

No changes to lyra-embodiment, lyra-voice, or lyra-listen.

---

## macOS Setup Note

Screen capture via `PIL.ImageGrab.grab()` requires **Screen Recording** permission for the terminal (or app) running lyra-vision. Grant it in System Settings → Privacy & Security → Screen Recording if screenshots return blank images.

---

## Trigger Behavior

`lyra_see()` is on-demand only — no passive polling. Lyra decides to call it when the user's message implies vision (e.g. "what do you see?", "look at my screen", "can you see me?"). Lyra selects `source` based on context:
- `"webcam"` when the user references themselves, their face, or their physical environment
- `"screen"` when the user references their computer, an app, code, or something on their display
