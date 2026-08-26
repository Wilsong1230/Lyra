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
from kokoro import KPipeline
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
