from __future__ import annotations
import io
import sys
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from lyra.cli import main


def _run_main(inputs: list[str], mock_assistant: MagicMock | None = None) -> str:
    mock_backend = MagicMock()
    mock_backend.name = "mock"
    mock_backend.default_model = "mock-model"

    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    if mock_assistant is None:
        mock_assistant = MagicMock()

    input_iter = iter(inputs)

    def fake_read_input() -> str:
        return next(input_iter)

    with patch("builtins.input", side_effect=inputs), \
         patch("lyra.cli._read_input", side_effect=fake_read_input), \
         patch("lyra.cli.auto_select_backend", return_value=mock_backend), \
         patch("lyra.cli.ConversationMemory", return_value=mock_memory), \
         patch("lyra.cli.Assistant", return_value=mock_assistant), \
         patch("lyra.cli.set_state"), \
         patch("sys.argv", ["lyra"]):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main()
        return buf.getvalue()


def test_stream_toggle_on():
    output = _run_main(["/stream", "/quit"])
    assert "Streaming on." in output


def test_stream_toggle_off():
    output = _run_main(["/stream", "/stream", "/quit"])
    assert "Streaming on." in output
    assert "Streaming off." in output


def test_stream_appears_in_help():
    output = _run_main(["/help", "/quit"])
    assert "/stream" in output


def test_streaming_output_prints_chunks():
    mock_assistant = MagicMock()
    mock_assistant.stream_chat_with_tools.return_value = iter(["Hello", " world"])

    output = _run_main(["/stream", "hello", "/quit"], mock_assistant=mock_assistant)
    assert "Hello world" in output
    mock_assistant.stream_chat_with_tools.assert_called_once()


def test_non_streaming_path_unchanged():
    mock_assistant = MagicMock()
    mock_assistant.chat_with_tools.return_value = "Hello world"

    output = _run_main(["hello", "/quit"], mock_assistant=mock_assistant)
    assert "Hello world" in output
    mock_assistant.chat_with_tools.assert_called_once()
    mock_assistant.stream_chat_with_tools.assert_not_called()


def test_cli_calls_chat_with_tools_not_chat():
    mock_assistant = MagicMock()
    mock_assistant.chat_with_tools.return_value = "You have a terminal open."

    output = _run_main(["what's on my screen?", "/quit"], mock_assistant=mock_assistant)

    mock_assistant.chat_with_tools.assert_called_once()
    mock_assistant.chat.assert_not_called()
    assert "You have a terminal open." in output


def test_cli_calls_stream_chat_with_tools_not_stream_chat():
    mock_assistant = MagicMock()
    mock_assistant.stream_chat_with_tools.return_value = iter(["You have", " a browser open."])

    output = _run_main(["/stream", "what's on my screen?", "/quit"], mock_assistant=mock_assistant)

    mock_assistant.stream_chat_with_tools.assert_called_once()
    mock_assistant.stream_chat.assert_not_called()
    assert "You have a browser open." in output
