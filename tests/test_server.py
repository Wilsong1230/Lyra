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
