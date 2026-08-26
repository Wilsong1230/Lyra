from unittest.mock import MagicMock, patch

import httpx
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
    mock_post.side_effect = Exception("unexpected error")
    r = get_client().post("/see", json={"source": "screen"})
    assert r.status_code == 200
    assert r.json()["description"].startswith("Error: vision request failed")


@patch("vision_service.httpx.post")
@patch("vision_service.ImageGrab.grab")
def test_see_4xx_returns_model_rejected(mock_grab, mock_post):
    mock_grab.return_value = Image.new("RGB", (10, 10))
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"error": {"message": "model not found"}}
    mock_post.side_effect = httpx.HTTPStatusError(
        "400 Bad Request", request=MagicMock(), response=mock_response
    )
    r = get_client().post("/see", json={"source": "screen"})
    assert r.status_code == 200
    desc = r.json()["description"]
    assert desc.startswith("Error: vision model rejected")
    assert "model not found" in desc


@patch("vision_service.httpx.post")
@patch("vision_service.ImageGrab.grab")
def test_see_connection_error_returns_unreachable(mock_grab, mock_post):
    mock_grab.return_value = Image.new("RGB", (10, 10))
    mock_post.side_effect = httpx.ConnectError("connection refused")
    r = get_client().post("/see", json={"source": "screen"})
    assert r.status_code == 200
    assert r.json()["description"].startswith("Error: vision provider unreachable")


@patch("vision_service.httpx.post")
@patch("vision_service.ImageGrab.grab")
def test_see_uses_custom_prompt(mock_grab, mock_post):
    mock_grab.return_value = Image.new("RGB", (10, 10))
    mock_post.return_value = _openrouter_mock("Yes, the user is smiling.")
    get_client().post("/see", json={"source": "screen", "prompt": "Is the user smiling?"})
    call_json = mock_post.call_args[1]["json"]
    text_parts = [p for p in call_json["messages"][0]["content"] if p["type"] == "text"]
    assert text_parts[0]["text"] == "Is the user smiling?"
