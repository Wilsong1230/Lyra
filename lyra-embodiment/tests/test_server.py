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
    data = r.json()
    assert data["state"] == "idle"
    assert data["color"] == "#4A9EFF"
    assert "amplitude" in data


def test_post_state_valid_returns_new_state():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "thinking"})
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "thinking"
    assert data["color"] == "#9B59FF"


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


def test_all_eight_states_valid():
    client, _, _ = get_client()
    for s in ["idle", "thinking", "speaking", "curious", "processing", "confused", "focused", "listening"]:
        r = client.post("/state", json={"state": s})
        assert r.status_code == 200, f"state '{s}' rejected"


def test_listening_state_has_amber_color():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "listening"})
    assert r.status_code == 200
    assert r.json()["color"] == "#FF9500"


def test_get_state_includes_amplitude():
    client, _, _ = get_client()
    r = client.get("/state")
    data = r.json()
    assert "amplitude" in data
    assert isinstance(data["amplitude"], float)


def test_post_state_with_amplitude_envelope():
    client, _, _ = get_client()
    r = client.post("/state", json={"state": "speaking", "amplitude_envelope": [0.1, 0.5, 0.9, 0.3]})
    assert r.status_code == 200
    assert r.json()["state"] == "speaking"
