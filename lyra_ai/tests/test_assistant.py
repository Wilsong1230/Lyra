from __future__ import annotations
import asyncio
import pytest
from unittest.mock import MagicMock, call
from lyra.assistant import Assistant, DEFAULT_SYSTEM


def test_stream_chat_yields_chunks():
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Hello", " world"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat("hi", "session-1")]

    chunks = asyncio.run(_run())
    assert chunks == ["Hello", " world"]


def test_stream_chat_saves_full_response_to_memory():
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Hello", " world"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    async def _run():
        async for _ in assistant.stream_chat("hi", "session-1"):
            pass

    asyncio.run(_run())

    mock_memory.add.assert_any_call("session-1", "user", "hi")
    mock_memory.add.assert_any_call("session-1", "assistant", "Hello world")


def test_stream_chat_passes_history_and_system_to_backend():
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Ok"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = [{"role": "user", "content": "prior"}]

    assistant = Assistant(backend=mock_backend, memory=mock_memory, system="Be concise.")

    async def _run():
        async for _ in assistant.stream_chat("hi", "s1"):
            pass

    asyncio.run(_run())

    mock_backend.stream_chat.assert_called_once_with(
        [{"role": "user", "content": "prior"}],
        system="Be concise.",
    )


def test_default_system_includes_tool_instructions():
    assert "[TOOL:see:screen]" in DEFAULT_SYSTEM
    assert "[TOOL:see:webcam]" in DEFAULT_SYSTEM


def test_chat_with_tools_calls_vision_and_reprompts():
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = ["[TOOL:see:screen]", "You have a Python file open."]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A code editor with Python open.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)
    result = asyncio.run(assistant.chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision))

    assert result == "You have a Python file open."
    mock_vision.assert_called_once_with("screen")
    mock_memory.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    mock_memory.add.assert_any_call("s1", "user", "[Vision result: A code editor with Python open.]")


def test_chat_with_tools_no_tool_call_returns_directly():
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "The sky is blue."
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory)
    result = asyncio.run(assistant.chat_with_tools("what color is the sky?", "s1", vision_fn=mock_vision))

    assert result == "The sky is blue."
    mock_vision.assert_not_called()
    assert mock_backend.chat.call_count == 1


def test_chat_with_tools_caps_at_three_iterations():
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "[TOOL:see:screen]"
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="still a screen")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)
    result = asyncio.run(assistant.chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision))

    assert mock_backend.chat.call_count == 3
    assert mock_vision.call_count == 3
    assert "unable to determine" in result
    assistant_tool_calls = [
        c for c in mock_memory.add.call_args_list
        if c == call("s1", "assistant", "[TOOL:see:screen]")
    ]
    assert len(assistant_tool_calls) == 3
    fallback_text = "I was unable to determine what you're looking at after several attempts."
    mock_memory.add.assert_any_call("s1", "assistant", fallback_text)


def test_chat_with_tools_raises_if_no_vision_fn_and_tool_called():
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "[TOOL:see:screen]"
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory)
    with pytest.raises(ValueError, match="vision_fn is required"):
        asyncio.run(assistant.chat_with_tools("what's on my screen?", "s1"))


def test_chat_with_tools_webcam_source():
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = ["[TOOL:see:webcam]", "I see you."]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A person at a desk.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)
    result = asyncio.run(assistant.chat_with_tools("what do you see?", "s1", vision_fn=mock_vision))

    assert result == "I see you."
    mock_vision.assert_called_once_with("webcam")


def test_stream_chat_with_tools_no_tool_yields_chunks():
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Hello", " world"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat_with_tools("hi", "s1", vision_fn=mock_vision)]

    chunks = asyncio.run(_run())
    assert chunks == ["Hello", " world"]
    mock_vision.assert_not_called()


def test_stream_chat_with_tools_resolves_tool_then_streams_final():
    mock_backend = MagicMock()
    mock_backend.stream_chat.side_effect = [
        iter(["[TOOL:see:screen]"]),
        iter(["You have", " a browser open."]),
    ]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A browser showing Google.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision)]

    chunks = asyncio.run(_run())
    assert chunks == ["You have", " a browser open."]
    mock_vision.assert_called_once_with("screen")
    mock_memory.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    mock_memory.add.assert_any_call("s1", "user", "[Vision result: A browser showing Google.]")


def test_stream_chat_with_tools_caps_at_three_iterations():
    mock_backend = MagicMock()
    mock_backend.stream_chat.side_effect = [
        iter(["[TOOL:see:screen]"]),
        iter(["[TOOL:see:screen]"]),
        iter(["[TOOL:see:screen]"]),
    ]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="still a screen")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision)]

    chunks = asyncio.run(_run())
    assert mock_backend.stream_chat.call_count == 3
    assert mock_vision.call_count == 3
    assert len(chunks) == 1
    assert "unable to determine" in chunks[0]
    assistant_calls = [c for c in mock_memory.add.call_args_list if c == call("s1", "assistant", "[TOOL:see:screen]")]
    assert len(assistant_calls) == 3


def test_stream_chat_with_tools_raises_if_no_vision_fn_and_tool_called():
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["[TOOL:see:screen]"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    async def _run():
        async for _ in assistant.stream_chat_with_tools("what's on my screen?", "s1"):
            pass

    with pytest.raises(ValueError, match="vision_fn is required"):
        asyncio.run(_run())
