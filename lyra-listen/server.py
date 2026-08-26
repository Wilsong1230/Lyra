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


@app.get("/status")
def status():
    return {"recording": _recording}


@app.post("/activate")
def activate():
    result = record_start()
    if result["status"] == "recording":
        try:
            httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "listening"}, timeout=2)
        except Exception:
            pass
    return result


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
def record_stop(req: RecordStopRequest = RecordStopRequest()):
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
        tmp_path = tmp.name
    try:
        sf.write(tmp_path, audio, RECORD_SAMPLE_RATE)
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
