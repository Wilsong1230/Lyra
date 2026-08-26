# STT Status & Activate Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /status` and `POST /activate` to `server.py` so `ambient_service` can pause inference during STT and `wakeword_service` can trigger recording on wake word detection.

**Architecture:** Both endpoints are added directly to `server.py` alongside the existing routes. `/status` reads `_recording` directly. `/activate` calls `record_start()` (already a plain function) and on success posts `{"state": "listening"}` to the embodiment service. No new files, no new dependencies.

**Tech Stack:** FastAPI, httpx (already imported), pytest + unittest.mock

---

## Files

- Modify: `server.py` — add two route functions after the existing routes
- Modify: `tests/test_server.py` — add three new test functions

---

### Task 1: GET /status

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_status_returns_recording_false_when_idle(client):
    import server
    server._recording = False
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json() == {"recording": False}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/wilsongomez/Developer/Lyra/lyra-listen
venv/bin/pytest tests/test_server.py::test_status_returns_recording_false_when_idle -v
```

Expected: FAIL with `404 Not Found`

- [ ] **Step 3: Implement GET /status**

Add after the `/health` route in `server.py` (after line 41):

```python
@app.get("/status")
def status():
    return {"recording": _recording}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
venv/bin/pytest tests/test_server.py::test_status_returns_recording_false_when_idle -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(lyra-listen): add GET /status endpoint"
```

---

### Task 2: POST /activate

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server.py`:

```python
def test_activate_starts_recording(client):
    import server
    server._recording = False
    with patch("server.threading.Thread"), \
         patch("server.httpx.post") as mock_post:
        r = client.post("/activate")
    assert r.status_code == 200
    assert r.json()["status"] == "recording"
    mock_post.assert_called_once()
    assert "listening" in str(mock_post.call_args)
    server._recording = False


def test_activate_when_already_recording(client):
    import server
    server._recording = True
    with patch("server.httpx.post") as mock_post:
        r = client.post("/activate")
    assert r.status_code == 200
    assert r.json()["status"] == "already_recording"
    mock_post.assert_not_called()
    server._recording = False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
venv/bin/pytest tests/test_server.py::test_activate_starts_recording tests/test_server.py::test_activate_when_already_recording -v
```

Expected: FAIL with `404 Not Found`

- [ ] **Step 3: Implement POST /activate**

Add after the `/status` route in `server.py`:

```python
@app.post("/activate")
def activate():
    result = record_start()
    if result["status"] == "recording":
        try:
            httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "listening"}, timeout=2)
        except Exception:
            pass
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
venv/bin/pytest tests/test_server.py::test_activate_starts_recording tests/test_server.py::test_activate_when_already_recording -v
```

Expected: PASS

- [ ] **Step 5: Run the full test suite**

```bash
venv/bin/pytest tests/ -v
```

Expected: All tests pass (previously 8, now 11 — no regressions)

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(lyra-listen): add POST /activate endpoint"
```
