from __future__ import annotations
from unittest.mock import MagicMock, patch
import json
from lyra.backends import AnthropicBackend


def _make_resp(lines: list[bytes]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.readline.side_effect = lines + [b""]
    return mock_resp


def test_anthropic_stream_chat_yields_text_chunks():
    sse_lines = [
        b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}\n',
        b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}\n',
        b'data: {"type": "message_stop"}\n',
        b'\n',
    ]
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = _make_resp(sse_lines)

    with patch("lyra.backends.httpclient.HTTPSConnection", return_value=mock_conn):
        backend = AnthropicBackend(api_key="test-key")
        chunks = list(backend.stream_chat([{"role": "user", "content": "hi"}]))

    assert chunks == ["Hello", " world"]


def test_anthropic_stream_chat_includes_system_and_stream_flag():
    sse_lines = [
        b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}\n',
        b'data: {"type": "message_stop"}\n',
    ]
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = _make_resp(sse_lines)

    with patch("lyra.backends.httpclient.HTTPSConnection", return_value=mock_conn):
        backend = AnthropicBackend(api_key="test-key")
        list(backend.stream_chat(
            [{"role": "user", "content": "hi"}],
            system="You are helpful.",
        ))

    call_args = mock_conn.request.call_args
    body = json.loads(call_args[1]["body"] if "body" in call_args[1] else call_args[0][2])
    assert body.get("system") == "You are helpful."
    assert body.get("stream") is True


from lyra.backends import CerebrasBackend


def test_openai_compat_stream_chat_yields_text_chunks():
    sse_lines = [
        b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n',
        b'data: {"choices": [{"delta": {"content": " there"}}]}\n',
        b'data: [DONE]\n',
    ]
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = _make_resp(sse_lines)

    with patch("lyra.backends.httpclient.HTTPSConnection", return_value=mock_conn):
        backend = CerebrasBackend(api_key="test-key")
        chunks = list(backend.stream_chat([{"role": "user", "content": "hi"}]))

    assert chunks == ["Hi", " there"]


def test_openai_compat_stream_chat_skips_empty_deltas():
    sse_lines = [
        b'data: {"choices": [{"delta": {"role": "assistant"}}]}\n',
        b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n',
        b'data: {"choices": [{"delta": {}}]}\n',
        b'data: [DONE]\n',
    ]
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = _make_resp(sse_lines)

    with patch("lyra.backends.httpclient.HTTPSConnection", return_value=mock_conn):
        backend = CerebrasBackend(api_key="test-key")
        chunks = list(backend.stream_chat([{"role": "user", "content": "hi"}]))

    assert chunks == ["Hello"]


from lyra.backends import OllamaBackend


def test_ollama_stream_chat_yields_text_chunks():
    ndjson_lines = [
        b'{"message": {"role": "assistant", "content": "Hey"}, "done": false}\n',
        b'{"message": {"role": "assistant", "content": "!"}, "done": false}\n',
        b'{"message": {"role": "assistant", "content": ""}, "done": true}\n',
    ]
    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = _make_resp(ndjson_lines)

    with patch("lyra.backends.httpclient.HTTPConnection", return_value=mock_conn):
        backend = OllamaBackend()
        chunks = list(backend.stream_chat([{"role": "user", "content": "hi"}]))

    assert chunks == ["Hey", "!"]
