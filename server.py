from __future__ import annotations

import io
import os
import tempfile
import threading
from contextlib import asynccontextmanager

import httpx

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
_stt: whisper.Whisper | None = None


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
