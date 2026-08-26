# Lyra Embodiment — Design Spec

**Date:** 2026-05-28  
**Status:** Approved

---

## What It Is

Lyra Embodiment is a standalone MCP server that gives any AI model a visual presence through an atom avatar. It is an open embodiment protocol for AI — the atom is the reference implementation. Any MCP-compatible client can call `set_emotion` to drive the avatar's state, making the AI's internal activity visible in a browser window.

---

## Architecture

Two independent Python processes communicating over HTTP on localhost:

```
MCP Client (Claude Desktop, etc.)
        ↓  MCP protocol (stdio)
  mcp_server.py  ──HTTP──►  server.py :8000
                                  ↓
                            static/index.html (served once)
                                  ↓
                     browser polls GET /state every 100ms
```

`server.py` owns all state. `mcp_server.py` is a thin adapter — it validates input, delegates to FastAPI, and formats responses. `index.html` is purely a consumer; it never writes state directly except through the manual control buttons, which POST to `/state`.

---

## Project Structure

```
lyra-embodiment/
├── server.py          # FastAPI — serves HTML, holds state (~50 lines)
├── mcp_server.py      # MCP server — tools that call FastAPI (~50 lines)
├── static/
│   └── index.html     # Three.js atom avatar (~300 lines)
├── start.sh           # launches both processes
└── README.md
```

---

## Components

### server.py

FastAPI app with three routes:

- `GET /` — serves `static/index.html`
- `GET /state` — returns `{"state": "idle", "color": "#4A9EFF"}`
- `POST /state` — body `{"state": "thinking"}`, validates against allowed states, updates global, returns new state dict

CORS enabled for all localhost origins. State is a module-level dict — no database, no classes.

The canonical state map lives here:

```python
STATES = {
    "idle":       "#4A9EFF",
    "thinking":   "#9B59FF",
    "speaking":   "#2ECC71",
    "curious":    "#00D4FF",
    "processing": "#F39C12",
    "confused":   "#FF4444",
    "focused":    "#F0F0F0",
}
```

`POST /state` validates the incoming state against `STATES.keys()` and returns HTTP 422 with a message listing valid options on failure. Color is always derived server-side so the browser and MCP clients always agree.

### mcp_server.py

FastMCP app with two tools:

**`set_emotion(state: str)`**
- Validates state is one of the 7 valid options (returns error string if not)
- Calls `POST /state` on FastAPI
- Returns confirmation string with new state and color

**`get_state()`**
- Calls `GET /state` on FastAPI
- Returns current state and color

**Startup self-heal:** On the first tool call, if the HTTP request fails with a connection error, the MCP server fires `subprocess.Popen(["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"])`, sleeps 2 seconds, and retries once. If still down, returns a plain error string. One attempt only — no retry loop.

### static/index.html

Single file, Three.js loaded from CDN. No build step, no React, plain HTML and vanilla JS.

**Visual structure:**
- Dark background `#0A0A0F`
- Three rings, each with one orbiting dot and a subtle motion trail
  - Ring 1: equatorial, flat horizontal plane
  - Ring 2: polar, 70° tilt
  - Ring 3: polar, 110° tilt, symmetric to Ring 2
- Nucleus: center sphere
- All elements use Three.js emissive material in the current state color

**Animation system:**
- Module-level animation state: current state name, target color (hex), current color (lerped toward target each frame via RGB interpolation)
- Three `THREE.Mesh` rings with one dot each, one nucleus sphere
- Each frame: advance orbit angles, apply state-specific behavior modifiers, update emissive color

**State behavior profiles:**

| State | Orbit speed | Ring tilt | Nucleus | Special |
|---|---|---|---|---|
| idle | slow, equal | centered | calm | — |
| thinking | desync'd | slowly wander | dim | — |
| speaking | slightly fast | re-centered | pulses 0.5s | — |
| curious | normal | tilt toward fixed focal point at (0, 0.5, 0) | normal | orbit radius tightens by ~20% |
| processing | fast | centered | flickers | — |
| confused | fully desync'd | wandering | dim | ring 3 reverses direction |
| focused | nearly stopped | locked | bright steady | — |

**Polling:** `setInterval` at 100ms, independent of the render loop. On connection error, logs to console and retries on the next tick.

**Manual controls:** Row of buttons at bottom of screen, one per state. Clicking calls `POST /state`. Active state button is highlighted. Failed clicks log to console silently.

### start.sh

```bash
#!/bin/bash
uvicorn server:app --host 0.0.0.0 --port 8000 &
python mcp_server.py
```

Uvicorn runs in the background; MCP server runs in the foreground (stdio). Killing the script kills the MCP server; uvicorn continues until manually stopped.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| `POST /state` with invalid state | HTTP 422, message lists valid states |
| MCP tool called with invalid state | Returns error string before making HTTP call |
| MCP tool called, FastAPI not running | Attempts to start FastAPI, waits 2s, retries once, then returns error string |
| Browser can't reach `/state` | Logs to console, retries on next 100ms tick |
| Button click POST fails | Logs to console, no visible UI error |

---

## Testing

No automated test suite. Verification is manual and interactive:

1. Run `./start.sh`
2. Open `localhost:8000`
3. Click each of the 7 state buttons — confirm color transition and animation behavior match the state profile table
4. Connect an MCP client, call `set_emotion("thinking")` — confirm avatar updates
5. Call `get_state()` — confirm returns current state and color

---

## Setup

```bash
pip install fastapi uvicorn mcp fastmcp httpx
./start.sh
```

---

## Roadmap

- Game engine support (Unity, Unreal) via WebSocket bridge
- Spatial tools — avatar position and orientation control
- Animation control — custom sequences, gesture triggers
- Additional avatar types beyond the atom reference implementation
