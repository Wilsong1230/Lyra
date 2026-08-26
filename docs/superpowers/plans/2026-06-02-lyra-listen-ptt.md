# Lyra Listen + Push-to-Talk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone `lyra-listen` service (Whisper STT + mic recording), add Ctrl+R push-to-talk to the lyra_ai CLI, add a listening avatar state, and remove Whisper from lyra-voice.

**Architecture:** lyra-listen (port 8002) owns all audio input — mic recording via sounddevice and Whisper transcription. lyra-voice is simplified to TTS-only. lyra_ai CLI uses readchar to intercept Ctrl+R, starts/stops recording over HTTP, and feeds the transcribed text into the normal chat flow. lyra-mcp routes the `transcribe` tool to lyra-listen.

**Tech Stack:** FastAPI, openai-whisper, sounddevice, soundfile, readchar, pytest, Three.js (frontend avatar)

---

## File Map

**Create:**
- `lyra-listen/server.py` — Whisper STT + mic recording endpoints
- `lyra-listen/requirements.txt`
- `lyra-listen/start.sh`
- `lyra-listen/tests/__init__.py`
- `lyra-listen/tests/test_server.py`

**Modify:**
- `lyra-voice/server.py` — remove Whisper/STT (_stt, lifespan load, /transcribe, /health stt_model)
- `lyra-voice/requirements.txt` — remove openai-whisper
- `lyra-voice/tests/test_server.py` — remove Whisper fixture patches + transcribe tests, fix health assertion
- `lyra-embodiment/server.py` — add "listening" state
- `lyra-embodiment/static/index.html` — add listening to STATES, B, STATE_TILTS; fix amplitude in tests
- `lyra-embodiment/tests/test_server.py` — update exact-dict assertions, add listening + amplitude tests
- `lyra-mcp/mcp_server.py` — transcribe tool uses LISTEN_URL
- `lyra-mcp/start.sh` — add LISTEN_URL env export
- `lyra-mcp/tests/test_mcp_server.py` — update transcribe test URL reference
- `lyra_ai/pyproject.toml` — add readchar dependency
- `lyra_ai/lyra/cli.py` — readchar input handler, PTT flow, LISTEN_URL

---

### Task 1: lyra-listen — scaffold, /health, /transcribe

**Files:**
- Create: `lyra-listen/server.py`
- Create: `lyra-listen/requirements.txt`
- Create: `lyra-listen/start.sh`
- Create: `lyra-listen/tests/__init__.py`
- Test: `lyra-listen/tests/test_server.py`

- [ ] **Step 1: Create the lyra-listen directory and init git**

```bash
mkdir -p /Users/wilsongomez/Developer/Lyra/lyra-listen/tests
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
git init
touch tests/__init__.py
```

- [ ] **Step 2: Write the failing tests for /health and /transcribe**

Create `lyra-listen/tests/test_server.py`:

```python
import io
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    mock_whisper_model = MagicMock()
    mock_whisper_model.transcribe.return_value = {"text": "hello world"}

    with patch("server.whisper") as mock_whisper, \
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
    assert "stt_model" in data
    assert "recording" in data
    assert data["recording"] is False


def test_transcribe_returns_text(client):
    fake_wav = io.BytesIO(b"RIFF" + b"\x00" * 36)
    r = client.post("/transcribe", files={"file": ("test.wav", fake_wav, "audio/wav")})
    assert r.status_code == 200
    assert r.json()["text"] == "hello world"


def test_transcribe_requires_file(client):
    r = client.post("/transcribe")
    assert r.status_code == 422
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
python -m pytest tests/test_server.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 4: Create requirements.txt**

```
fastapi>=0.111.0
uvicorn>=0.30.0
openai-whisper>=20240930
sounddevice>=0.4.7
soundfile>=0.12.1
numpy>=1.26.0
httpx>=0.27.0
python-multipart>=0.0.9
pytest>=8.0.0
```

- [ ] **Step 5: Create the venv and install deps**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
/opt/homebrew/bin/python3.12 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q
echo "done"
```

Expected: `done`

- [ ] **Step 6: Create server.py with /health and /transcribe**

```python
from __future__ import annotations

import os
import tempfile
import threading
from contextlib import asynccontextmanager

import httpx
import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
from fastapi import FastAPI, UploadFile
from pydantic import BaseModel

WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
RECORD_SAMPLE_RATE = 16000

_stt: whisper.Whisper | None = None
_recording = False
_audio_chunks: list[np.ndarray] = []
_record_thread: threading.Thread | None = None
_record_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stt
    _stt = whisper.load_model(WHISPER_MODEL_NAME)
    print(f"[listen] ready  model={WHISPER_MODEL_NAME}", flush=True)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "stt_model": WHISPER_MODEL_NAME, "recording": _recording}


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
        os.unlink(tmp_path)
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
venv/bin/pytest tests/test_server.py -v
```

Expected: `3 passed`

- [ ] **Step 8: Create start.sh**

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8002
```

```bash
chmod +x /Users/wilsongomez/Developer/Lyra/lyra-listen/start.sh
```

- [ ] **Step 9: Commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
git add .
git commit -m "feat: scaffold lyra-listen with Whisper /health and /transcribe"
```

---

### Task 2: lyra-listen — /record/start and /record/stop

**Files:**
- Modify: `lyra-listen/server.py`
- Modify: `lyra-listen/tests/test_server.py`

- [ ] **Step 1: Write failing tests for recording endpoints**

Append to `lyra-listen/tests/test_server.py`:

```python
import numpy as np

FAKE_AUDIO = np.zeros(16000, dtype=np.float32)


def test_record_start_returns_recording_status(client):
    import server
    server._recording = False
    with patch("server.threading.Thread"):
        r = client.post("/record/start")
    assert r.status_code == 200
    assert r.json()["status"] == "recording"
    server._recording = False


def test_record_start_when_already_recording(client):
    import server
    server._recording = True
    r = client.post("/record/start")
    assert r.status_code == 200
    assert r.json()["status"] == "already_recording"
    server._recording = False


def test_record_stop_when_not_recording(client):
    import server
    server._recording = False
    r = client.post("/record/stop")
    assert r.status_code == 200
    assert r.json()["text"] == ""


def test_record_stop_transcribes_audio(client):
    import server
    server._recording = True
    server._audio_chunks = [FAKE_AUDIO.reshape(-1, 1)]
    server._record_thread = None
    with patch("server.httpx.post"):
        r = client.post("/record/stop", json={"sync_emotion": False})
    assert r.status_code == 200
    assert r.json()["text"] == "hello world"


def test_record_stop_returns_empty_on_silence(client):
    import server
    server._recording = True
    server._audio_chunks = []
    server._record_thread = None
    r = client.post("/record/stop", json={"sync_emotion": False})
    assert r.status_code == 200
    assert r.json()["text"] == ""
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
venv/bin/pytest tests/test_server.py -v 2>&1 | tail -10
```

Expected: 5 new tests failing with `404` or `AttributeError`

- [ ] **Step 3: Add /record/start and /record/stop to server.py**

Add after the `/transcribe` endpoint:

```python
@app.post("/record/start")
def record_start():
    global _recording, _audio_chunks, _record_thread
    with _record_lock:
        if _recording:
            return {"status": "already_recording"}
        _recording = True
        _audio_chunks = []

    def _capture():
        with sd.InputStream(samplerate=RECORD_SAMPLE_RATE, channels=1, dtype="float32") as stream:
            while _recording:
                chunk, _ = stream.read(1024)
                _audio_chunks.append(chunk.copy())

    _record_thread = threading.Thread(target=_capture, daemon=True)
    _record_thread.start()
    return {"status": "recording"}


class RecordStopRequest(BaseModel):
    sync_emotion: bool = True


@app.post("/record/stop")
def record_stop(req: RecordStopRequest):
    global _recording, _record_thread
    with _record_lock:
        if not _recording:
            return {"text": ""}
        _recording = False

    if _record_thread:
        _record_thread.join(timeout=2.0)

    if not _audio_chunks:
        return {"text": ""}

    audio = np.concatenate(_audio_chunks).squeeze()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        sf.write(tmp.name, audio, RECORD_SAMPLE_RATE)
        tmp_path = tmp.name

    try:
        result = _stt.transcribe(tmp_path)
        return {"text": result["text"].strip()}
    except Exception as e:
        print(f"[listen] transcription error: {e}", flush=True)
        return {"text": ""}
    finally:
        os.unlink(tmp_path)
        if req.sync_emotion:
            try:
                httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "idle"}, timeout=2)
            except Exception:
                pass
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
venv/bin/pytest tests/test_server.py -v
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
git add server.py tests/test_server.py
git commit -m "feat: add /record/start and /record/stop endpoints"
```

---

### Task 3: lyra-voice — strip Whisper/STT

**Files:**
- Modify: `lyra-voice/server.py`
- Modify: `lyra-voice/requirements.txt`
- Modify: `lyra-voice/tests/test_server.py`

- [ ] **Step 1: Update tests first — remove Whisper fixture patches and transcribe tests**

Replace `lyra-voice/tests/test_server.py` with:

```python
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


FAKE_AUDIO = np.zeros(24000, dtype=np.float32)


@pytest.fixture
def client():
    mock_pipeline_instance = MagicMock(return_value=[(None, None, FAKE_AUDIO)])

    with patch("server.KPipeline", return_value=mock_pipeline_instance), \
         patch("server.sd"):
        import server
        with TestClient(server.app) as c:
            yield c


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "tts_voice" in data
    assert "engine" in data


def test_voices_returns_list(client):
    r = client.get("/voices")
    assert r.status_code == 200
    data = r.json()
    assert "voices" in data
    assert isinstance(data["voices"], list)
    assert "bf_emma" in data["voices"]


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

- [ ] **Step 2: Run tests to verify they currently fail (Whisper still present)**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-voice
venv/bin/pytest tests/test_server.py -v 2>&1 | tail -10
```

Expected: failures due to Whisper still in fixture

- [ ] **Step 3: Remove Whisper from server.py**

Replace the full `lyra-voice/server.py` with:

```python
from __future__ import annotations

import io
import os
import threading
from contextlib import asynccontextmanager

import torch

_real_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _real_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

import httpx
import numpy as np
import sounddevice as sd
import soundfile as sf
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

TTS_ENGINE = os.getenv("TTS_ENGINE", "kokoro")

KOKORO_VOICE = os.getenv("KOKORO_VOICE", "bf_emma")
COQUI_VOICE = os.getenv("COQUI_VOICE", "Claribel Dervla")
COQUI_LANG = os.getenv("COQUI_LANG", "en")
EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
SAMPLE_RATE = 24000

KOKORO_VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
    "am_adam", "am_michael",
    "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
]

COQUI_VOICES = [
    "Claribel Dervla", "Daisy Studious", "Grace Oshea", "Gracie Wise",
    "Tammie Ema", "Alison Dietlinde", "Ana Florence", "Annmarie Nele",
    "Asya Anara", "Brenda Stern", "Gitta Nikolaus", "Henriette Usha",
    "Sofia Hellen", "Tammy Grit", "Tanja Adelina", "Vjollca Johnnie",
    "Andrew Chipper", "Badr Odhiambo", "Dionisio Schuyler", "Royston Min",
    "Viktor Eka", "Abrahan Mack", "Adde Michal", "Baldur Sanjin",
    "Craig Gutsy", "Damien Black", "Gilberto Mathias", "Ilkin Urbano",
]

_kokoro = None
_coqui = None

_ENV_WINDOW = int(SAMPLE_RATE * 0.05)


def _amplitude_envelope(audio: np.ndarray) -> list[float]:
    n = len(audio) // _ENV_WINDOW
    rms = [float(np.sqrt(np.mean(audio[i * _ENV_WINDOW:(i + 1) * _ENV_WINDOW] ** 2))) for i in range(n)]
    peak = max(rms) if rms else 1.0
    return [round(v / peak, 4) for v in rms] if peak > 0 else rms


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kokoro, _coqui
    if TTS_ENGINE == "coqui":
        from TTS.api import TTS
        _coqui = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=torch.cuda.is_available())
    else:
        from kokoro import KPipeline
        _kokoro = KPipeline(lang_code="b")
    print(f"[voice] ready  engine={TTS_ENGINE}", flush=True)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    voice = COQUI_VOICE if TTS_ENGINE == "coqui" else KOKORO_VOICE
    return {"status": "ok", "engine": TTS_ENGINE, "tts_voice": voice}


@app.get("/voices")
def voices():
    return {"voices": COQUI_VOICES if TTS_ENGINE == "coqui" else KOKORO_VOICES}


class SpeakRequest(BaseModel):
    text: str
    sync_emotion: bool = True


@app.post("/speak")
def speak(req: SpeakRequest):
    if TTS_ENGINE == "coqui":
        wav = _coqui.tts(text=req.text, speaker=COQUI_VOICE, language=COQUI_LANG)
        audio = np.array(wav, dtype=np.float32)
    else:
        chunks = [a for _, _, a in _kokoro(req.text, voice=KOKORO_VOICE)]
        audio = np.concatenate(chunks)

    if req.sync_emotion:
        try:
            envelope = _amplitude_envelope(audio)
            httpx.post(f"{EMBODIMENT_URL}/state",
                       json={"state": "speaking", "amplitude_envelope": envelope}, timeout=2)
        except Exception:
            pass

    def _play_and_reset():
        try:
            device = sd.default.device[1]
            sd.play(audio, samplerate=SAMPLE_RATE, device=device, blocking=True)
        except Exception as e:
            print(f"[voice] playback error: {e}", flush=True)
        finally:
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

- [ ] **Step 4: Remove openai-whisper from requirements.txt**

Edit `lyra-voice/requirements.txt` — remove the line `openai-whisper>=20240930`. Final file:

```
fastapi>=0.111.0
uvicorn>=0.30.0
kokoro>=0.7.16
TTS>=0.22.0
transformers==4.43.4
sounddevice>=0.4.7
soundfile>=0.12.1
numpy>=1.26.0
httpx>=0.27.0
python-multipart>=0.0.9
typer>=0.12.0
pytest>=8.0.0
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-voice
venv/bin/pytest tests/test_server.py -v
```

Expected: `6 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-voice
git add server.py requirements.txt tests/test_server.py
git commit -m "refactor: remove Whisper/STT — lyra-voice is TTS-only"
```

---

### Task 4: lyra-embodiment — listening state

**Files:**
- Modify: `lyra-embodiment/server.py`
- Modify: `lyra-embodiment/static/index.html`
- Modify: `lyra-embodiment/tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Replace `lyra-embodiment/tests/test_server.py` with:

```python
import pytest
from fastapi.testclient import TestClient


def get_client():
    from server import app, state, STATES
    state["state"] = "idle"
    state["color"] = STATES["idle"]
    return TestClient(app), state, STATES


def test_get_state_returns_idle_by_default():
    client, _, _ = get_client()
    r = client.get("/state")
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "idle"
    assert data["color"] == "#4A9EFF"
    assert "amplitude" in data


def test_post_state_valid_returns_new_state():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "thinking"})
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "thinking"
    assert data["color"] == "#9B59FF"


def test_post_state_invalid_returns_422():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "dancing"})
    assert r.status_code == 422
    assert "dancing" in r.json()["detail"]


def test_post_state_persists_to_get():
    client, _, _ = get_client()
    client.post("/state", json={"state": "speaking"})
    r = client.get("/state")
    assert r.json()["state"] == "speaking"


def test_all_eight_states_valid():
    client, _, _ = get_client()
    for s in ["idle", "thinking", "speaking", "curious", "processing", "confused", "focused", "listening"]:
        r = client.post("/state", json={"state": s})
        assert r.status_code == 200, f"state '{s}' rejected"


def test_listening_state_has_amber_color():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "listening"})
    assert r.status_code == 200
    assert r.json()["color"] == "#FF9500"


def test_get_state_includes_amplitude():
    client, _, _ = get_client()
    r = client.get("/state")
    data = r.json()
    assert "amplitude" in data
    assert isinstance(data["amplitude"], float)


def test_post_state_with_amplitude_envelope():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "speaking", "amplitude_envelope": [0.1, 0.5, 0.9, 0.3]})
    assert r.status_code == 200
    assert r.json()["state"] == "speaking"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-embodiment
venv/bin/pytest tests/test_server.py -v 2>&1 | tail -15
```

Expected: `test_all_eight_states_valid` and `test_listening_state_has_amber_color` fail with 422

- [ ] **Step 3: Add listening to server.py STATES**

In `lyra-embodiment/server.py`, update the STATES dict:

```python
STATES = {
    "idle":       "#4A9EFF",
    "thinking":   "#9B59FF",
    "speaking":   "#2ECC71",
    "curious":    "#00D4FF",
    "processing": "#F39C12",
    "confused":   "#FF4444",
    "focused":    "#F0F0F0",
    "listening":  "#FF9500",
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-embodiment
venv/bin/pytest tests/test_server.py -v
```

Expected: `8 passed`

- [ ] **Step 5: Add listening to the frontend — STATES, B, STATE_TILTS**

In `lyra-embodiment/static/index.html`, make three additions:

**Add to STATES object** (after `focused`):
```javascript
listening:  '#FF9500',
```

**Add to B (behaviors) object** (after `focused`):
```javascript
listening:  { speed: [0.60, 0.60, 0.60], drift: false, lock: false, tight: true,  pulse: false, flicker: false, bright: false, dim: true  },
```

**Add to STATE_TILTS object** (after `focused`):
```javascript
listening:  [[Math.PI/2, 0],          [Math.PI/9, 0],            [-Math.PI/9, 0]          ],
```

- [ ] **Step 6: Verify the avatar loads in the browser**

Start lyra-embodiment: `./start.sh`

Open `http://localhost:8000` in a browser. Confirm:
- A "listening" button appears in the controls row at the bottom
- Clicking it turns the avatar amber (#FF9500), rings contract inward and slow

- [ ] **Step 7: Commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-embodiment
git add server.py static/index.html tests/test_server.py
git commit -m "feat: add listening state — amber color, contracted rings"
```

---

### Task 5: lyra-mcp — route transcribe to lyra-listen

**Files:**
- Modify: `lyra-mcp/mcp_server.py`
- Modify: `lyra-mcp/start.sh`
- Modify: `lyra-mcp/tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

In `lyra-mcp/tests/test_mcp_server.py`, find the existing `test_transcribe_returns_text` test and replace it:

```python
@patch("mcp_server.httpx.post")
def test_transcribe_returns_text(mock_post, tmp_path):
    mock_post.return_value = mock_response({"text": "hello world"})
    audio_file = tmp_path / "test.wav"
    audio_file.write_bytes(b"RIFF" + b"\x00" * 36)
    from mcp_server import transcribe
    result = transcribe(str(audio_file))
    assert "hello world" in result
    # Verify it called lyra-listen (port 8002), not lyra-voice (port 8001)
    call_url = mock_post.call_args[0][0]
    assert "8002" in call_url
```

- [ ] **Step 2: Run to verify test fails**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-mcp
venv/bin/pytest tests/test_mcp_server.py::test_transcribe_returns_text -v
```

Expected: FAIL — `assert "8002" in call_url` fails (currently hits 8001)

- [ ] **Step 3: Update mcp_server.py**

Add `LISTEN_URL` and update the `transcribe` tool in `lyra-mcp/mcp_server.py`:

```python
LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")
```

Replace the `transcribe` tool:

```python
@mcp.tool()
def transcribe(audio_path: str) -> str:
    """Transcribe a local audio file to text using Whisper."""
    try:
        with open(audio_path, "rb") as f:
            resp = httpx.post(f"{LISTEN_URL}/transcribe", files={"file": f}, timeout=60)
        resp.raise_for_status()
        return resp.json()["text"]
    except FileNotFoundError:
        return f"Error: file not found — {audio_path}"
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        return f"Error: could not reach lyra-listen — {e}"
```

- [ ] **Step 4: Update start.sh to export LISTEN_URL**

Replace `lyra-mcp/start.sh` with:

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export EMBODIMENT_URL="${EMBODIMENT_URL:-http://localhost:8000}"
export VOICE_URL="${VOICE_URL:-http://localhost:8001}"
export LISTEN_URL="${LISTEN_URL:-http://localhost:8002}"
venv/bin/python mcp_server.py
```

- [ ] **Step 5: Run all tests to verify they pass**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-mcp
venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-mcp
git add mcp_server.py start.sh tests/test_mcp_server.py
git commit -m "feat: route transcribe tool to lyra-listen:8002"
```

---

### Task 6: lyra_ai — push-to-talk input handler

**Files:**
- Modify: `lyra_ai/pyproject.toml`
- Modify: `lyra_ai/lyra/cli.py`

- [ ] **Step 1: Add readchar to pyproject.toml**

In `lyra_ai/pyproject.toml`, update the dependencies field:

```toml
dependencies = ["readchar>=4.0.0"]
```

Install it:

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra_ai
.venv/bin/pip install "readchar>=4.0.0" -q
```

- [ ] **Step 2: Verify readchar works**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra_ai
.venv/bin/python -c "import readchar; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Update cli.py — add LISTEN_URL, update _post_json, add helpers**

At the top of `lyra_ai/lyra/cli.py`, update imports and add constants. The current imports section:

```python
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from http.client import HTTPConnection
from urllib.parse import urlparse
```

Becomes:

```python
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from http.client import HTTPConnection
from urllib.parse import urlparse

import readchar
```

Add `LISTEN_URL` after the existing URL constants:

```python
EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
VOICE_URL = os.getenv("VOICE_URL", "http://localhost:8001")
LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")
```

Update `_post_json` to return the parsed response (set_state and speak_response callers safely ignore the return value):

```python
def _post_json(url: str, body: dict, timeout: float = 1.0) -> dict:
    parsed = urlparse(url)
    conn = HTTPConnection(parsed.netloc, timeout=timeout)
    data = json.dumps(body).encode()
    conn.request("POST", parsed.path, body=data, headers={"Content-Type": "application/json"})
    raw = conn.getresponse().read()
    try:
        return json.loads(raw)
    except Exception:
        return {}
```

Add two helpers after `speak_response`:

```python
_PTT_KEY  = "\x12"   # Ctrl+R
_CTRL_C   = "\x03"
_CTRL_D   = "\x04"
_ESC      = "\x1b"
_BACKSPACE = ("\x7f", "\x08")


def _read_input() -> str | None:
    """Read a line from stdin char-by-char. Returns None if Ctrl+R pressed."""
    print("you> ", end="", flush=True)
    buf: list[str] = []
    while True:
        ch = readchar.readchar()
        if ch == _PTT_KEY:
            print()
            return None
        elif ch in ("\r", "\n"):
            print()
            return "".join(buf)
        elif ch in _BACKSPACE:
            if buf:
                buf.pop()
                print("\b \b", end="", flush=True)
        elif ch == _CTRL_C:
            raise KeyboardInterrupt
        elif ch == _CTRL_D:
            raise EOFError
        elif ch == _ESC:
            try:
                readchar.readchar()
                readchar.readchar()
            except Exception:
                pass
        elif ch.isprintable():
            buf.append(ch)
            print(ch, end="", flush=True)


def _wait_for_enter() -> None:
    """Block until Enter is pressed, discarding other input."""
    while True:
        ch = readchar.readchar()
        if ch in ("\r", "\n"):
            return
```

- [ ] **Step 4: Update the HELP_TEXT to include /voice and PTT instructions**

Replace the `HELP_TEXT` constant:

```python
HELP_TEXT = """\
Commands:
  /help             Show this help
  /quit             Exit
  /stream           Toggle streaming output on/off
  /history          Print this session's conversation
  /clear            Erase this session's history
  /session list     List all saved sessions
  /session new      Start a fresh session
  /session <id>     Switch to an existing session
  /backend          Show current backend
  /model            Show current model
  /voice            Toggle voice output on/off

Push-to-talk: press Ctrl+R at the prompt, speak, press Enter to send.
"""
```

- [ ] **Step 5: Replace the input() call and add PTT flow in the main loop**

Find this block in the `while True` loop:

```python
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
```

Replace it with:

```python
        try:
            raw = _read_input()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if raw is None:
            print("[recording... press Enter to send]", flush=True)
            try:
                _post_json(f"{LISTEN_URL}/record/start", {})
                set_state("listening")
                _wait_for_enter()
                result = _post_json(f"{LISTEN_URL}/record/stop",
                                    {"sync_emotion": False}, timeout=30.0)
                user_input = result.get("text", "").strip()
                set_state("thinking")
                if not user_input:
                    print("[no speech detected]", flush=True)
                    set_state("idle")
                    continue
                print(f"you> {user_input}")
            except Exception as e:
                print(f"[ptt error] {e}", file=sys.stderr)
                set_state("idle")
                continue
        else:
            user_input = raw.strip()

        if not user_input:
            continue
```

- [ ] **Step 6: Smoke test PTT end-to-end**

With lyra-listen running (`./start.sh` in lyra-listen), start lyra_ai:

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra_ai
python main.py
```

Press `Ctrl+R` at the prompt. Verify:
- Terminal prints `[recording... press Enter to send]`
- lyra-listen terminal shows the recording started
- Avatar turns amber (if lyra-embodiment is running)
- Press Enter → lyra_ai prints the transcribed text and sends it to the AI

- [ ] **Step 7: Commit**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra_ai
git add lyra/cli.py pyproject.toml
git commit -m "feat: add Ctrl+R push-to-talk via lyra-listen"
```

---

## Push All Repos

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen && git remote add origin <your-remote-url> && git push -u origin main
cd /Users/wilsongomez/Developer/Lyra/lyra-voice && git push
cd /Users/wilsongomez/Developer/Lyra/lyra-embodiment && git push
cd /Users/wilsongomez/Developer/Lyra/lyra-mcp && git push
cd /Users/wilsongomez/Developer/Lyra/lyra_ai && git push
```
