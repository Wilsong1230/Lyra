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
_lock = threading.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _yamnet, _class_names
    try:
        _yamnet = hub.load("https://tfhub.dev/google/yamnet/1")
        raw = urllib.request.urlopen(
            "https://raw.githubusercontent.com/tensorflow/models/master/"
            "research/audioset/yamnet/yamnet_class_map.csv"
        ).read().decode()
        _class_names = [row["display_name"] for row in csv.DictReader(io.StringIO(raw))]
        print(f"[ambient] ready  classes={len(_class_names)}", flush=True)
    except Exception as e:
        print(f"[ambient] startup failed: {e} — service will run but produce no detections", flush=True)
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
        if _yamnet is not None and not _stt_active():
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
    with _lock:
        if _running:
            return {"status": "already_running"}
        _running = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
    return {"status": "started"}
@app.post("/ambient/stop")
def stop_ambient():
    global _running, _thread
    with _lock:
        _running = False
    if _thread:
        _thread.join(timeout=3.0)
    return {"status": "stopped"}
