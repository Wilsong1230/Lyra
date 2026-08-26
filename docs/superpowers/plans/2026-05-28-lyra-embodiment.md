# Lyra Embodiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone MCP server that gives any AI model a visual presence through a Three.js atom avatar with seven emotional states.

**Architecture:** Two independent Python processes — FastAPI (`server.py`) owns state and serves the browser UI, and FastMCP (`mcp_server.py`) is a thin adapter that validates input and calls FastAPI over HTTP. The browser polls `GET /state` every 100ms and updates Three.js materials and animation behaviors. If FastAPI is not running when an MCP tool is called, `mcp_server.py` attempts to start it once before failing.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, FastMCP (via `mcp` package), httpx, Three.js r128 (CDN), pytest, plain HTML/vanilla JS (no build step).

---

## File Map

| File | Role |
|---|---|
| `server.py` | FastAPI app — state store, three HTTP routes |
| `mcp_server.py` | FastMCP app — two MCP tools, startup self-heal |
| `static/index.html` | Three.js atom — geometry, animation, polling, buttons |
| `start.sh` | Launches both processes |
| `requirements.txt` | Python dependencies |
| `tests/test_server.py` | FastAPI route tests |
| `tests/test_mcp_server.py` | MCP tool unit tests (httpx mocked) |
| `README.md` | Docs |

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `start.sh`
- Create: `static/` (empty dir, gitkeep)
- Create: `tests/__init__.py`

- [ ] **Step 1: Write `requirements.txt`**

```
fastapi>=0.111.0
uvicorn>=0.30.0
mcp>=1.0.0
httpx>=0.27.0
aiofiles>=23.0.0
pytest>=8.0.0
```

- [ ] **Step 2: Write `start.sh`**

```bash
#!/bin/bash
trap 'kill $(jobs -p) 2>/dev/null' EXIT
uvicorn server:app --host 0.0.0.0 --port 8000 &
echo "Lyra running at http://localhost:8000"
python mcp_server.py
```

- [ ] **Step 3: Create static dir and tests package**

```bash
mkdir -p static tests
touch static/.gitkeep tests/__init__.py
```

- [ ] **Step 4: Make start.sh executable**

```bash
chmod +x start.sh
```

- [ ] **Step 5: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt start.sh static/.gitkeep tests/__init__.py
git commit -m "feat: project scaffold"
```

---

## Task 2: server.py (TDD)

**Files:**
- Create: `tests/test_server.py`
- Create: `server.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server.py`:

```python
import pytest
from fastapi.testclient import TestClient


def get_client():
    from server import app, state, STATES
    state["state"] = "idle"
    state["color"] = STATES["idle"]
    return TestClient(app), state, STATES


def test_get_state_returns_idle_by_default():
    client, _, _ = get_client()
    r = client.get("/state")
    assert r.status_code == 200
    assert r.json() == {"state": "idle", "color": "#4A9EFF"}


def test_post_state_valid_returns_new_state():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "thinking"})
    assert r.status_code == 200
    assert r.json() == {"state": "thinking", "color": "#9B59FF"}


def test_post_state_invalid_returns_422():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "dancing"})
    assert r.status_code == 422
    assert "dancing" in r.json()["detail"]


def test_post_state_persists_to_get():
    client, _, _ = get_client()
    client.post("/state", json={"state": "speaking"})
    r = client.get("/state")
    assert r.json()["state"] == "speaking"


def test_all_seven_states_valid():
    client, _, STATES = get_client()
    for s, color in STATES.items():
        r = client.post("/state", json={"state": s})
        assert r.status_code == 200
        assert r.json()["color"] == color
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_server.py -v
```

Expected: `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Write `server.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_server.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: FastAPI state server with tests"
```

---

## Task 3: mcp_server.py (TDD)

**Files:**
- Create: `tests/test_mcp_server.py`
- Create: `mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_server.py`:

```python
from unittest.mock import patch, MagicMock
import httpx


def mock_response(data):
    m = MagicMock(spec=httpx.Response)
    m.json.return_value = data
    return m


@patch("mcp_server._ensure_server", return_value=True)
@patch("httpx.post")
def test_set_emotion_valid(mock_post, _ensure):
    mock_post.return_value = mock_response({"state": "thinking", "color": "#9B59FF"})
    from mcp_server import set_emotion
    result = set_emotion("thinking")
    assert "thinking" in result
    assert "#9B59FF" in result


@patch("mcp_server._ensure_server", return_value=True)
def test_set_emotion_invalid_state(_ensure):
    from mcp_server import set_emotion
    result = set_emotion("disco")
    assert "Error" in result
    assert "disco" in result


@patch("mcp_server._ensure_server", return_value=False)
def test_set_emotion_server_down(_ensure):
    from mcp_server import set_emotion
    result = set_emotion("idle")
    assert "Error" in result


@patch("mcp_server._ensure_server", return_value=True)
@patch("httpx.get")
def test_get_state_returns_current(mock_get, _ensure):
    mock_get.return_value = mock_response({"state": "curious", "color": "#00D4FF"})
    from mcp_server import get_state
    result = get_state()
    assert "curious" in result
    assert "#00D4FF" in result


@patch("mcp_server._ensure_server", return_value=False)
def test_get_state_server_down(_ensure):
    from mcp_server import get_state
    result = get_state()
    assert "Error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_mcp_server.py -v
```

Expected: `ModuleNotFoundError: No module named 'mcp_server'`

- [ ] **Step 3: Write `mcp_server.py`**

```python
import subprocess
import time
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lyra-embodiment")

FASTAPI_URL = "http://localhost:8000"
VALID_STATES = ["idle", "thinking", "speaking", "curious", "processing", "confused", "focused"]


def _ensure_server() -> bool:
    try:
        httpx.get(f"{FASTAPI_URL}/state", timeout=1)
        return True
    except httpx.ConnectError:
        subprocess.Popen(["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"])
        time.sleep(2)
        try:
            httpx.get(f"{FASTAPI_URL}/state", timeout=1)
            return True
        except httpx.ConnectError:
            return False


@mcp.tool()
def set_emotion(state: str) -> str:
    """Set the emotional state of the Lyra avatar."""
    if state not in VALID_STATES:
        return f"Error: '{state}' is not a valid state. Valid states: {', '.join(VALID_STATES)}"
    if not _ensure_server():
        return "Error: Could not connect to Lyra server. Try running ./start.sh manually."
    data = httpx.post(f"{FASTAPI_URL}/state", json={"state": state}).json()
    return f"State set to '{data['state']}' (color: {data['color']})"


@mcp.tool()
def get_state() -> str:
    """Get the current emotional state of the Lyra avatar."""
    if not _ensure_server():
        return "Error: Could not connect to Lyra server. Try running ./start.sh manually."
    data = httpx.get(f"{FASTAPI_URL}/state").json()
    return f"Current state: '{data['state']}' (color: {data['color']})"


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_mcp_server.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: 10 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py tests/test_mcp_server.py
git commit -m "feat: FastMCP server with tools and tests"
```

---

## Task 4: static/index.html — Three.js Atom Avatar

**Files:**
- Create: `static/index.html`

- [ ] **Step 1: Write `static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Lyra</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #0A0A0F; overflow: hidden; font-family: system-ui, sans-serif; }
    canvas { display: block; }
    #controls {
      position: fixed; bottom: 28px; left: 50%;
      transform: translateX(-50%);
      display: flex; gap: 8px; z-index: 10;
    }
    .btn {
      padding: 7px 14px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.15);
      color: rgba(255,255,255,0.7);
      border-radius: 6px; cursor: pointer;
      font-size: 12px; letter-spacing: 0.04em;
      transition: background 0.2s, color 0.2s;
    }
    .btn:hover { background: rgba(255,255,255,0.1); }
    .btn.active {
      border-color: var(--c);
      color: #fff;
      background: rgba(255,255,255,0.12);
      box-shadow: 0 0 10px var(--c);
    }
  </style>
</head>
<body>
  <div id="controls"></div>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script>
  // ── Scene ──────────────────────────────────────────────────────────
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(devicePixelRatio);
  renderer.setSize(innerWidth, innerHeight);
  document.body.prepend(renderer.domElement);

  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 100);
  camera.position.z = 4.5;
  scene.add(new THREE.AmbientLight(0xffffff, 0.08));

  addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });

  // ── State ──────────────────────────────────────────────────────────
  const STATES = {
    idle:       '#4A9EFF',
    thinking:   '#9B59FF',
    speaking:   '#2ECC71',
    curious:    '#00D4FF',
    processing: '#F39C12',
    confused:   '#FF4444',
    focused:    '#F0F0F0',
  };

  let currentState = 'idle';
  const targetColor  = new THREE.Color(STATES.idle);
  const currentColor = new THREE.Color(STATES.idle);

  // ── Behaviors ─────────────────────────────────────────────────────
  // speed:   per-ring orbit speed multipliers (negative = reverse)
  // drift:   rings wander their tilt angles
  // lock:    rings freeze tilt at base angle
  // tight:   orbit radius shrinks ~20%
  // pulse:   nucleus blinks at 0.5s interval
  // flicker: nucleus emissive flickers randomly
  // bright:  nucleus at max steady glow
  // dim:     nucleus at low emissive
  const B = {
    idle:       { speed: [1.00, 1.00,  1.00], drift: false, lock: false, tight: false, pulse: false, flicker: false, bright: false, dim: false },
    thinking:   { speed: [0.70, 1.30,  0.90], drift: true,  lock: false, tight: false, pulse: false, flicker: false, bright: false, dim: true  },
    speaking:   { speed: [1.40, 1.40,  1.40], drift: false, lock: false, tight: false, pulse: true,  flicker: false, bright: false, dim: false },
    curious:    { speed: [1.00, 1.00,  1.00], drift: false, lock: false, tight: true,  pulse: false, flicker: false, bright: false, dim: false },
    processing: { speed: [2.50, 2.50,  2.50], drift: false, lock: false, tight: false, pulse: false, flicker: true,  bright: false, dim: false },
    confused:   { speed: [1.50, 0.80, -1.20], drift: true,  lock: false, tight: false, pulse: false, flicker: false, bright: false, dim: true  },
    focused:    { speed: [0.05, 0.05,  0.05], drift: false, lock: true,  tight: false, pulse: false, flicker: false, bright: true,  dim: false },
  };

  // ── Geometry helpers ───────────────────────────────────────────────
  function emissiveMat(intensity) {
    return new THREE.MeshStandardMaterial({
      emissive: currentColor.clone(),
      emissiveIntensity: intensity,
      metalness: 0.4,
      roughness: 0.5,
    });
  }

  // ── Nucleus ────────────────────────────────────────────────────────
  const nucleusMat = emissiveMat(1.5);
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(0.18, 32, 32), nucleusMat));

  // ── Rings ──────────────────────────────────────────────────────────
  const ORBIT_R    = 1.05;
  const TRAIL_N    = 6;
  const BASE_TILTS = [
    [0,                 0],   // Ring 1 — equatorial
    [Math.PI * 7 / 18,  0],  // Ring 2 — 70°
    [Math.PI * 11 / 18, 0],  // Ring 3 — 110°
  ];

  const rings = BASE_TILTS.map(([bx, bz], i) => {
    const ringMat = emissiveMat(0.6);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(ORBIT_R, 0.011, 8, 96), ringMat);
    ring.rotation.set(bx, 0, bz);
    scene.add(ring);

    const dotMat = emissiveMat(2.2);
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.044, 16, 16), dotMat);
    scene.add(dot);

    const trail = Array.from({ length: TRAIL_N }, (_, j) => {
      const f   = 1 - (j + 1) / (TRAIL_N + 1);
      const mat = new THREE.MeshStandardMaterial({
        emissive: currentColor.clone(),
        emissiveIntensity: 1.6 * f,
        transparent: true,
        opacity: 0.45 * f,
      });
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.032 * f, 8, 8), mat);
      scene.add(m);
      return { mesh: m, mat };
    });

    return {
      ring, ringMat, dot, dotMat, trail,
      bx, bz,
      driftX: 0, driftZ: 0,
      angle: (i * Math.PI * 2) / 3,
      radius: ORBIT_R,
    };
  });

  // ── Animation ──────────────────────────────────────────────────────
  let pulseT = 0;

  function orbitPos(angle, radius, rx, rz) {
    return new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius, 0)
      .applyEuler(new THREE.Euler(rx, 0, rz));
  }

  function updateAtom(dt) {
    const beh = B[currentState];

    currentColor.lerp(targetColor, 1 - Math.pow(0.001, dt));

    let ni;
    if      (beh.pulse)   { pulseT += dt; ni = 1.5 + 1.5 * Math.sin(pulseT * Math.PI / 0.5); }
    else if (beh.flicker) { ni = 0.6 + Math.random() * 2.0; }
    else if (beh.bright)  { ni = 3.5; }
    else if (beh.dim)     { ni = 0.4; }
    else                  { ni = 1.5; }
    nucleusMat.emissive.copy(currentColor);
    nucleusMat.emissiveIntensity = ni;

    rings.forEach((r, i) => {
      r.angle += beh.speed[i] * 0.55 * dt;

      if (beh.drift) {
        r.driftX += (Math.random() - 0.5) * 0.004;
        r.driftZ += (Math.random() - 0.5) * 0.004;
        r.driftX  = Math.max(-0.35, Math.min(0.35, r.driftX));
        r.driftZ  = Math.max(-0.35, Math.min(0.35, r.driftZ));
      } else {
        r.driftX *= 0.94;
        r.driftZ *= 0.94;
      }

      const rx = beh.lock ? r.bx : r.bx + r.driftX;
      const rz = beh.lock ? r.bz : r.bz + r.driftZ;
      r.ring.rotation.set(rx, 0, rz);

      const tR = beh.tight ? ORBIT_R * 0.80 : ORBIT_R;
      r.radius += (tR - r.radius) * 0.04;

      const sign = beh.speed[i] < 0 ? -1 : 1;
      r.dot.position.copy(orbitPos(r.angle, r.radius, rx, rz));
      r.trail.forEach(({ mesh, mat }, j) => {
        mesh.position.copy(orbitPos(r.angle - sign * (j + 1) * 0.13, r.radius, rx, rz));
        mat.emissive.copy(currentColor);
      });

      r.ringMat.emissive.copy(currentColor);
      r.dotMat.emissive.copy(currentColor);
    });
  }

  const clock = new THREE.Clock();
  (function animate() {
    requestAnimationFrame(animate);
    updateAtom(Math.min(clock.getDelta(), 0.05));
    renderer.render(scene, camera);
  })();

  // ── State polling ──────────────────────────────────────────────────
  function applyState(data) {
    currentState = data.state;
    targetColor.set(data.color);
    pulseT = 0;
    document.querySelectorAll('.btn').forEach(b => {
      const on = b.dataset.s === currentState;
      b.classList.toggle('active', on);
      if (on) b.style.setProperty('--c', data.color);
    });
  }

  setInterval(async () => {
    try { applyState(await (await fetch('/state')).json()); }
    catch { /* retry next tick */ }
  }, 100);

  // ── Controls ───────────────────────────────────────────────────────
  const ctrl = document.getElementById('controls');
  Object.entries(STATES).forEach(([s, color]) => {
    const btn      = document.createElement('button');
    btn.className  = 'btn' + (s === 'idle' ? ' active' : '');
    btn.dataset.s  = s;
    btn.textContent = s;
    if (s === 'idle') btn.style.setProperty('--c', color);
    btn.onclick = async () => {
      try {
        applyState(await (await fetch('/state', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ state: s }),
        })).json());
      } catch (e) { console.warn('state update failed', e); }
    };
    ctrl.appendChild(btn);
  });
  </script>
</body>
</html>
```

- [ ] **Step 2: Start the server and verify the avatar renders**

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. Expected: dark canvas with three orbiting rings, a nucleus, and 7 labeled buttons at the bottom.

- [ ] **Step 3: Verify each state button changes the avatar**

Click each button in order: idle → thinking → speaking → curious → processing → confused → focused.

Expected for each:
- Color transitions smoothly to the state's color (no instant snap)
- Active button highlights with a colored glow border
- Animation behavior matches:
  - **idle** — slow equal orbits, calm nucleus
  - **thinking** — ring speeds differ, rings slowly tilt, nucleus dim
  - **speaking** — slightly faster orbits, nucleus pulses
  - **curious** — rings tighten radius, nucleus normal
  - **processing** — fast orbits, nucleus flickers
  - **confused** — desync'd speeds, ring 3 reverses, nucleus dim
  - **focused** — orbits nearly stopped, nucleus bright steady glow

Stop uvicorn with `Ctrl+C`.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: Three.js atom avatar with 7 emotional states"
```

---

## Task 5: README.md

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

Write the following content to `README.md` (use the Write tool):

    # Lyra Embodiment

    Lyra Embodiment is an open embodiment protocol for AI — a standalone MCP server that gives
    any AI model a visual presence through an atom avatar. Connect any MCP-compatible client and
    call `set_emotion` to drive the avatar's state in real time. The atom is the reference
    implementation: three orbiting rings, a pulsing nucleus, seven emotional states, all rendered
    in a browser with Three.js.

    ## Setup

        pip install fastapi uvicorn mcp httpx aiofiles
        chmod +x start.sh
        ./start.sh

    Open http://localhost:8000 to see the avatar.

    ## Usage

    **Manual:** Click any state button at the bottom of the browser window.

    **MCP client:** Connect `mcp_server.py` as an MCP server (stdio transport). Call the tools
    below from any MCP-compatible client such as Claude Desktop.

    ## MCP Tools

    ### `set_emotion(state: str)`

    Sets the avatar's emotional state. Valid states: `idle`, `thinking`, `speaking`, `curious`,
    `processing`, `confused`, `focused`.

    Example input:  `{"state": "thinking"}`
    Example output: `State set to 'thinking' (color: #9B59FF)`

    ### `get_state()`

    Returns the current emotional state and color.

    Example output: `Current state: 'thinking' (color: #9B59FF)`

    ## Roadmap

    - **Game engine support** — Unity and Unreal plugins via WebSocket bridge
    - **Spatial tools** — avatar position, orientation, and scale control
    - **Animation control** — custom sequences, gesture triggers, expressions
    - **Additional avatars** — extend beyond the atom reference implementation

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README"
```

---

## Task 6: End-to-End Integration Test

This is a manual verification checklist. No automated test needed — this project's primary interface is visual.

- [ ] **Step 1: Run the full stack**

```bash
./start.sh
```

Expected: terminal shows `Lyra running at http://localhost:8000` and the MCP server starts (no errors).

- [ ] **Step 2: Verify browser UI**

Open `http://localhost:8000`. Confirm:
- [ ] Dark background, atom renders with three rings and nucleus
- [ ] 7 buttons visible at bottom: idle, thinking, speaking, curious, processing, confused, focused
- [ ] `idle` button is highlighted in blue on load

- [ ] **Step 3: Verify MCP tools via Python**

In a separate terminal, with `./start.sh` still running:

```bash
python - <<'EOF'
import httpx

# Test get_state
r = httpx.get("http://localhost:8000/state")
print("GET /state:", r.json())

# Test set_emotion via POST
r = httpx.post("http://localhost:8000/state", json={"state": "thinking"})
print("POST thinking:", r.json())

# Verify it persisted
r = httpx.get("http://localhost:8000/state")
print("GET /state after:", r.json())

# Test invalid state
r = httpx.post("http://localhost:8000/state", json={"state": "disco"})
print("POST invalid (expect 422):", r.status_code, r.json())
EOF
```

Expected output:
```
GET /state: {'state': 'idle', 'color': '#4A9EFF'}
POST thinking: {'state': 'thinking', 'color': '#9B59FF'}
GET /state after: {'state': 'thinking', 'color': '#9B59FF'}
POST invalid (expect 422): 422 {'detail': "Invalid state 'disco'..."}
```

- [ ] **Step 4: Verify browser reflects MCP state change**

After the Python snippet above sets state to `thinking`, the browser should show:
- Purple color (`#9B59FF`)
- `thinking` button highlighted
- Ring speeds desync'd, nucleus dimmed

- [ ] **Step 5: Run full test suite one final time**

```bash
pytest -v
```

Expected: 10 tests PASS.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: Lyra Embodiment complete"
```
