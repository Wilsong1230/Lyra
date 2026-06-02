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
