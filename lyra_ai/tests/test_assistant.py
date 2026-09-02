"""Tests for lyra.assistant — the client, and the Layer 1 prompt text it hosts."""
from __future__ import annotations

import asyncio
import json

import pytest

from lyra.assistant import (
    DEFAULT_SYSTEM,
    LAYER1_FACTS,
    DaemonError,
    DaemonGone,
    DaemonUnavailable,
    LyraClient,
)
from lyra_core.transport import encode_frame, error_frame, reply_frame


# ── the client constructs no cognition ──────────────────────────────────────

def test_client_module_imports_no_core_or_memory():
    import lyra.assistant

    ns = vars(lyra.assistant)
    for banned in ("CognitiveCore", "MemorySystem", "WorkingMemory", "DreamingLoop", "ConversationMemory"):
        assert banned not in ns, f"{banned} must not be reachable from the client module"


# ── a fake daemon ────────────────────────────────────────────────────────────

class _FakeDaemon:
    """Speaks the wire protocol; scripted per received turn."""

    def __init__(self, script) -> None:
        self._script = script  # callable(text, session) -> frame dict | None (None = hang up)
        self.received: list[dict] = []
        self._server = None
        self._writers: list = []

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        # A dying daemon takes its sockets with it; on 3.11 Server.close()
        # alone leaves them open, so hang up on every client explicitly.
        self._server.close()
        for w in self._writers:
            w.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        self._writers.append(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                frame = json.loads(line)
                self.received.append(frame)
                out = self._script(frame.get("text"), frame.get("session"))
                if out is None:
                    break
                writer.write(encode_frame(out))
                await writer.drain()
        finally:
            writer.close()


def test_connect_fails_clearly_when_nothing_listens():
    async def _run():
        with pytest.raises(DaemonUnavailable, match="no daemon listening on 127.0.0.1:1"):
            await LyraClient.connect("127.0.0.1", 1, timeout=1.0)

    asyncio.run(_run())


def test_chat_sends_a_versioned_turn_and_returns_the_reply_text():
    async def _run():
        async with _FakeDaemon(lambda t, s: reply_frame(f"you said {t}")) as daemon:
            client = await LyraClient.connect("127.0.0.1", daemon.port)
            reply = await client.chat("hello", "sess-1")
            await client.close()
            return reply, daemon.received

    reply, received = asyncio.run(_run())
    assert reply == "you said hello"
    assert received == [{"v": 1, "type": "turn", "text": "hello", "session": "sess-1"}]


def test_error_frame_raises_daemon_error_and_connection_stays_usable():
    def script(text, session):
        return error_frame("backend down") if text == "bad" else reply_frame("fine")

    async def _run():
        async with _FakeDaemon(script) as daemon:
            client = await LyraClient.connect("127.0.0.1", daemon.port)
            with pytest.raises(DaemonError, match="backend down"):
                await client.chat("bad", "s")
            ok = await client.chat("good", "s")
            await client.close()
            return ok

    assert asyncio.run(_run()) == "fine"


def test_daemon_hanging_up_mid_turn_raises_daemon_gone():
    async def _run():
        async with _FakeDaemon(lambda t, s: None) as daemon:
            client = await LyraClient.connect("127.0.0.1", daemon.port)
            with pytest.raises(DaemonGone):
                await asyncio.wait_for(client.chat("hello", "s"), 5.0)
            assert client.closed.is_set()
            await client.close()

    asyncio.run(_run())


def test_closed_event_fires_while_idle_when_the_daemon_goes_away():
    async def _run():
        async with _FakeDaemon(lambda t, s: reply_frame("ok")) as daemon:
            client = await LyraClient.connect("127.0.0.1", daemon.port)
            await client.chat("hello", "s")
            assert not client.closed.is_set()
        # server closed: the idle client must notice on its own
        await asyncio.wait_for(client.closed.wait(), 5.0)
        with pytest.raises(DaemonGone):
            await client.chat("again", "s")
        await client.close()

    asyncio.run(_run())


def test_close_sends_nothing_to_the_daemon():
    async def _run():
        async with _FakeDaemon(lambda t, s: reply_frame("ok")) as daemon:
            client = await LyraClient.connect("127.0.0.1", daemon.port)
            await client.close()
            await asyncio.sleep(0.05)
            return daemon.received

    assert asyncio.run(_run()) == []


# ── Persona strip: Layer 1 facts-only ──────────────────────────────────────────

_BANNED_ADJECTIVES = [
    "precise", "loyal", "expressive", "concise", "friendly", "helpful",
    "honest", "genuine", "witty", "charming", "quirky", "sassy", "playful",
    "android", "yorha",
]


def test_default_system_includes_tool_instructions():
    assert "[TOOL:see:screen]" in DEFAULT_SYSTEM
    assert "[TOOL:see:webcam]" in DEFAULT_SYSTEM


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
