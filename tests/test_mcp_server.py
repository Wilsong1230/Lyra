from unittest.mock import patch, MagicMock
import httpx


def mock_response(data):
    m = MagicMock(spec=httpx.Response)
    m.json.return_value = data
    return m


@patch("mcp_server._ensure_server", return_value=True)
@patch("httpx.post")
def test_set_emotion_valid(mock_post, _ensure):
    mock_post.return_value = mock_response({"state": "thinking", "color": "#9B59FF"})
    from mcp_server import set_emotion
    result = set_emotion("thinking")
    assert "thinking" in result
    assert "#9B59FF" in result


@patch("mcp_server._ensure_server", return_value=True)
def test_set_emotion_invalid_state(_ensure):
    from mcp_server import set_emotion
    result = set_emotion("disco")
    assert "Error" in result
    assert "disco" in result


@patch("mcp_server._ensure_server", return_value=False)
def test_set_emotion_server_down(_ensure):
    from mcp_server import set_emotion
    result = set_emotion("idle")
    assert "Error" in result


@patch("mcp_server._ensure_server", return_value=True)
@patch("httpx.get")
def test_get_state_returns_current(mock_get, _ensure):
    mock_get.return_value = mock_response({"state": "curious", "color": "#00D4FF"})
    from mcp_server import get_state
    result = get_state()
    assert "curious" in result
    assert "#00D4FF" in result


@patch("mcp_server._ensure_server", return_value=False)
def test_get_state_server_down(_ensure):
    from mcp_server import get_state
    result = get_state()
    assert "Error" in result
