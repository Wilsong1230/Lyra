# Ambient & Wake Word Services — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ambient_service.py` (YAMNet sound classification, port 8004) and `wakeword_service.py` (openwakeword detection, port 8005) to `lyra-listen`, and update `start.sh` to launch all three services.

**Architecture:** Each service is a standalone FastAPI app with a single daemon thread for audio processing. The ambient service runs a time-corrected 1-second YAMNet inference loop; the wakeword service runs a continuous 80ms openwakeword frame loop. Both fail gracefully when model files or downstream services are unavailable. `server.py` is not modified.

**Tech Stack:** FastAPI, uvicorn, TensorFlow Hub (YAMNet), openwakeword, sounddevice, httpx, python-dotenv

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `lyra-listen/ambient_service.py` | Create | YAMNet classification service, port 8004 |
| `lyra-listen/wakeword_service.py` | Create | openwakeword detection service, port 8005 |
| `lyra-listen/start.sh` | Modify | Launch all three services |
| `lyra-listen/tests/test_ambient_service.py` | Create | Tests for ambient service |
| `lyra-listen/tests/test_wakeword_service.py` | Create | Tests for wakeword service |

---

### Task 1: Install dependencies

**Files:** none (venv setup only)

- [ ] **Step 1: Install new packages into the existing venv**

```bash
cd lyra-listen
venv/bin/pip install tensorflow tensorflow-hub openwakeword python-dotenv
```

Expected: all packages install without error.

- [ ] **Step 2: Verify imports**

```bash
venv/bin/python -c "import tensorflow_hub; from openwakeword.model import Model; print('ok')"
```

Expected output: `ok`

---

### Task 2: `ambient_service.py`

**Files:**
- Create: `lyra-listen/ambient_service.py`
- Create: `lyra-listen/tests/test_ambient_service.py`

- [ ] **Step 1: Write the failing tests**

Create `lyra-listen/tests/test_ambient_service.py`:

```python
from __future__ import annotations
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

NCLASSES = 521
FAKE_NAMES = [f"class_{i}" for i in range(NCLASSES)]
FAKE_NAMES[42] = "Music"
FAKE_NAMES[7] = "Dog bark"
FAKE_NAMES[100] = "Knock"
FAKE_NAMES[200] = "Laughter"
FAKE_NAMES[300] = "Computer keyboard"
FAKE_NAMES[400] = "Telephone bell ringing"


def _make_fake_csv(names: list[str]) -> bytes:
    import io, csv
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["index", "mid", "display_name"])
    w.writeheader()
    for i, name in enumerate(names):
        w.writerow({"index": i, "mid": f"/m/{i:05d}", "display_name": name})
    return buf.getvalue().encode()


FAKE_CSV = _make_fake_csv(FAKE_NAMES)


@pytest.fixture
def app_client():
    mock_yamnet = MagicMock()
    scores = np.zeros((2, NCLASSES), dtype=np.float32)
    mock_scores = MagicMock()
    mock_scores.numpy.return_value = scores
    mock_yamnet.return_value = (mock_scores, MagicMock(), MagicMock())

    mock_response = MagicMock()
    mock_response.read.return_value = FAKE_CSV  # bytes; .decode() works on real bytes

    with patch("ambient_service.hub") as mock_hub, \
         patch("ambient_service.sd"), \
         patch("ambient_service.urllib.request.urlopen", return_value=mock_response):
        mock_hub.load.return_value = mock_yamnet
        import ambient_service as svc
        svc._latest = None
        svc._history.clear()
        svc._running = False
        svc._stt_warned = False
        with TestClient(svc.app) as c:
            # Set after lifespan runs so these override whatever lifespan produced
            svc._yamnet = mock_yamnet
            svc._class_names = list(FAKE_NAMES)
            yield c, svc, mock_yamnet


# --- _match_label (pure function, no fixture needed) ---

def test_match_label_music():
    import ambient_service as svc
    assert svc._match_label("Music") == "music"

def test_match_label_dog():
    import ambient_service as svc
    assert svc._match_label("Dog bark") == "dog"

def test_match_label_case_insensitive():
    import ambient_service as svc
    assert svc._match_label("LAUGHTER") == "laughter"

def test_match_label_no_match_returns_none():
    import ambient_service as svc
    assert svc._match_label("Wind instrument") is None

def test_match_label_telephone_bell():
    import ambient_service as svc
    assert svc._match_label("Telephone bell ringing") == "phone ringing"

def test_match_label_computer_keyboard():
    import ambient_service as svc
    assert svc._match_label("Computer keyboard") == "keyboard typing"

def test_match_label_shatter():
    import ambient_service as svc
    assert svc._match_label("Glass shatter") == "glass breaking"


# --- _stt_active ---

def test_stt_active_false_on_connection_error():
    import ambient_service as svc
    svc._stt_warned = False
    with patch("ambient_service.httpx.get", side_effect=Exception("refused")):
        assert svc._stt_active() is False

def test_stt_active_true_when_recording():
    import ambient_service as svc
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"recording": True}
    with patch("ambient_service.httpx.get", return_value=mock_resp):
        assert svc._stt_active() is True

def test_stt_active_false_when_not_recording():
    import ambient_service as svc
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"recording": False}
    with patch("ambient_service.httpx.get", return_value=mock_resp):
        assert svc._stt_active() is False

def test_stt_active_false_on_non_200():
    import ambient_service as svc
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("ambient_service.httpx.get", return_value=mock_resp):
        assert svc._stt_active() is False


# --- endpoints ---

def test_get_ambient_no_detection(app_client):
    c, svc, _ = app_client
    r = c.get("/ambient")
    assert r.status_code == 200
    assert r.json() == {"sound": None}

def test_get_ambient_returns_latest(app_client):
    c, svc, _ = app_client
    svc._latest = {"sound": "music", "confidence": 0.92, "timestamp": "2026-06-03T00:00:00+00:00"}
    r = c.get("/ambient")
    assert r.json()["sound"] == "music"
    assert r.json()["confidence"] == 0.92

def test_get_ambient_history_empty(app_client):
    c, svc, _ = app_client
    r = c.get("/ambient/history")
    assert r.status_code == 200
    assert r.json() == {"history": []}

def test_get_ambient_history_returns_entries(app_client):
    c, svc, _ = app_client
    entry = {"sound": "dog", "confidence": 0.88, "timestamp": "2026-06-03T00:00:00+00:00"}
    svc._history.append(entry)
    r = c.get("/ambient/history")
    assert r.json()["history"] == [entry]

def test_post_ambient_start(app_client):
    c, svc, _ = app_client
    with patch("ambient_service.threading.Thread"):
        r = c.post("/ambient/start")
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    svc._running = False

def test_post_ambient_start_idempotent(app_client):
    c, svc, _ = app_client
    svc._running = True
    r = c.post("/ambient/start")
    assert r.json()["status"] == "already_running"
    svc._running = False

def test_post_ambient_stop(app_client):
    c, svc, _ = app_client
    r = c.post("/ambient/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"
    assert svc._running is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd lyra-listen
venv/bin/pytest tests/test_ambient_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'ambient_service'`

- [ ] **Step 3: Write `ambient_service.py`**

Create `lyra-listen/ambient_service.py`:

```python
from __future__ import annotations
import csv, io, os, threading, time, urllib.request
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import numpy as np
import sounddevice as sd
import tensorflow_hub as hub
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")
SAMPLE_RATE = 16000
CONFIDENCE_THRESHOLD = 0.75
LABEL_MAP = {
    "music": "music", "dog": "dog", "knock": "door knock", "slam": "door slam",
    "ringtone": "phone ringing", "telephone bell": "phone ringing",
    "breaking": "glass breaking", "shatter": "glass breaking",
    "laughter": "laughter", "applause": "applause", "alarm": "alarm",
    "siren": "siren", "rain": "rain", "thunder": "thunder",
    "typing": "keyboard typing", "computer keyboard": "keyboard typing",
}

_yamnet = None
_class_names: list[str] = []
_latest: dict | None = None
_history: deque = deque(maxlen=10)
_running = False
_thread: threading.Thread | None = None
_stt_warned = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _yamnet, _class_names
    _yamnet = hub.load("https://tfhub.dev/google/yamnet/1")
    raw = urllib.request.urlopen(
        "https://raw.githubusercontent.com/tensorflow/models/master/"
        "research/audioset/yamnet/yamnet_class_map.csv"
    ).read().decode()
    _class_names = [row["display_name"] for row in csv.DictReader(io.StringIO(raw))]
    print(f"[ambient] ready  classes={len(_class_names)}", flush=True)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _match_label(name: str) -> str | None:
    low = name.lower()
    for substr, label in LABEL_MAP.items():
        if substr in low:
            return label
    return None


def _stt_active() -> bool:
    global _stt_warned
    try:
        r = httpx.get(f"{LISTEN_URL}/status", timeout=0.5)
        return r.status_code == 200 and bool(r.json().get("recording"))
    except Exception:
        if not _stt_warned:
            print("[ambient] /status unavailable — STT pause disabled", flush=True)
            _stt_warned = True
        return False


def _loop():
    global _latest, _running
    while _running:
        t0 = time.monotonic()
        audio = sd.rec(SAMPLE_RATE, samplerate=SAMPLE_RATE, channels=1,
                       dtype="float32", blocking=True).squeeze()
        if not _stt_active():
            scores, _, _ = _yamnet(audio)
            mean_scores = scores.numpy().mean(axis=0)
            top_idx = int(mean_scores.argmax())
            confidence = float(mean_scores[top_idx])
            if confidence >= CONFIDENCE_THRESHOLD:
                label = _match_label(_class_names[top_idx])
                if label:
                    entry = {"sound": label, "confidence": round(confidence, 4),
                             "timestamp": datetime.now(timezone.utc).isoformat()}
                    _latest = entry
                    _history.append(entry)
        time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))


@app.get("/ambient")
def get_ambient():
    return _latest or {"sound": None}


@app.get("/ambient/history")
def get_history():
    return {"history": list(_history)}


@app.post("/ambient/start")
def start_ambient():
    global _running, _thread
    if _running:
        return {"status": "already_running"}
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    return {"status": "started"}


@app.post("/ambient/stop")
def stop_ambient():
    global _running, _thread
    _running = False
    if _thread:
        _thread.join(timeout=3.0)
    return {"status": "stopped"}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd lyra-listen
venv/bin/pytest tests/test_ambient_service.py -v
```

Expected: all 18 tests pass.

- [ ] **Step 5: Commit**

```bash
cd lyra-listen
git add ambient_service.py tests/test_ambient_service.py
git commit -m "feat(lyra-listen): add ambient sound classification service (YAMNet, port 8004)"
```

---

### Task 3: `wakeword_service.py`

**Files:**
- Create: `lyra-listen/wakeword_service.py`
- Create: `lyra-listen/tests/test_wakeword_service.py`

- [ ] **Step 1: Write the failing tests**

Create `lyra-listen/tests/test_wakeword_service.py`:

```python
from __future__ import annotations
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client_no_model():
    """Service with no model loaded (missing path)."""
    with patch("wakeword_service.sd"):
        import wakeword_service as svc
        svc._model = None
        svc._running = False
        svc._detections_today = 0
        svc._detection_date = date.today()
        with TestClient(svc.app) as c:
            yield c, svc


@pytest.fixture
def client_with_model():
    """Service with a mocked model loaded."""
    mock_model = MagicMock()
    mock_model.predict.return_value = {"hey_lyra": 0.0}

    with patch("wakeword_service.sd"):
        import wakeword_service as svc
        svc._model = mock_model
        svc._running = False
        svc._detections_today = 0
        svc._detection_date = date.today()
        with TestClient(svc.app) as c:
            yield c, svc, mock_model


# --- _fire ---

def test_fire_does_not_raise_on_connection_error():
    import wakeword_service as svc
    with patch("wakeword_service.httpx.post", side_effect=Exception("refused")):
        svc._fire("http://localhost:9999/state", {"state": "curious"})  # must not raise


def test_fire_posts_to_correct_url():
    import wakeword_service as svc
    with patch("wakeword_service.httpx.post") as mock_post:
        svc._fire("http://localhost:8000/state", {"state": "curious"})
        mock_post.assert_called_once_with(
            "http://localhost:8000/state", json={"state": "curious"}, timeout=2.0
        )


# --- _increment_counter ---

def test_increment_counter_increments():
    import wakeword_service as svc
    svc._detections_today = 0
    svc._detection_date = date.today()
    svc._increment_counter()
    assert svc._detections_today == 1


def test_increment_counter_resets_on_new_day():
    import wakeword_service as svc
    svc._detections_today = 5
    svc._detection_date = date.today() - timedelta(days=1)
    svc._increment_counter()
    assert svc._detections_today == 1
    assert svc._detection_date == date.today()


# --- endpoints: no model ---

def test_status_no_model(client_no_model):
    c, svc = client_no_model
    r = c.get("/wakeword/status")
    assert r.status_code == 200
    assert r.json() == {"listening": False, "detections_today": 0}


def test_start_no_model_returns_error(client_no_model):
    c, svc = client_no_model
    r = c.post("/wakeword/start")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_stop_no_model(client_no_model):
    c, svc = client_no_model
    r = c.post("/wakeword/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


# --- endpoints: with model ---

def test_status_with_model_not_listening(client_with_model):
    c, svc, _ = client_with_model
    r = c.get("/wakeword/status")
    assert r.json() == {"listening": False, "detections_today": 0}


def test_start_with_model_starts_thread(client_with_model):
    c, svc, _ = client_with_model
    with patch("wakeword_service.threading.Thread") as mock_thread:
        r = c.post("/wakeword/start")
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    assert mock_thread.called
    svc._running = False


def test_start_with_model_idempotent(client_with_model):
    c, svc, _ = client_with_model
    svc._running = True
    r = c.post("/wakeword/start")
    assert r.json()["status"] == "already_running"
    svc._running = False


def test_stop_sets_running_false(client_with_model):
    c, svc, _ = client_with_model
    svc._running = True
    r = c.post("/wakeword/stop")
    assert r.json()["status"] == "stopped"
    assert svc._running is False


def test_status_reflects_detections_today(client_with_model):
    c, svc, _ = client_with_model
    svc._detections_today = 7
    r = c.get("/wakeword/status")
    assert r.json()["detections_today"] == 7
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd lyra-listen
venv/bin/pytest tests/test_wakeword_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'wakeword_service'`

- [ ] **Step 3: Write `wakeword_service.py`**

Create `lyra-listen/wakeword_service.py`:

```python
from __future__ import annotations
import os, threading
from contextlib import asynccontextmanager
from datetime import date

import httpx
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")
WAKEWORD_MODEL_PATH = os.getenv("WAKEWORD_MODEL_PATH", "")
WAKEWORD_THRESHOLD = float(os.getenv("WAKEWORD_THRESHOLD", "0.5"))
FRAME_SIZE = 1280
SAMPLE_RATE = 16000

try:
    from openwakeword.model import Model
except ImportError:
    Model = None  # type: ignore

_model = None
_running = False
_thread: threading.Thread | None = None
_detections_today = 0
_detection_date = date.today()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    if not WAKEWORD_MODEL_PATH:
        print("[wakeword] WAKEWORD_MODEL_PATH not set — detection disabled", flush=True)
    elif not os.path.exists(WAKEWORD_MODEL_PATH):
        print(f"[wakeword] model not found: {WAKEWORD_MODEL_PATH!r} — detection disabled", flush=True)
    elif Model is None:
        print("[wakeword] openwakeword not installed — detection disabled", flush=True)
    else:
        _model = Model(wakeword_models=[WAKEWORD_MODEL_PATH], inference_framework="onnx")
        print(f"[wakeword] ready  model={WAKEWORD_MODEL_PATH}", flush=True)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _fire(url: str, body: dict) -> None:
    try:
        httpx.post(url, json=body, timeout=2.0)
    except Exception:
        pass


def _increment_counter() -> None:
    global _detections_today, _detection_date
    today = date.today()
    if today != _detection_date:
        _detections_today = 0
        _detection_date = today
    _detections_today += 1


def _loop() -> None:
    global _running
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=FRAME_SIZE) as stream:
        while _running:
            data, _ = stream.read(FRAME_SIZE)
            scores = _model.predict(data.squeeze())
            if any(v >= WAKEWORD_THRESHOLD for v in scores.values()):
                _fire(f"{LISTEN_URL}/activate", {})
                _fire(f"{EMBODIMENT_URL}/state", {"state": "curious"})
                _increment_counter()


@app.get("/wakeword/status")
def wakeword_status():
    return {"listening": _running, "detections_today": _detections_today}


@app.post("/wakeword/start")
def wakeword_start():
    global _running, _thread
    if _model is None:
        return {"status": "error", "detail": "model not loaded"}
    if _running:
        return {"status": "already_running"}
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    return {"status": "started"}


@app.post("/wakeword/stop")
def wakeword_stop():
    global _running, _thread
    _running = False
    if _thread:
        _thread.join(timeout=2.0)
    return {"status": "stopped"}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd lyra-listen
venv/bin/pytest tests/test_wakeword_service.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
cd lyra-listen
git add wakeword_service.py tests/test_wakeword_service.py
git commit -m "feat(lyra-listen): add wake word detection service (openwakeword, port 8005)"
```

---

### Task 4: Update `start.sh`

**Files:**
- Modify: `lyra-listen/start.sh`

- [ ] **Step 1: Update `start.sh` to launch all three services**

Replace the contents of `lyra-listen/start.sh` with:

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8002 &
venv/bin/uvicorn ambient_service:app --host 0.0.0.0 --port 8004 &
venv/bin/uvicorn wakeword_service:app --host 0.0.0.0 --port 8005
```

The original `server:app` line is preserved; it now runs in the background (`&`) so the new services can follow. The wakeword service runs in the foreground so the script blocks naturally (consistent with `start.sh` patterns elsewhere in this repo).

- [ ] **Step 2: Run the full test suite to confirm nothing regressed**

```bash
cd lyra-listen
venv/bin/pytest tests/ -v
```

Expected: all tests pass (server tests + ambient tests + wakeword tests).

- [ ] **Step 3: Commit**

```bash
cd lyra-listen
git add start.sh
git commit -m "feat(lyra-listen): launch ambient and wakeword services from start.sh"
```

---

## Environment Setup Note

Before running the services, add to `lyra-listen/.env`:

```
WAKEWORD_MODEL_PATH=./hey_lyra.onnx
```

Optional:
```
WAKEWORD_THRESHOLD=0.5
LISTEN_URL=http://localhost:8002
EMBODIMENT_URL=http://localhost:8000
```

The ambient service auto-downloads the YAMNet class map CSV from GitHub on first startup. Requires internet access at startup time.
