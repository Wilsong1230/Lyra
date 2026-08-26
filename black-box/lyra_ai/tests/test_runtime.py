"""Tests for lyra_core.runtime — CoreSink and Runtime lifecycle.

No real services. Pollers are noop stubs; embed is patched so model loading
never happens. Tests are sync wrappers around asyncio.run().
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from lyra_core.interface import AffectState, Observation, ObservationKind


def _obs(content: str, source: str = "test") -> Observation:
    return Observation(kind=ObservationKind.sensory, source=source, content=content)


async def _noop_poller() -> list[Observation]:
    return []


# ── CoreSink ──────────────────────────────────────────────────────────────────

def test_core_sink_calls_tick_once_with_all_observations():
    async def _run():
        from lyra_core.runtime import CoreSink
        mock_core = MagicMock()
        mock_core.tick = AsyncMock(return_value=([], AffectState()))

        observations = [
            _obs("the cat sat", source="vision"),
            _obs("ambient sound: rain", source="ears"),
            _obs("user said hello", source="chat"),
        ]

        sink = CoreSink(mock_core, dt=2.0)
        await sink(observations)

        mock_core.tick.assert_called_once_with(observations, 2.0)

    asyncio.run(_run())


def test_core_sink_uses_configured_dt():
    async def _run():
        from lyra_core.runtime import CoreSink
        mock_core = MagicMock()
        mock_core.tick = AsyncMock(return_value=([], AffectState()))

        sink = CoreSink(mock_core, dt=0.5)
        await sink([_obs("ambient sound: keyboard typing", source="ears")])

        args, _ = mock_core.tick.call_args
        assert args[1] == 0.5

    asyncio.run(_run())


def test_core_sink_default_dt_matches_default_poll_seconds():
    async def _run():
        from lyra_core.runtime import CoreSink, _DEFAULT_POLL_SECONDS
        mock_core = MagicMock()
        mock_core.tick = AsyncMock(return_value=([], AffectState()))

        sink = CoreSink(mock_core)
        await sink([_obs("wake word detected", source="wakeword")])

        args, _ = mock_core.tick.call_args
        assert args[1] == _DEFAULT_POLL_SECONDS

    asyncio.run(_run())


# ── Runtime lifecycle ─────────────────────────────────────────────────────────

# Embed is patched in lifecycle tests so model loading never happens.
_ZERO_EMBED = b"\x00" * (384 * 4)


def test_runtime_start_stop_lifecycle(tmp_path):
    async def _run():
        import lyra_memory.config as _cfg
        orig_prompt = _cfg.CORE_PROMPT
        _cfg.CORE_PROMPT = "You are Lyra."
        try:
            with patch("lyra_memory.embeddings.embed", new=AsyncMock(return_value=_ZERO_EMBED)):
                from lyra_core.runtime import Runtime

                rt = Runtime(
                    pollers=[_noop_poller],
                    poll_seconds=0.05,
                    db_path=tmp_path / "test.db",
                )
                await rt.start()

                # Both loops running after start
                assert rt._perception_loop is not None
                assert rt._perception_loop._task is not None
                assert not rt._perception_loop._task.done()
                assert rt.core.memory.dreaming_loop._task is not None
                assert not rt.core.memory.dreaming_loop._task.done()

                await asyncio.sleep(0.1)
                await rt.stop()

                # Both loops finished after stop
                assert rt._perception_loop._task.done()
                assert rt.core.memory.dreaming_loop._task.done()
        finally:
            _cfg.CORE_PROMPT = orig_prompt

    asyncio.run(_run())


def test_runtime_shutdown_order(tmp_path):
    """Perception loop must stop before core teardown — no observation
    can arrive after the DB starts closing."""
    async def _run():
        import lyra_memory.config as _cfg
        orig_prompt = _cfg.CORE_PROMPT
        _cfg.CORE_PROMPT = "You are Lyra."
        try:
            with patch("lyra_memory.embeddings.embed", new=AsyncMock(return_value=_ZERO_EMBED)):
                from lyra_core.runtime import Runtime

                rt = Runtime(
                    pollers=[_noop_poller],
                    poll_seconds=0.05,
                    db_path=tmp_path / "test.db",
                )
                await rt.start()

                events: list[str] = []
                orig_perc_stop = rt._perception_loop.stop
                orig_core_stop = rt._core.stop

                async def record_perc():
                    events.append("perception")
                    await orig_perc_stop()

                async def record_core():
                    events.append("core")
                    await orig_core_stop()

                rt._perception_loop.stop = record_perc
                rt._core.stop = record_core

                await rt.stop()

                assert events == ["perception", "core"]
        finally:
            _cfg.CORE_PROMPT = orig_prompt

    asyncio.run(_run())


# ── Affect persistence across stop/start ──────────────────────────────────────

def test_core_affect_persists_across_stop_start(tmp_path):
    """CognitiveCore.start()/stop() persist affect via the owned memory's
    StructuredState facts table — the simplest honest store, since the core
    already owns that DB connection."""
    async def _run():
        import lyra_memory.config as _cfg
        orig_prompt = _cfg.CORE_PROMPT
        _cfg.CORE_PROMPT = "You are Lyra."
        try:
            with patch("lyra_memory.embeddings.embed", new=AsyncMock(return_value=_ZERO_EMBED)):
                from lyra_core.interface import CognitiveCore
                from lyra_memory import MemorySystem

                db_path = tmp_path / "test.db"

                core1 = CognitiveCore(memory=MemorySystem(db_path=db_path))
                await core1.start()
                for _ in range(5):
                    await core1.tick([], dt=0.1)
                before = core1.introspect()
                await core1.stop()

                core2 = CognitiveCore(memory=MemorySystem(db_path=db_path))
                await core2.start()
                after = core2.introspect()
                await core2.stop()

                return before, after
        finally:
            _cfg.CORE_PROMPT = orig_prompt

    before, after = asyncio.run(_run())

    assert before.valence != pytest.approx(0.0)
    assert after.valence == pytest.approx(before.valence)
    assert after.arousal == pytest.approx(before.arousal)
