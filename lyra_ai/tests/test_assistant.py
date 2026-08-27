from __future__ import annotations
import asyncio
import pytest
from unittest.mock import MagicMock, call
from lyra.assistant import Assistant, DEFAULT_SYSTEM, LAYER1_FACTS
from lyra_core.affect import AffectEngine
from lyra_core.interface import AffectState, CognitiveCore

from tests.conftest import NullMemory


@pytest.fixture
async def live_core(tmp_path):
    """A core backed by a REAL, started, empty MemorySystem.

    These tests are about tool parsing and streaming rather than about the
    store, but they still have to run against one: an unstarted MemorySystem
    now raises rather than discarding turns silently, and stubbing assembly out
    with a mock would stop testing the thing the failure policy protects.
    """
    from lyra_memory import MemorySystem

    memory = MemorySystem(db_path=tmp_path / "assistant.db")
    await memory.start(run_dream_loop=False)
    try:
        yield CognitiveCore(memory=memory)
    finally:
        await memory.stop()


async def test_stream_chat_yields_chunks(live_core):
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Hello", " world"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat("hi", "session-1")]

    chunks = await _run()
    assert chunks == ["Hello", " world"]


async def test_stream_chat_saves_full_response_to_memory(live_core):
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Hello", " world"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    async def _run():
        async for _ in assistant.stream_chat("hi", "session-1"):
            pass

    await _run()

    mock_memory.add.assert_any_call("session-1", "user", "hi")
    mock_memory.add.assert_any_call("session-1", "assistant", "Hello world")


async def test_stream_chat_passes_history_and_system_to_backend(live_core):
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Ok"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = [{"role": "user", "content": "prior"}]

    assistant = Assistant(backend=mock_backend, memory=mock_memory, system="Be concise.", core=live_core)

    async def _run():
        async for _ in assistant.stream_chat("hi", "s1"):
            pass

    await _run()

    # The assembled prompt now ends with the live conversation: position is
    # load-bearing, and `recent` is pinned last.
    (messages,), kwargs = mock_backend.stream_chat.call_args
    assert messages == [{"role": "user", "content": "prior"}]
    assert kwargs["system"].startswith("Be concise.")
    assert kwargs["system"].rstrip().endswith("[user]: hi")


def test_default_system_includes_tool_instructions():
    assert "[TOOL:see:screen]" in DEFAULT_SYSTEM
    assert "[TOOL:see:webcam]" in DEFAULT_SYSTEM


async def test_chat_with_tools_calls_vision_and_reprompts(live_core):
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = ["[TOOL:see:screen]", "You have a Python file open."]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A code editor with Python open.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)
    result = await assistant.chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision)

    assert result == "You have a Python file open."
    mock_vision.assert_called_once_with("screen")
    mock_memory.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    mock_memory.add.assert_any_call("s1", "user", "[Vision result: A code editor with Python open.]")


async def test_chat_with_tools_no_tool_call_returns_directly(live_core):
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "The sky is blue."
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)
    result = await assistant.chat_with_tools("what color is the sky?", "s1", vision_fn=mock_vision)

    assert result == "The sky is blue."
    mock_vision.assert_not_called()
    assert mock_backend.chat.call_count == 1


async def test_chat_with_tools_caps_at_three_iterations(live_core):
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "[TOOL:see:screen]"
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="still a screen")

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)
    result = await assistant.chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision)

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


async def test_chat_with_tools_raises_if_no_vision_fn_and_tool_called(live_core):
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "[TOOL:see:screen]"
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)
    with pytest.raises(ValueError, match="vision_fn is required"):
        await assistant.chat_with_tools("what's on my screen?", "s1")


async def test_chat_with_tools_webcam_source(live_core):
    mock_backend = MagicMock()
    mock_backend.chat.side_effect = ["[TOOL:see:webcam]", "I see you."]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A person at a desk.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)
    result = await assistant.chat_with_tools("what do you see?", "s1", vision_fn=mock_vision)

    assert result == "I see you."
    mock_vision.assert_called_once_with("webcam")


async def test_stream_chat_with_tools_no_tool_yields_chunks(live_core):
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["Hello", " world"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat_with_tools("hi", "s1", vision_fn=mock_vision)]

    chunks = await _run()
    assert chunks == ["Hello", " world"]
    mock_vision.assert_not_called()


async def test_stream_chat_with_tools_resolves_tool_then_streams_final(live_core):
    mock_backend = MagicMock()
    mock_backend.stream_chat.side_effect = [
        iter(["[TOOL:see:screen]"]),
        iter(["You have", " a browser open."]),
    ]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="A browser showing Google.")

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision)]

    chunks = await _run()
    assert chunks == ["You have", " a browser open."]
    mock_vision.assert_called_once_with("screen")
    mock_memory.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    mock_memory.add.assert_any_call("s1", "user", "[Vision result: A browser showing Google.]")


async def test_stream_chat_with_tools_caps_at_three_iterations(live_core):
    mock_backend = MagicMock()
    mock_backend.stream_chat.side_effect = [
        iter(["[TOOL:see:screen]"]),
        iter(["[TOOL:see:screen]"]),
        iter(["[TOOL:see:screen]"]),
    ]
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []
    mock_vision = MagicMock(return_value="still a screen")

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    async def _run():
        return [chunk async for chunk in assistant.stream_chat_with_tools("what's on my screen?", "s1", vision_fn=mock_vision)]

    chunks = await _run()
    assert mock_backend.stream_chat.call_count == 3
    assert mock_vision.call_count == 3
    assert len(chunks) == 1
    assert "unable to determine" in chunks[0]
    assistant_calls = [c for c in mock_memory.add.call_args_list if c == call("s1", "assistant", "[TOOL:see:screen]")]
    assert len(assistant_calls) == 3


async def test_stream_chat_with_tools_raises_if_no_vision_fn_and_tool_called(live_core):
    mock_backend = MagicMock()
    mock_backend.stream_chat.return_value = iter(["[TOOL:see:screen]"])
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    async def _run():
        async for _ in assistant.stream_chat_with_tools("what's on my screen?", "s1"):
            pass

    with pytest.raises(ValueError, match="vision_fn is required"):
        await _run()


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

async def test_chat_routes_turns_through_core_and_moves_affect(live_core):
    """A chat turn must feed both the user message and Lyra's reply into
    core.tick(), advancing the live cognitive loop's affect away from
    its neutral starting point."""
    mock_backend = MagicMock()
    mock_backend.chat.return_value = "hi there"
    mock_memory = MagicMock()
    mock_memory.get_history.return_value = []

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    before = assistant.introspect()
    await assistant.chat("hello", "s1")
    after = assistant.introspect()

    assert (after.valence, after.arousal) != (before.valence, before.arousal)


def test_default_core_is_constructed_when_none_injected():
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory)

    assert isinstance(assistant.introspect(), AffectState)


# ── Layered system prompt ────────────────────────────────────────────────────────

class _FakeIdentityEngine:
    def _traits(self):
        from lyra_memory.models import Trait
        return [
            Trait(
                name="curiosity", value="high", confidence=0.8,
                stability="character", evidence_count=5, updated_at=0.0,
            )
        ]

    async def get_top_traits(self, limit=5):
        return self._traits()

    async def get_context_traits(self, limit=10):
        # The retrieval block filters on confidence >= 0.3; 0.8 clears it.
        return self._traits()


async def test_get_system_includes_layer1_facts_and_promoted_traits(live_core):
    """A trait above the confidence floor reaches the assembled prompt."""
    live_core.memory.identity_engine = _FakeIdentityEngine()

    assistant = Assistant(backend=MagicMock(), memory=MagicMock(), core=live_core)
    system = await assistant._get_system()

    assert LAYER1_FACTS in system
    assert "curiosity" in system


async def test_get_system_is_layer1_alone_when_the_store_is_empty(live_core):
    """An empty store contributes no blocks, so the prompt is Layer 1 alone.

    Distinct from the broken-store case below, which raises. That the two are
    distinguishable at all is the point of the failure policy.
    """
    assistant = Assistant(backend=MagicMock(), memory=MagicMock(), core=live_core)
    assert await assistant._get_system() == LAYER1_FACTS


def test_get_system_does_not_swallow_a_broken_store():
    """Spec "Failure policy": build_context is named in it.

    A store that raises must not degrade quietly to Layer 1 — that is the
    fail-open which made a broken store and an empty store indistinguishable
    from a transcript.
    """
    assistant = Assistant(backend=MagicMock(), memory=MagicMock())

    with pytest.raises(RuntimeError, match="never started"):
        asyncio.run(assistant._get_system())


# ── Layer 3: prose hint ──────────────────────────────────────────────────────

def _core_with_affect(emotion_v: float, emotion_a: float, memory=None) -> CognitiveCore:
    affect = AffectEngine.from_dict({
        "accum_rate": 1.0, "emotion_decay": 2.0, "mood_drift": 0.2,
        "emotion_v": emotion_v, "emotion_a": emotion_a,
        "mood_v": 0.0, "mood_a": 0.0,
    })
    return CognitiveCore(affect=affect, memory=memory)


async def test_get_system_appends_prose_hint_when_affect_strongly_negative(live_core):
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    core = _core_with_affect(emotion_v=-0.6, emotion_a=0.6, memory=live_core.memory)
    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=core)

    system = await assistant._get_system()

    assert "Keep responses brief and direct. Don't soften or elaborate." in system
    assert system.startswith(LAYER1_FACTS)


async def test_get_system_has_no_layer3_when_affect_neutral(live_core):
    mock_backend = MagicMock()
    mock_memory = MagicMock()

    assistant = Assistant(backend=mock_backend, memory=mock_memory, core=live_core)

    system = await assistant._get_system()

    assert system == LAYER1_FACTS
