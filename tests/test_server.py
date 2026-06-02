import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


FAKE_AUDIO = np.zeros(24000, dtype=np.float32)


@pytest.fixture
def client():
    mock_pipeline_instance = MagicMock(return_value=[(None, None, FAKE_AUDIO)])
    mock_whisper_model = MagicMock()
    mock_whisper_model.transcribe.return_value = {"text": "hello world"}

    with patch("server.KPipeline", return_value=mock_pipeline_instance), \
         patch("server.whisper") as mock_whisper, \
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
    assert "tts_voice" in data
    assert "stt_model" in data


def test_voices_returns_list(client):
    r = client.get("/voices")
    assert r.status_code == 200
    data = r.json()
    assert "voices" in data
    assert isinstance(data["voices"], list)
    assert "bf_emma" in data["voices"]


def test_speak_returns_wav_bytes(client):
    r = client.post("/speak", json={"text": "Hello, this is Lyra."})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert len(r.content) > 0
    assert r.content[:4] == b"RIFF"


def test_speak_calls_emotion_sync_by_default(client):
    with patch("server.httpx") as mock_httpx:
        mock_httpx.post.return_value = MagicMock()
        r = client.post("/speak", json={"text": "Hello."})
    assert r.status_code == 200
    calls = [str(c) for c in mock_httpx.post.call_args_list]
    assert any("speaking" in c for c in calls)


def test_speak_skips_emotion_sync_when_disabled(client):
    with patch("server.httpx") as mock_httpx:
        r = client.post("/speak", json={"text": "Hello.", "sync_emotion": False})
    assert r.status_code == 200
    mock_httpx.post.assert_not_called()


def test_speak_continues_when_emotion_sync_fails(client):
    with patch("server.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("embodiment down")
        r = client.post("/speak", json={"text": "Hello."})
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
