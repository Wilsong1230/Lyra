from __future__ import annotations
import time
import pytest
from lyra.memory_bridge import MemoryBridge


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    from lyra_memory import config as mem_config
    monkeypatch.setattr(mem_config, "CORE_PROMPT", mem_config.CORE_PROMPT)
    b = MemoryBridge(db_path=tmp_path / "test_memory.db")
    b.start()
    yield b
    b.stop()


def test_bridge_starts_and_exposes_memory(bridge):
    assert bridge.memory is not None
    assert bridge.loop is not None
    assert bridge.loop.is_running()


def test_get_system_prompt_returns_string(bridge):
    prompt = bridge.get_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_get_system_prompt_contains_core_prompt(bridge):
    from lyra_memory import config
    prompt = bridge.get_system_prompt()
    assert config.CORE_PROMPT in prompt


def test_add_turn_does_not_raise(bridge):
    # fire-and-forget — just verify no exception is raised
    bridge.add_turn("user", "Hello Lyra, do you remember me?")
    bridge.add_turn("assistant", "I don't have enough context yet to be certain.")
    time.sleep(0.05)
    assert bridge.loop.is_running()


def test_system_prompt_enriched_after_turns(bridge):
    # Add several turns and wait for dreaming loop to potentially fire.
    # We can't guarantee a dream in unit tests (threshold is 10 items),
    # but we can verify the working memory items appear in the prompt.
    for i in range(3):
        bridge.add_turn("user", f"I really enjoy hiking in the mountains, turn {i}")
        bridge.add_turn("assistant", f"That sounds wonderful, turn {i}")
    time.sleep(0.2)  # let fire-and-forget coroutines complete
    prompt = bridge.get_system_prompt()
    # Working memory items should appear in the Current Experience section
    assert "hiking" in prompt or "Current Experience" in prompt


def test_stop_is_idempotent(bridge):
    bridge.stop()
    bridge.stop()  # second stop must not raise; fixture teardown adds a third


def test_bridge_fallback_on_no_start():
    b = MemoryBridge()
    # get_system_prompt before start returns DEFAULT_SYSTEM fallback, not an exception
    from lyra.assistant import DEFAULT_SYSTEM
    prompt = b.get_system_prompt()
    assert prompt == DEFAULT_SYSTEM
