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
