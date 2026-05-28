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
}

state = {"state": "idle", "color": STATES["idle"]}


class StateUpdate(BaseModel):
    state: str


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/state")
def get_state():
    return state


@app.post("/state")
def set_state(update: StateUpdate):
    if update.state not in STATES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid state '{update.state}'. Valid states: {', '.join(STATES.keys())}",
        )
    state["state"] = update.state
    state["color"] = STATES[update.state]
    return state
