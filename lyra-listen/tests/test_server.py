import io
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    mock_whisper_model = MagicMock()
    mock_whisper_model.transcribe.return_value = {"text": "hello world"}

    with patch("server.whisper") as mock_whisper, \
         patch("server.sd"):
        mock_whisper.load_model.return_value = mock_whisper_model
        import server
        with TestClient(server.app) as c:
            yield c


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "stt_model" in data
    assert "recording" in data
    assert data["recording"] is False


def test_transcribe_returns_text(client):
    fake_wav = io.BytesIO(b"RIFF" + b"\x00" * 36)
    r = client.post("/transcribe", files={"file": ("test.wav", fake_wav, "audio/wav")})
    assert r.status_code == 200
    assert r.json()["text"] == "hello world"


def test_transcribe_requires_file(client):
    r = client.post("/transcribe")
    assert r.status_code == 422


FAKE_AUDIO = np.zeros(16000, dtype=np.float32)


def test_record_start_returns_recording_status(client):
    import server
    server._recording = False
    with patch("server.threading.Thread"):
        r = client.post("/record/start")
    assert r.status_code == 200
    assert r.json()["status"] == "recording"
    server._recording = False


def test_record_start_when_already_recording(client):
    import server
    server._recording = True
    r = client.post("/record/start")
    assert r.status_code == 200
    assert r.json()["status"] == "already_recording"
    server._recording = False


def test_record_stop_when_not_recording(client):
    import server
    server._recording = False
    r = client.post("/record/stop")
    assert r.status_code == 200
    assert r.json()["text"] == ""


def test_record_stop_transcribes_audio(client):
    import server
    server._recording = True
    server._audio_chunks = [FAKE_AUDIO.reshape(-1, 1)]
    server._record_thread = None
    with patch("server.httpx.post"):
        r = client.post("/record/stop", json={"sync_emotion": False})
    assert r.status_code == 200
    assert r.json()["text"] == "hello world"


def test_record_stop_returns_empty_on_silence(client):
    import server
    server._recording = True
    server._audio_chunks = []
    server._record_thread = None
    r = client.post("/record/stop", json={"sync_emotion": False})
    assert r.status_code == 200
    assert r.json()["text"] == ""


def test_record_stop_calls_idle_when_sync_emotion_true(client):
    import server
    server._recording = True
    server._audio_chunks = [FAKE_AUDIO.reshape(-1, 1)]
    server._record_thread = None
    with patch("server.httpx.post") as mock_post:
        r = client.post("/record/stop", json={"sync_emotion": True})
    assert r.status_code == 200
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "idle" in str(call_kwargs)


def test_status_returns_recording_false_when_idle(client):
    import server
    server._recording = False
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json() == {"recording": False}


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
