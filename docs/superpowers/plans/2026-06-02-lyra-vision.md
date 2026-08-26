# Lyra Vision Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `lyra-vision` FastAPI service on port 8003 that captures screen or webcam frames, queries OpenRouter for a description, and exposes a `lyra_see()` MCP tool in `lyra-mcp`.

**Architecture:** A new `lyra-vision/` directory (mirrors `lyra-listen/`) runs the capture + inference service. The `lyra-mcp/mcp_server.py` gains one new tool `lyra_see()` that sets avatar state to `processing`, calls `/see`, then sets avatar to `curious` before returning the description.

**Tech Stack:** FastAPI, uvicorn, Pillow (screen capture), opencv-python (webcam), httpx (OpenRouter), python-dotenv, pytest + FastAPI TestClient, unittest.mock.

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `lyra-vision/vision_service.py` | Create | FastAPI app — `/see` endpoint, capture, OpenRouter call |
| `lyra-vision/requirements.txt` | Create | Dependencies |
| `lyra-vision/start.sh` | Create | Launch uvicorn on port 8003 |
| `lyra-vision/.env.example` | Create | Template for `OPENROUTER_API_KEY` |
| `lyra-vision/.gitignore` | Create | Exclude `.env` and `venv/` |
| `lyra-vision/tests/__init__.py` | Create | Empty, marks tests as package |
| `lyra-vision/tests/test_vision_service.py` | Create | Tests for `/see` endpoint |
| `lyra-mcp/mcp_server.py` | Modify | Add `VISION_URL` + `lyra_see()` tool |
| `lyra-mcp/tests/test_mcp_server.py` | Modify | Add tests for `lyra_see()` |

---

## Task 1: Scaffold lyra-vision directory

**Files:**
- Create: `lyra-vision/requirements.txt`
- Create: `lyra-vision/start.sh`
- Create: `lyra-vision/.env.example`
- Create: `lyra-vision/.gitignore`
- Create: `lyra-vision/tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.111.0
uvicorn>=0.30.0
httpx>=0.27.0
pillow>=10.0.0
opencv-python>=4.9.0
python-dotenv>=1.0.0
numpy>=1.26.0
pytest>=8.0.0
```

Save to: `lyra-vision/requirements.txt`

- [ ] **Step 2: Create start.sh**

```bash
#!/bin/bash
cd "$(dirname "$0")"
venv/bin/uvicorn vision_service:app --host 0.0.0.0 --port 8003
```

Save to: `lyra-vision/start.sh`, then run:
```bash
chmod +x lyra-vision/start.sh
```

- [ ] **Step 3: Create .env.example**

```
OPENROUTER_API_KEY=your_key_here
```

Save to: `lyra-vision/.env.example`

- [ ] **Step 4: Create .gitignore**

```
.env
venv/
__pycache__/
*.pyc
.pytest_cache/
```

Save to: `lyra-vision/.gitignore`

- [ ] **Step 5: Create tests/__init__.py**

Empty file. Run:
```bash
mkdir -p lyra-vision/tests && touch lyra-vision/tests/__init__.py
```

- [ ] **Step 6: Set up venv and install dependencies**

```bash
cd lyra-vision
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Expected: no errors, packages installed.

- [ ] **Step 7: Copy your real OpenRouter key into .env**

```bash
echo "OPENROUTER_API_KEY=<your_real_key>" > lyra-vision/.env
```

- [ ] **Step 8: Commit scaffold**

```bash
git -C lyra-vision init  # only if lyra-vision has no git repo yet
git -C lyra-vision add requirements.txt start.sh .env.example .gitignore tests/__init__.py
git -C lyra-vision commit -m "feat: scaffold lyra-vision service"
```

---

## Task 2: Implement vision_service.py (TDD)

**Files:**
- Create: `lyra-vision/tests/test_vision_service.py`
- Create: `lyra-vision/vision_service.py`

- [ ] **Step 1: Write all failing tests**

Save to `lyra-vision/tests/test_vision_service.py`:

```python
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image


def get_client():
    from vision_service import app
    return TestClient(app)


def _openrouter_mock(description: str) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"choices": [{"message": {"content": description}}]}
    return m


@patch("vision_service.httpx.post")
@patch("vision_service.ImageGrab.grab")
def test_see_screen_returns_description(mock_grab, mock_post):
    mock_grab.return_value = Image.new("RGB", (10, 10))
    mock_post.return_value = _openrouter_mock("A terminal with Python code.")
    r = get_client().post("/see", json={"source": "screen"})
    assert r.status_code == 200
    assert r.json()["description"] == "A terminal with Python code."


@patch("vision_service.httpx.post")
@patch("vision_service.cv2.VideoCapture")
def test_see_webcam_returns_description(mock_vc, mock_post):
    mock_cap = MagicMock()
    mock_cap.read.return_value = (True, np.zeros((10, 10, 3), dtype=np.uint8))
    mock_vc.return_value = mock_cap
    mock_post.return_value = _openrouter_mock("A person at a desk.")
    r = get_client().post("/see", json={"source": "webcam"})
    assert r.status_code == 200
    assert r.json()["description"] == "A person at a desk."


@patch("vision_service.cv2.VideoCapture")
def test_see_webcam_unavailable_returns_error(mock_vc):
    mock_cap = MagicMock()
    mock_cap.read.return_value = (False, None)
    mock_vc.return_value = mock_cap
    r = get_client().post("/see", json={"source": "webcam"})
    assert r.status_code == 200
    assert r.json()["description"] == "Error: could not access webcam"


def test_see_invalid_source_returns_422():
    r = get_client().post("/see", json={"source": "microphone"})
    assert r.status_code == 422
    assert "microphone" in r.json()["detail"]


@patch("vision_service.httpx.post")
@patch("vision_service.ImageGrab.grab")
def test_see_openrouter_failure_returns_error(mock_grab, mock_post):
    mock_grab.return_value = Image.new("RGB", (10, 10))
    mock_post.side_effect = Exception("connection refused")
    r = get_client().post("/see", json={"source": "screen"})
    assert r.status_code == 200
    assert r.json()["description"].startswith("Error: vision request failed")


@patch("vision_service.httpx.post")
@patch("vision_service.ImageGrab.grab")
def test_see_uses_custom_prompt(mock_grab, mock_post):
    mock_grab.return_value = Image.new("RGB", (10, 10))
    mock_post.return_value = _openrouter_mock("Yes, the user is smiling.")
    get_client().post("/see", json={"source": "screen", "prompt": "Is the user smiling?"})
    call_json = mock_post.call_args[1]["json"]
    text_parts = [p for p in call_json["messages"][0]["content"] if p["type"] == "text"]
    assert text_parts[0]["text"] == "Is the user smiling?"
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
cd lyra-vision
venv/bin/pytest tests/test_vision_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'vision_service'` (or similar import error).

- [ ] **Step 3: Write vision_service.py**

Save to `lyra-vision/vision_service.py`:

```python
from __future__ import annotations

import base64
import io
import os

import cv2
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageGrab
from pydantic import BaseModel

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.0-flash-exp:free"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SeeRequest(BaseModel):
    source: str = "screen"
    prompt: str = "Describe what you see."


def _capture_webcam() -> Image.Image | None:
    cap = cv2.VideoCapture(0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@app.post("/see")
def see(req: SeeRequest):
    if req.source not in ("screen", "webcam"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source '{req.source}'. Use 'screen' or 'webcam'.",
        )

    if req.source == "screen":
        img = ImageGrab.grab()
    else:
        img = _capture_webcam()
        if img is None:
            return {"description": "Error: could not access webcam"}

    b64 = _to_base64(img)

    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                            {"type": "text", "text": req.prompt},
                        ],
                    }
                ],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return {"description": resp.json()["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"description": f"Error: vision request failed — {e}"}
```

- [ ] **Step 4: Run tests to verify they all pass**

```bash
cd lyra-vision
venv/bin/pytest tests/test_vision_service.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git -C lyra-vision add vision_service.py tests/test_vision_service.py
git -C lyra-vision commit -m "feat: add /see endpoint with screen and webcam capture"
```

---

## Task 3: Add lyra_see() to lyra-mcp/mcp_server.py (TDD)

**Files:**
- Modify: `lyra-mcp/mcp_server.py`
- Modify: `lyra-mcp/tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Append to `lyra-mcp/tests/test_mcp_server.py`:

```python
@patch("mcp_server.httpx.post")
def test_lyra_see_returns_description(mock_post):
    processing_resp = mock_response({"state": "processing", "color": "#F39C12"})
    see_resp = MagicMock(spec=httpx.Response)
    see_resp.raise_for_status.return_value = None
    see_resp.json.return_value = {"description": "A terminal with Python code."}
    curious_resp = mock_response({"state": "curious", "color": "#00D4FF"})
    mock_post.side_effect = [processing_resp, see_resp, curious_resp]

    from mcp_server import lyra_see
    result = lyra_see(source="screen", prompt="What do you see?")
    assert result == "A terminal with Python code."


@patch("mcp_server.httpx.post")
def test_lyra_see_vision_service_down(mock_post):
    processing_resp = mock_response({"state": "processing", "color": "#F39C12"})
    mock_post.side_effect = [processing_resp, httpx.ConnectError("refused")]

    from mcp_server import lyra_see
    result = lyra_see()
    assert "Error" in result
    assert "lyra-vision" in result


@patch("mcp_server.httpx.post")
def test_lyra_see_webcam_source(mock_post):
    processing_resp = mock_response({"state": "processing", "color": "#F39C12"})
    see_resp = MagicMock(spec=httpx.Response)
    see_resp.raise_for_status.return_value = None
    see_resp.json.return_value = {"description": "A smiling person."}
    curious_resp = mock_response({"state": "curious", "color": "#00D4FF"})
    mock_post.side_effect = [processing_resp, see_resp, curious_resp]

    from mcp_server import lyra_see
    result = lyra_see(source="webcam", prompt="Who is there?")
    assert result == "A smiling person."
    # Verify the /see call used port 8003
    see_call_url = mock_post.call_args_list[1][0][0]
    assert "8003" in see_call_url
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd lyra-mcp
venv/bin/pytest tests/test_mcp_server.py::test_lyra_see_returns_description -v
```

Expected: `ImportError` or `AttributeError: module 'mcp_server' has no attribute 'lyra_see'`.

- [ ] **Step 3: Add VISION_URL and lyra_see() to mcp_server.py**

In `lyra-mcp/mcp_server.py`, add `VISION_URL` alongside the other URL constants:

```python
VISION_URL = os.getenv("VISION_URL", "http://localhost:8003")
```

Then add the tool after `list_voices`:

```python
@mcp.tool()
def lyra_see(source: str = "screen", prompt: str = "Describe what you see.") -> str:
    """See the user's screen or webcam. source: 'screen' (default) or 'webcam'. Lyra picks based on context."""
    try:
        httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "processing"}, timeout=5)
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    try:
        resp = httpx.post(
            f"{VISION_URL}/see",
            json={"source": source, "prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        description = resp.json()["description"]
        try:
            httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "curious"}, timeout=5)
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        return description
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        return f"Error: could not reach lyra-vision — {e}"
```

- [ ] **Step 4: Run all mcp_server tests**

```bash
cd lyra-mcp
venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: all tests PASSED (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git -C lyra-mcp add mcp_server.py tests/test_mcp_server.py
git -C lyra-mcp commit -m "feat: add lyra_see() MCP tool for screen and webcam vision"
```

---

## Smoke Test (manual)

After all tasks complete:

- [ ] Start lyra-vision: `cd lyra-vision && ./start.sh`
- [ ] Start lyra-embodiment (if not running): `cd lyra-embodiment && ./start.sh`
- [ ] Test screen capture directly:
  ```bash
  curl -s -X POST http://localhost:8003/see \
    -H "Content-Type: application/json" \
    -d '{"source":"screen","prompt":"What do you see?"}' | python3 -m json.tool
  ```
  Expected: `{"description": "...some description of your screen..."}` (not an error string)
- [ ] Restart lyra-mcp and ask Lyra: *"What's on my screen?"* — avatar should go `processing` → `curious`, and Lyra should describe what she sees.
