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
_lock = threading.Lock()


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
    if _model is None:
        _running = False
        return
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=FRAME_SIZE) as stream:
            while _running:
                data, _ = stream.read(FRAME_SIZE)
                scores = _model.predict(data.squeeze())
                if any(v >= WAKEWORD_THRESHOLD for v in scores.values()):
                    _fire(f"{LISTEN_URL}/activate", {})
                    _fire(f"{EMBODIMENT_URL}/state", {"state": "curious"})
                    _increment_counter()
    except Exception as e:
        print(f"[wakeword] audio error: {e} — stopping", flush=True)
    finally:
        _running = False


@app.get("/wakeword/status")
def wakeword_status():
    return {"listening": _running, "detections_today": _detections_today}


@app.post("/wakeword/start")
def wakeword_start():
    global _running, _thread
    if _model is None:
        return {"status": "error", "detail": "model not loaded"}
    with _lock:
        if _running:
            return {"status": "already_running"}
        _running = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    return {"status": "started"}


@app.post("/wakeword/stop")
def wakeword_stop():
    global _running, _thread
    with _lock:
        _running = False
    if _thread:
        _thread.join(timeout=2.0)
    return {"status": "stopped"}
