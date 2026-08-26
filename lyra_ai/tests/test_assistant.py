from __future__ import annotations
import asyncio
import pytest
from unittest.mock import MagicMock, call
from lyra.assistant import Assistant, DEFAULT_SYSTEM, LAYER1_FACTS
from lyra_core.affect import AffectEngine
from lyra_core.interface import AffectState, CognitiveCore


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


# ── Persona strip: Layer 1 facts-only ──────────────────────────────────────────

_BANNED_ADJECTIVES = [
    "precise", "loyal", "expressive", "concise", "friendly", "helpful",
    "honest", "genuine", "witty", "charming", "quirky", "sassy", "playful",
    "android", "yorha",
]


def test_layer1_facts_contains_no_banned_personality_adjectives():
    lowered = LAYER1_FACTS.lower()
    for word in _BANNED_ADJECTIVES:
        assert word not in lowered, f"banned adjective {word!r} found in LAYER1_FACTS"


def test_layer1_facts_states_real_architecture():
    lowered = LAYER1_FACTS.lower()
    assert "affect" in lowered
    assert "drives" in lowered
    assert "persistent memory" in lowered
    assert "cannot know" in lowered


def test_layer1_facts_prohibits_self_denial():
    assert "do not deny" in LAYER1_FACTS.lower()


# ── CognitiveCore wiring ────────────────────────────────────────────────────────

def test_chat_routes_turns_through_core_and_moves_affect():
    """A chat turn must feed both the user message and Lyra's reply into
    core.tick(), advancing the live cognitive loop's affect away from
    its neutral starting point."""
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "hi there"
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    core = CognitiveCore()
    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=core)

    before = assistant.introspect()
    asyncio.run(assistant.chat("hello", "s1"))
    after = assistant.introspect()

    assert (after.valence, after.arousal) != (before.valence, before.arousal)


def test_default_core_is_constructed_when_none_injected():
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    assert isinstance(assistant.introspect(), AffectState)


# ── Layered system prompt ────────────────────────────────────────────────────────

class _FakeIdentityEngine:
    async def get_top_traits(self, limit=5):
        from lyra_memory.models import Trait
        return [
            Trait(
                name="curiosity", value="high", confidence=0.8,
                stability="character", evidence_count=5, updated_at=0.0,
            )
        ]


class _FakeWorkingMemory:
    def get_items(self):
        return []


class _FakeCoreMemory:
    def __init__(self) -> None:
        self.identity_engine = _FakeIdentityEngine()
        self.working_memory = _FakeWorkingMemory()


def test_get_system_includes_layer1_facts_and_promoted_traits():
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    core = CognitiveCore(memory=_FakeCoreMemory())
    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=core)

    system = asyncio.run(assistant._get_system())

    assert LAYER1_FACTS in system
    assert "curiosity" in system


def test_get_system_falls_back_to_layer1_when_memory_not_started():
    """No injected core — memory hasn't been start()ed, so identity_engine
    is None and build_context() raises. _get_system must degrade to Layer 1
    alone, not propagate the error."""
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    system = asyncio.run(assistant._get_system())

    assert system == LAYER1_FACTS


# ── Layer 3: prose hint ──────────────────────────────────────────────────────

def _core_with_affect(emotion_v: float, emotion_a: float) -> CognitiveCore:
    affect = AffectEngine.from_dict({
        "accum_rate": 1.0, "emotion_decay": 2.0, "mood_drift": 0.2,
        "emotion_v": emotion_v, "emotion_a": emotion_a,
        "mood_v": 0.0, "mood_a": 0.0,
    })
    return CognitiveCore(affect=affect)


def test_get_system_appends_prose_hint_when_affect_strongly_negative():
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    core = _core_with_affect(emotion_v=-0.6, emotion_a=0.6)
    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=core)

    system = asyncio.run(assistant._get_system())

    assert "Keep responses brief and direct. Don't soften or elaborate." in system
    assert system.startswith(LAYER1_FACTS)


def test_get_system_has_no_layer3_when_affect_neutral():
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    system = asyncio.run(assistant._get_system())

    assert system == LAYER1_FACTS


# --- confabulated tool results -------------------------------------------
# A model that ignores "on its own line and nothing else" emits the token and
# then invents the result it has not seen yet. Stored verbatim, that invention
# is replayed to the model as her own words on every later turn.


def test_chat_with_tools_discards_text_after_tool_call():
    mock_backend = MagicMock()
    confabulated = "[TOOL:see:screen]\nThe screen shows a text editor reading 'Hello Lyra'."
    mock_backend.chat.side_effect = [confabulated, "You have a Python file open."]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A terminal window.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)
    result = asyncio.run(
        assistant.chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision)
    )

    assert result == "You have a Python file open."
    mock_memory.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    stored = [c.args[2] for c in mock_memory.add.call_args_list]
    assert not any("Hello Lyra" in s for s in stored)


def test_chat_with_tools_keeps_text_before_tool_call():
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = [
        "Let me look.\n[TOOL:see:screen]\nI see a browser window.",
        "Done.",
    ]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A terminal window.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)
    asyncio.run(assistant.chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision))

    mock_memory.add.assert_any_call("s1", "assistant", "Let me look.\n[TOOL:see:screen]")
    stored = [c.args[2] for c in mock_memory.add.call_args_list]
    assert not any("browser window" in s for s in stored)


def test_stream_chat_with_tools_discards_text_after_tool_call():
    mock_backend = MagicMock()
    mock_backend.stream_chat.side_effect = [
        iter(["[TOOL:see:screen]\n", "The screen shows a text editor reading 'Hello Lyra'."]),
        iter(["You have a Python file open."]),
    ]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A terminal window.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    async def _run():
        return [
            chunk
            async for chunk in assistant.stream_chat_with_tools(
                "what's on my screen?", "s1", vision_fn=mock_vision
            )
        ]

    chunks = asyncio.run(_run())

    assert "".join(chunks) == "You have a Python file open."
    mock_memory.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    stored = [c.args[2] for c in mock_memory.add.call_args_list]
    assert not any("Hello Lyra" in s for s in stored)
