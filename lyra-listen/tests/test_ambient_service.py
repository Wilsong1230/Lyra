from __future__ import annotations
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

NCLASSES = 521
FAKE_NAMES = [f"class_{i}" for i in range(NCLASSES)]
FAKE_NAMES[42] = "Music"
FAKE_NAMES[7] = "Dog bark"
FAKE_NAMES[100] = "Knock"
FAKE_NAMES[200] = "Laughter"
FAKE_NAMES[300] = "Computer keyboard"
FAKE_NAMES[400] = "Telephone bell ringing"


def _make_fake_csv(names: list[str]) -> bytes:
    import io, csv
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["index", "mid", "display_name"])
    w.writeheader()
    for i, name in enumerate(names):
        w.writerow({"index": i, "mid": f"/m/{i:05d}", "display_name": name})
    return buf.getvalue().encode()


FAKE_CSV = _make_fake_csv(FAKE_NAMES)


@pytest.fixture
def app_client():
    mock_yamnet = MagicMock()
    scores = np.zeros((2, NCLASSES), dtype=np.float32)
    mock_scores = MagicMock()
    mock_scores.numpy.return_value = scores
    mock_yamnet.return_value = (mock_scores, MagicMock(), MagicMock())

    mock_response = MagicMock()
    mock_response.read.return_value = FAKE_CSV  # bytes; .decode() works on real bytes

    with patch("ambient_service.hub") as mock_hub, \
         patch("ambient_service.sd"), \
         patch("ambient_service.urllib.request.urlopen", return_value=mock_response):
        mock_hub.load.return_value = mock_yamnet
        import ambient_service as svc
        svc._latest = None
        svc._history.clear()
        svc._running = False
        svc._stt_warned = False
        with TestClient(svc.app) as c:
            # Set after lifespan runs so these override whatever lifespan produced
            svc._yamnet = mock_yamnet
            svc._class_names = list(FAKE_NAMES)
            yield c, svc, mock_yamnet


# --- _match_label (pure function, no fixture needed) ---

def test_match_label_music():
    import ambient_service as svc
    assert svc._match_label("Music") == "music"

def test_match_label_dog():
    import ambient_service as svc
    assert svc._match_label("Dog bark") == "dog"

def test_match_label_case_insensitive():
    import ambient_service as svc
    assert svc._match_label("LAUGHTER") == "laughter"

def test_match_label_no_match_returns_none():
    import ambient_service as svc
    assert svc._match_label("Wind instrument") is None

def test_match_label_telephone_bell():
    import ambient_service as svc
    assert svc._match_label("Telephone bell ringing") == "phone ringing"

def test_match_label_computer_keyboard():
    import ambient_service as svc
    assert svc._match_label("Computer keyboard") == "keyboard typing"

def test_match_label_shatter():
    import ambient_service as svc
    assert svc._match_label("Glass shatter") == "glass breaking"


# --- _stt_active ---

def test_stt_active_false_on_connection_error():
    import ambient_service as svc
    svc._stt_warned = False
    with patch("ambient_service.httpx.get", side_effect=Exception("refused")):
        assert svc._stt_active() is False

def test_stt_active_true_when_recording():
    import ambient_service as svc
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"recording": True}
    with patch("ambient_service.httpx.get", return_value=mock_resp):
        assert svc._stt_active() is True

def test_stt_active_false_when_not_recording():
    import ambient_service as svc
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"recording": False}
    with patch("ambient_service.httpx.get", return_value=mock_resp):
        assert svc._stt_active() is False

def test_stt_active_false_on_non_200():
    import ambient_service as svc
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("ambient_service.httpx.get", return_value=mock_resp):
        assert svc._stt_active() is False


# --- endpoints ---

def test_get_ambient_no_detection(app_client):
    c, svc, _ = app_client
    r = c.get("/ambient")
    assert r.status_code == 200
    assert r.json() == {"sound": None}

def test_get_ambient_returns_latest(app_client):
    c, svc, _ = app_client
    svc._latest = {"sound": "music", "confidence": 0.92, "timestamp": "2026-06-03T00:00:00+00:00"}
    r = c.get("/ambient")
    assert r.json()["sound"] == "music"
    assert r.json()["confidence"] == 0.92

def test_get_ambient_history_empty(app_client):
    c, svc, _ = app_client
    r = c.get("/ambient/history")
    assert r.status_code == 200
    assert r.json() == {"history": []}

def test_get_ambient_history_returns_entries(app_client):
    c, svc, _ = app_client
    entry = {"sound": "dog", "confidence": 0.88, "timestamp": "2026-06-03T00:00:00+00:00"}
    svc._history.append(entry)
    r = c.get("/ambient/history")
    assert r.json()["history"] == [entry]

def test_post_ambient_start(app_client):
    c, svc, _ = app_client
    with patch("ambient_service.threading.Thread"):
        r = c.post("/ambient/start")
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    svc._running = False

def test_post_ambient_start_idempotent(app_client):
    c, svc, _ = app_client
    svc._running = True
    r = c.post("/ambient/start")
    assert r.json()["status"] == "already_running"
    svc._running = False

def test_post_ambient_stop(app_client):
    c, svc, _ = app_client
    r = c.post("/ambient/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"
    assert svc._running is False
