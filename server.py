import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

state = {"state": "idle", "color": STATES["idle"]}
_amplitude_envelope: list[float] = []
_playback_start: float = 0.0
_WINDOW_S = 0.05  # 50ms per envelope window


class StateUpdate(BaseModel):
    state: str
    amplitude_envelope: Optional[list[float]] = None


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/state")
def get_state():
    elapsed = time.time() - _playback_start
    idx = int(elapsed / _WINDOW_S)
    amp = _amplitude_envelope[idx] if 0 <= idx < len(_amplitude_envelope) else 0.0
    return {**state, "amplitude": round(amp, 4)}


@app.post("/state")
def set_state(update: StateUpdate):
    global _amplitude_envelope, _playback_start
    if update.state not in STATES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid state '{update.state}'. Valid states: {', '.join(STATES.keys())}",
        )
    state["state"] = update.state
    state["color"] = STATES[update.state]
    if update.amplitude_envelope is not None:
        _amplitude_envelope = update.amplitude_envelope
        _playback_start = time.time()
    elif update.state != "speaking":
        _amplitude_envelope = []
    return {**state, "amplitude": 0.0}
