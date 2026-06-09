"""Tests for lyra_core.runtime — MemorySink and Runtime lifecycle.

No real services. Pollers are noop stubs; embed is patched so model loading
never happens. Tests are sync wrappers around asyncio.run().
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from lyra_core.interface import Observation, ObservationKind


def _obs(content: str, source: str = "test") -> Observation:
    return Observation(kind=ObservationKind.sensory, source=source, content=content)


async def _noop_poller() -> list[Observation]:
    return []


# ── MemorySink ────────────────────────────────────────────────────────────────

def test_memory_sink_calls_add_observation_for_each_obs():
    async def _run():
        from lyra_core.runtime import MemorySink
        mock_mem = MagicMock()
        mock_mem.add_observation = AsyncMock()

        sink = MemorySink(mock_mem)
        await sink([
            _obs("the cat sat", source="vision"),
            _obs("ambient sound: rain", source="ears"),
            _obs("user said hello", source="chat"),
        ])

        assert mock_mem.add_observation.call_count == 3

    asyncio.run(_run())


def test_memory_sink_passes_content_and_source():
    async def _run():
        from lyra_core.runtime import MemorySink
        mock_mem = MagicMock()
        mock_mem.add_observation = AsyncMock()

        sink = MemorySink(mock_mem)
        await sink([_obs("ambient sound: keyboard typing", source="ears")])

        mock_mem.add_observation.assert_called_once_with(
            "ambient sound: keyboard typing", source="ears"
        )

    asyncio.run(_run())


def test_memory_sink_passes_source_for_low_salience_weighting():
    """'ears' and 'wakeword' sources must reach memory.add_observation so
    WorkingMemory applies the low-salience base importance."""
    async def _run():
        from lyra_core.runtime import MemorySink
        mock_mem = MagicMock()
        mock_mem.add_observation = AsyncMock()

        sink = MemorySink(mock_mem)
        await sink([
            _obs("ambient sound: rain", source="ears"),
            _obs("wake word detected", source="wakeword"),
        ])

        calls = mock_mem.add_observation.call_args_list
        assert call("ambient sound: rain", source="ears") in calls
        assert call("wake word detected", source="wakeword") in calls

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
                assert rt._memory.dreaming_loop._task is not None
                assert not rt._memory.dreaming_loop._task.done()

                await asyncio.sleep(0.1)
                await rt.stop()

                # Both loops finished after stop
                assert rt._perception_loop._task.done()
                assert rt._memory.dreaming_loop._task.done()
        finally:
            _cfg.CORE_PROMPT = orig_prompt

    asyncio.run(_run())


def test_runtime_shutdown_order(tmp_path):
    """Perception loop must stop before memory teardown — no observation
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
                orig_mem_stop = rt._memory.stop

                async def record_perc():
                    events.append("perception")
                    await orig_perc_stop()

                async def record_mem():
                    events.append("memory")
                    await orig_mem_stop()

                rt._perception_loop.stop = record_perc
                rt._memory.stop = record_mem

                await rt.stop()

                assert events == ["perception", "memory"]
        finally:
            _cfg.CORE_PROMPT = orig_prompt

    asyncio.run(_run())
