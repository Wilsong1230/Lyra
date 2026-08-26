from __future__ import annotations
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client_no_model():
    """Service with no model loaded (missing path)."""
    with patch("wakeword_service.sd"):
        import wakeword_service as svc
        svc._model = None
        svc._running = False
        svc._detections_today = 0
        svc._detection_date = date.today()
        with TestClient(svc.app) as c:
            yield c, svc


@pytest.fixture
def client_with_model():
    """Service with a mocked model loaded."""
    mock_model = MagicMock()
    mock_model.predict.return_value = {"hey_lyra": 0.0}

    with patch("wakeword_service.sd"):
        import wakeword_service as svc
        svc._model = mock_model
        svc._running = False
        svc._detections_today = 0
        svc._detection_date = date.today()
        with TestClient(svc.app) as c:
            yield c, svc, mock_model


# --- _fire ---

def test_fire_does_not_raise_on_connection_error():
    import wakeword_service as svc
    with patch("wakeword_service.httpx.post", side_effect=Exception("refused")):
        svc._fire("http://localhost:9999/state", {"state": "curious"})  # must not raise


def test_fire_posts_to_correct_url():
    import wakeword_service as svc
    with patch("wakeword_service.httpx.post") as mock_post:
        svc._fire("http://localhost:8000/state", {"state": "curious"})
        mock_post.assert_called_once_with(
            "http://localhost:8000/state", json={"state": "curious"}, timeout=2.0
        )


# --- _increment_counter ---

def test_increment_counter_increments():
    import wakeword_service as svc
    svc._detections_today = 0
    svc._detection_date = date.today()
    svc._increment_counter()
    assert svc._detections_today == 1


def test_increment_counter_resets_on_new_day():
    import wakeword_service as svc
    svc._detections_today = 5
    svc._detection_date = date.today() - timedelta(days=1)
    svc._increment_counter()
    assert svc._detections_today == 1
    assert svc._detection_date == date.today()


# --- endpoints: no model ---

def test_status_no_model(client_no_model):
    c, svc = client_no_model
    r = c.get("/wakeword/status")
    assert r.status_code == 200
    assert r.json() == {"listening": False, "detections_today": 0}


def test_start_no_model_returns_error(client_no_model):
    c, svc = client_no_model
    r = c.post("/wakeword/start")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_stop_no_model(client_no_model):
    c, svc = client_no_model
    r = c.post("/wakeword/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


# --- endpoints: with model ---

def test_status_with_model_not_listening(client_with_model):
    c, svc, _ = client_with_model
    r = c.get("/wakeword/status")
    assert r.json() == {"listening": False, "detections_today": 0}


def test_start_with_model_starts_thread(client_with_model):
    c, svc, _ = client_with_model
    with patch("wakeword_service.threading.Thread") as mock_thread:
        r = c.post("/wakeword/start")
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    assert mock_thread.called
    svc._running = False


def test_start_with_model_idempotent(client_with_model):
    c, svc, _ = client_with_model
    svc._running = True
    r = c.post("/wakeword/start")
    assert r.json()["status"] == "already_running"
    svc._running = False


def test_stop_sets_running_false(client_with_model):
    c, svc, _ = client_with_model
    svc._running = True
    r = c.post("/wakeword/stop")
    assert r.json()["status"] == "stopped"
    assert svc._running is False


def test_status_reflects_detections_today(client_with_model):
    c, svc, _ = client_with_model
    svc._detections_today = 7
    r = c.get("/wakeword/status")
    assert r.json()["detections_today"] == 7
