# Lyra Voice + Unified MCP Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `lyra-voice` FastAPI service (Whisper STT + Kokoro TTS) and replace the per-module MCP servers with a single `lyra-mcp` unified gateway.

**Architecture:** `lyra-voice` runs on port 8001, exposes `/speak`, `/transcribe`, `/health`, and `/voices` HTTP endpoints, and auto-syncs Lyra's emotion state with `lyra-embodiment`. `lyra-mcp` is a thin FastMCP gateway that aggregates all tools (emotion + voice) by delegating to the two HTTP services — no business logic lives there. `lyra-embodiment/mcp_server.py` is deleted; its tools move to the gateway.

**Tech Stack:** FastAPI, uvicorn, kokoro (`KPipeline`), openai-whisper, sounddevice, soundfile, numpy, httpx, typer, mcp (FastMCP)

---

## File Map

**Created:**
- `lyra-voice/server.py` — FastAPI app: /speak, /transcribe, /health, /voices
- `lyra-voice/cli.py` — typer CLI for manual control
- `lyra-voice/requirements.txt`
- `lyra-voice/start.sh`
- `lyra-voice/tests/__init__.py`
- `lyra-voice/tests/test_server.py`
- `lyra-mcp/mcp_server.py` — unified MCP gateway
- `lyra-mcp/requirements.txt`
- `lyra-mcp/start.sh`
- `lyra-mcp/tests/__init__.py`
- `lyra-mcp/tests/test_mcp_server.py`

**Deleted:**
- `lyra-embodiment/mcp_server.py`

**Modified:**
- `lyra-embodiment/requirements.txt` — remove `mcp`
- `lyra-embodiment/start.sh` — remove mcp_server.py invocation

---

## Task 1: lyra-voice scaffold

**Files:**
- Create: `lyra-voice/requirements.txt`
- Create: `lyra-voice/start.sh`
- Create: `lyra-voice/tests/__init__.py`

- [ ] **Step 1: Create `lyra-voice/requirements.txt`**

```
fastapi>=0.111.0
uvicorn>=0.30.0
kokoro>=0.9.4
openai-whisper>=20240930
sounddevice>=0.4.7
soundfile>=0.12.1
numpy>=1.26.0
httpx>=0.27.0
typer>=0.12.0
pytest>=8.0.0
```

- [ ] **Step 2: Create `lyra-voice/start.sh`**

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001
```

- [ ] **Step 3: Make start.sh executable**

Run: `chmod +x lyra-voice/start.sh`

- [ ] **Step 4: Create `lyra-voice/tests/__init__.py`**

Empty file.

- [ ] **Step 5: Create the venv and install dependencies**

Run from `lyra-voice/`:
```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

- [ ] **Step 6: Commit**

```bash
git -C lyra-voice add requirements.txt start.sh tests/__init__.py
git -C lyra-voice commit -m "feat: scaffold lyra-voice service"
```

---

## Task 2: /health and /voices endpoints

**Files:**
- Create: `lyra-voice/server.py`
- Create: `lyra-voice/tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Create `lyra-voice/tests/test_server.py`:

```python
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


FAKE_AUDIO = np.zeros(24000, dtype=np.float32)


@pytest.fixture
def client():
    mock_pipeline_instance = MagicMock(return_value=[(None, None, FAKE_AUDIO)])
    mock_whisper_model = MagicMock()
    mock_whisper_model.transcribe.return_value = {"text": "hello world"}

    with patch("server.KPipeline", return_value=mock_pipeline_instance), \
         patch("server.whisper") as mock_whisper, \
         patch("server.sd"):
        mock_whisper.load_model.return_value = mock_whisper_model
        import server
        with TestClient(server.app) as c:
            yield c


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "tts_voice" in data
    assert "stt_model" in data


def test_voices_returns_list(client):
    r = client.get("/voices")
    assert r.status_code == 200
    data = r.json()
    assert "voices" in data
    assert isinstance(data["voices"], list)
    assert "bf_emma" in data["voices"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lyra-voice && venv/bin/pytest tests/test_server.py::test_health_returns_ok tests/test_server.py::test_voices_returns_list -v`

Expected: FAIL with `ModuleNotFoundError` or `ImportError` (server.py does not exist)

- [ ] **Step 3: Create `lyra-voice/server.py` with /health and /voices**

```python
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
from kokoro import KPipeline
from fastapi import FastAPI, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

KOKORO_VOICE = os.getenv("KOKORO_VOICE", "bf_emma")
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
SAMPLE_RATE = 24000

KOKORO_VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
    "am_adam", "am_michael",
    "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
]

_tts: KPipeline | None = None
_stt = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tts, _stt
    _tts = KPipeline(lang_code="b")
    _stt = whisper.load_model(WHISPER_MODEL_NAME)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "tts_voice": KOKORO_VOICE, "stt_model": WHISPER_MODEL_NAME}


@app.get("/voices")
def voices():
    return {"voices": KOKORO_VOICES}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd lyra-voice && venv/bin/pytest tests/test_server.py::test_health_returns_ok tests/test_server.py::test_voices_returns_list -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C lyra-voice add server.py tests/test_server.py
git -C lyra-voice commit -m "feat: add /health and /voices endpoints"
```

---

## Task 3: /speak endpoint

**Files:**
- Modify: `lyra-voice/server.py`
- Modify: `lyra-voice/tests/test_server.py`

- [ ] **Step 1: Add failing tests for /speak**

Append to `lyra-voice/tests/test_server.py`:

```python
def test_speak_returns_wav_bytes(client):
    r = client.post("/speak", json={"text": "Hello, this is Lyra."})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert len(r.content) > 0
    assert r.content[:4] == b"RIFF"


def test_speak_calls_emotion_sync_by_default(client):
    with patch("server.httpx") as mock_httpx:
        mock_httpx.post.return_value = MagicMock()
        r = client.post("/speak", json={"text": "Hello."})
    assert r.status_code == 200
    calls = [str(c) for c in mock_httpx.post.call_args_list]
    assert any("speaking" in c for c in calls)


def test_speak_skips_emotion_sync_when_disabled(client):
    with patch("server.httpx") as mock_httpx:
        r = client.post("/speak", json={"text": "Hello.", "sync_emotion": False})
    assert r.status_code == 200
    mock_httpx.post.assert_not_called()


def test_speak_continues_when_emotion_sync_fails(client):
    with patch("server.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("embodiment down")
        r = client.post("/speak", json={"text": "Hello."})
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lyra-voice && venv/bin/pytest tests/test_server.py::test_speak_returns_wav_bytes tests/test_server.py::test_speak_calls_emotion_sync_by_default tests/test_server.py::test_speak_skips_emotion_sync_when_disabled tests/test_server.py::test_speak_continues_when_emotion_sync_fails -v`

Expected: FAIL with `422 Unprocessable Entity` or `AttributeError` (no /speak route)

- [ ] **Step 3: Add /speak to `lyra-voice/server.py`**

Add these imports at the top (after existing imports):

```python
import io
import threading
import httpx
```

Add this model and endpoint after the `/voices` route:

```python
class SpeakRequest(BaseModel):
    text: str
    sync_emotion: bool = True


@app.post("/speak")
def speak(req: SpeakRequest):
    chunks = [audio for _, _, audio in _tts(req.text, voice=KOKORO_VOICE)]
    audio = np.concatenate(chunks)

    if req.sync_emotion:
        try:
            httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "speaking"}, timeout=2)
        except Exception:
            pass

    def _play_and_reset():
        try:
            sd.play(audio, samplerate=SAMPLE_RATE)
            sd.wait()
        except Exception:
            pass
        if req.sync_emotion:
            try:
                httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "idle"}, timeout=2)
            except Exception:
                pass

    threading.Thread(target=_play_and_reset, daemon=True).start()

    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV")
    buf.seek(0)
    return Response(content=buf.read(), media_type="audio/wav")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd lyra-voice && venv/bin/pytest tests/test_server.py::test_speak_returns_wav_bytes tests/test_server.py::test_speak_calls_emotion_sync_by_default tests/test_server.py::test_speak_skips_emotion_sync_when_disabled tests/test_server.py::test_speak_continues_when_emotion_sync_fails -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C lyra-voice add server.py tests/test_server.py
git -C lyra-voice commit -m "feat: add /speak endpoint with TTS, local playback, and emotion sync"
```

---

## Task 4: /transcribe endpoint

**Files:**
- Modify: `lyra-voice/server.py`
- Modify: `lyra-voice/tests/test_server.py`

- [ ] **Step 1: Add failing tests for /transcribe**

Append to `lyra-voice/tests/test_server.py`:

```python
import io as _io


def test_transcribe_returns_text(client):
    fake_wav = _io.BytesIO(b"RIFF" + b"\x00" * 36)
    r = client.post("/transcribe", files={"file": ("test.wav", fake_wav, "audio/wav")})
    assert r.status_code == 200
    assert "text" in r.json()
    assert r.json()["text"] == "hello world"


def test_transcribe_requires_file(client):
    r = client.post("/transcribe")
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lyra-voice && venv/bin/pytest tests/test_server.py::test_transcribe_returns_text tests/test_server.py::test_transcribe_requires_file -v`

Expected: FAIL with `405 Method Not Allowed` or `422` (no /transcribe route)

- [ ] **Step 3: Add /transcribe to `lyra-voice/server.py`**

Add this import at the top (after existing imports):

```python
import tempfile
```

Add this endpoint after the `/speak` route:

```python
@app.post("/transcribe")
async def transcribe(file: UploadFile):
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = _stt.transcribe(tmp_path)
        return {"text": result["text"]}
    finally:
        import os as _os
        _os.unlink(tmp_path)
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd lyra-voice && venv/bin/pytest tests/ -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git -C lyra-voice add server.py tests/test_server.py
git -C lyra-voice commit -m "feat: add /transcribe endpoint with Whisper STT"
```

---

## Task 5: CLI

**Files:**
- Create: `lyra-voice/cli.py`

No tests for the CLI — it is a thin wrapper over the already-tested HTTP endpoints. Manual smoke test suffices.

- [ ] **Step 1: Create `lyra-voice/cli.py`**

```python
from __future__ import annotations

import os
import sys

import httpx
import typer

app = typer.Typer(help="Lyra voice CLI — control lyra-voice from the terminal.")
BASE_URL = os.getenv("VOICE_URL", "http://localhost:8001")


@app.command()
def speak(
    text: str = typer.Argument(..., help="Text for Lyra to speak."),
    sync_emotion: bool = typer.Option(True, "--sync-emotion/--no-sync-emotion", help="Sync avatar emotion state."),
):
    """Speak text aloud using Lyra's voice."""
    try:
        resp = httpx.post(f"{BASE_URL}/speak", json={"text": text, "sync_emotion": sync_emotion}, timeout=30)
        resp.raise_for_status()
        typer.echo(f"Spoke: {text!r}")
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running. Start it with ./start.sh", err=True)
        raise typer.Exit(1)


@app.command()
def transcribe(
    path: str = typer.Argument(..., help="Path to audio file to transcribe."),
):
    """Transcribe an audio file to text."""
    try:
        with open(path, "rb") as f:
            resp = httpx.post(f"{BASE_URL}/transcribe", files={"file": f}, timeout=60)
        resp.raise_for_status()
        typer.echo(resp.json()["text"])
    except FileNotFoundError:
        typer.echo(f"Error: file not found — {path}", err=True)
        raise typer.Exit(1)
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running. Start it with ./start.sh", err=True)
        raise typer.Exit(1)


@app.command()
def voices():
    """List available Kokoro voices."""
    try:
        resp = httpx.get(f"{BASE_URL}/voices", timeout=5)
        resp.raise_for_status()
        for v in resp.json()["voices"]:
            typer.echo(v)
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running.", err=True)
        raise typer.Exit(1)


@app.command()
def health():
    """Check lyra-voice service health."""
    try:
        resp = httpx.get(f"{BASE_URL}/health", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"status:    {data['status']}")
        typer.echo(f"tts_voice: {data['tts_voice']}")
        typer.echo(f"stt_model: {data['stt_model']}")
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running.", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Commit**

```bash
git -C lyra-voice add cli.py
git -C lyra-voice commit -m "feat: add typer CLI for manual voice control"
```

---

## Task 6: lyra-mcp scaffold + embodiment tools

**Files:**
- Create: `lyra-mcp/mcp_server.py`
- Create: `lyra-mcp/requirements.txt`
- Create: `lyra-mcp/start.sh`
- Create: `lyra-mcp/tests/__init__.py`
- Create: `lyra-mcp/tests/test_mcp_server.py`

- [ ] **Step 1: Create `lyra-mcp/requirements.txt`**

```
mcp>=1.0.0
httpx>=0.27.0
pytest>=8.0.0
```

- [ ] **Step 2: Create `lyra-mcp/start.sh`**

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
venv/bin/python mcp_server.py
```

- [ ] **Step 3: Make start.sh executable**

Run: `chmod +x lyra-mcp/start.sh`

- [ ] **Step 4: Create `lyra-mcp/tests/__init__.py`**

Empty file.

- [ ] **Step 5: Write failing tests for embodiment tools**

Create `lyra-mcp/tests/test_mcp_server.py`:

```python
from unittest.mock import MagicMock, patch
import httpx


def mock_response(data):
    m = MagicMock(spec=httpx.Response)
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


@patch("mcp_server.httpx.post")
def test_set_emotion_valid(mock_post):
    mock_post.return_value = mock_response({"state": "thinking", "color": "#9B59FF"})
    from mcp_server import set_emotion
    result = set_emotion("thinking")
    assert "thinking" in result
    assert "#9B59FF" in result


@patch("mcp_server.httpx.post")
def test_set_emotion_service_down(mock_post):
    mock_post.side_effect = httpx.ConnectError("refused")
    from mcp_server import set_emotion
    result = set_emotion("idle")
    assert "Error" in result


@patch("mcp_server.httpx.get")
def test_get_state_returns_current(mock_get):
    mock_get.return_value = mock_response({"state": "curious", "color": "#00D4FF"})
    from mcp_server import get_state
    result = get_state()
    assert "curious" in result
    assert "#00D4FF" in result


@patch("mcp_server.httpx.get")
def test_get_state_service_down(mock_get):
    mock_get.side_effect = httpx.ConnectError("refused")
    from mcp_server import get_state
    result = get_state()
    assert "Error" in result
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd lyra-mcp && venv/bin/pytest tests/test_mcp_server.py::test_set_emotion_valid tests/test_mcp_server.py::test_get_state_returns_current -v`

Expected: FAIL with `ModuleNotFoundError` (mcp_server.py does not exist)

- [ ] **Step 7: Create `lyra-mcp/mcp_server.py` with embodiment tools**

```python
from __future__ import annotations

import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lyra")

EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
VOICE_URL = os.getenv("VOICE_URL", "http://localhost:8001")


@mcp.tool()
def set_emotion(state: str) -> str:
    """Set the emotional state of the Lyra avatar."""
    try:
        data = httpx.post(f"{EMBODIMENT_URL}/state", json={"state": state}, timeout=5).json()
        return f"State set to '{data['state']}' (color: {data['color']})"
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-embodiment — {e}"


@mcp.tool()
def get_state() -> str:
    """Get the current emotional state of the Lyra avatar."""
    try:
        data = httpx.get(f"{EMBODIMENT_URL}/state", timeout=5).json()
        return f"Current state: '{data['state']}' (color: {data['color']})"
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-embodiment — {e}"


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 8: Create the venv and install dependencies**

Run from `lyra-mcp/`:
```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd lyra-mcp && venv/bin/pytest tests/test_mcp_server.py::test_set_emotion_valid tests/test_mcp_server.py::test_set_emotion_service_down tests/test_mcp_server.py::test_get_state_returns_current tests/test_mcp_server.py::test_get_state_service_down -v`

Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git -C lyra-mcp add mcp_server.py requirements.txt start.sh tests/__init__.py tests/test_mcp_server.py
git -C lyra-mcp commit -m "feat: add lyra-mcp gateway with embodiment tools"
```

---

## Task 7: Voice tools in lyra-mcp gateway

**Files:**
- Modify: `lyra-mcp/mcp_server.py`
- Modify: `lyra-mcp/tests/test_mcp_server.py`

- [ ] **Step 1: Add failing tests for voice tools**

Append to `lyra-mcp/tests/test_mcp_server.py`:

```python
@patch("mcp_server.httpx.post")
def test_speak_returns_confirmation(mock_post):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_post.return_value = mock_resp
    from mcp_server import speak
    result = speak("Hello, this is Lyra.")
    assert "Hello, this is Lyra." in result


@patch("mcp_server.httpx.post")
def test_speak_service_down(mock_post):
    mock_post.side_effect = httpx.ConnectError("refused")
    from mcp_server import speak
    result = speak("Hello.")
    assert "Error" in result


@patch("mcp_server.httpx.post")
def test_transcribe_returns_text(mock_post, tmp_path):
    mock_post.return_value = mock_response({"text": "hello world"})
    audio_file = tmp_path / "test.wav"
    audio_file.write_bytes(b"RIFF" + b"\x00" * 36)
    from mcp_server import transcribe
    result = transcribe(str(audio_file))
    assert "hello world" in result


def test_transcribe_missing_file():
    from mcp_server import transcribe
    result = transcribe("/nonexistent/path.wav")
    assert "Error" in result


@patch("mcp_server.httpx.get")
def test_list_voices_returns_names(mock_get):
    mock_get.return_value = mock_response({"voices": ["bf_emma", "bm_george"]})
    from mcp_server import list_voices
    result = list_voices()
    assert "bf_emma" in result
    assert "bm_george" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd lyra-mcp && venv/bin/pytest tests/test_mcp_server.py::test_speak_returns_confirmation tests/test_mcp_server.py::test_transcribe_returns_text tests/test_mcp_server.py::test_list_voices_returns_names -v`

Expected: FAIL with `ImportError` (functions not defined yet)

- [ ] **Step 3: Add voice tools to `lyra-mcp/mcp_server.py`**

Append after the `get_state` tool (before `if __name__ == "__main__":`)

```python
@mcp.tool()
def speak(text: str, sync_emotion: bool = True) -> str:
    """Speak text aloud using Lyra's voice. Optionally syncs avatar emotion state."""
    try:
        resp = httpx.post(
            f"{VOICE_URL}/speak",
            json={"text": text, "sync_emotion": sync_emotion},
            timeout=30,
        )
        resp.raise_for_status()
        return f"Spoke: {text!r}"
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-voice — {e}"


@mcp.tool()
def transcribe(audio_path: str) -> str:
    """Transcribe a local audio file to text using Whisper."""
    try:
        with open(audio_path, "rb") as f:
            resp = httpx.post(f"{VOICE_URL}/transcribe", files={"file": f}, timeout=60)
        resp.raise_for_status()
        return resp.json()["text"]
    except FileNotFoundError:
        return f"Error: file not found — {audio_path}"
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-voice — {e}"


@mcp.tool()
def list_voices() -> str:
    """List available Kokoro voices."""
    try:
        data = httpx.get(f"{VOICE_URL}/voices", timeout=5).json()
        return ", ".join(data["voices"])
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-voice — {e}"
```

- [ ] **Step 4: Run all gateway tests**

Run: `cd lyra-mcp && venv/bin/pytest tests/ -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git -C lyra-mcp add mcp_server.py tests/test_mcp_server.py
git -C lyra-mcp commit -m "feat: add voice tools to lyra-mcp gateway"
```

---

## Task 8: Clean up lyra-embodiment

**Files:**
- Delete: `lyra-embodiment/mcp_server.py`
- Modify: `lyra-embodiment/requirements.txt`
- Modify: `lyra-embodiment/start.sh`

- [ ] **Step 1: Delete `lyra-embodiment/mcp_server.py`**

Run: `git -C lyra-embodiment rm mcp_server.py`

- [ ] **Step 2: Update `lyra-embodiment/requirements.txt`**

Remove the `mcp>=1.0.0` line. Final file:

```
fastapi>=0.111.0
uvicorn>=0.30.0
httpx>=0.27.0
pytest>=8.0.0
```

- [ ] **Step 3: Update `lyra-embodiment/start.sh`**

The current `start.sh` launches both uvicorn and `mcp_server.py`. Remove the mcp_server invocation. New file:

```bash
#!/bin/bash
cd "$(dirname "$0")"
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000
echo "lyra-embodiment running at http://localhost:8000"
```

- [ ] **Step 4: Run existing embodiment tests to verify nothing broke**

Run: `cd lyra-embodiment && venv/bin/pytest tests/test_server.py -v`

Expected: All PASS (server.py is untouched)

- [ ] **Step 5: Commit**

```bash
git -C lyra-embodiment add -u
git -C lyra-embodiment commit -m "chore: remove per-module mcp_server.py, tools now live in lyra-mcp"
```
