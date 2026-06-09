"""Tests for lyra_core.perception — loop machinery only.

No real services, no memory, no core. Fake pollers + a recording sink drive
everything. Tests are sync wrappers around asyncio.run(), matching the pattern
used throughout this suite.
"""
from __future__ import annotations

import asyncio
import pytest

from lyra_core.interface import Observation, ObservationKind
from lyra_core.perception import PerceptionLoop


# ── helpers ───────────────────────────────────────────────────────────────────

def _obs(content: str, source: str = "test") -> Observation:
    return Observation(kind=ObservationKind.sensory, source=source, content=content)


class _RecordingSink:
    """Collects every batch the loop delivers; never raises."""

    def __init__(self) -> None:
        self.batches: list[list[Observation]] = []

    async def __call__(self, obs: list[Observation]) -> None:
        self.batches.append(list(obs))

    def flat(self) -> list[Observation]:
        return [o for batch in self.batches for o in batch]


# ── delivery ──────────────────────────────────────────────────────────────────

def test_poll_delivers_observations_to_sink():
    async def _run():
        sink = _RecordingSink()
        obs_a = _obs("screen shows code")
        obs_b = _obs("user typed hello")

        async def poller():
            return [obs_a, obs_b]

        lp = PerceptionLoop(sink=sink, pollers=[poller])
        await lp._poll_once()

        assert len(sink.batches) == 1
        assert obs_a in sink.batches[0]
        assert obs_b in sink.batches[0]

    asyncio.run(_run())


def test_multiple_pollers_both_contribute_to_one_sink_call():
    async def _run():
        sink = _RecordingSink()
        obs_a = _obs("vision observation", source="vision")
        obs_b = _obs("audio observation", source="audio")

        async def poller_vision():
            return [obs_a]

        async def poller_audio():
            return [obs_b]

        lp = PerceptionLoop(sink=sink, pollers=[poller_vision, poller_audio])
        await lp._poll_once()

        assert len(sink.batches) == 1
        assert obs_a in sink.batches[0]
        assert obs_b in sink.batches[0]

    asyncio.run(_run())


# ── fault isolation ───────────────────────────────────────────────────────────

def test_failing_poller_skipped_healthy_poller_still_delivers():
    async def _run():
        sink = _RecordingSink()
        obs = _obs("from healthy poller")

        async def bad_poller():
            raise RuntimeError("poller exploded")

        async def good_poller():
            return [obs]

        lp = PerceptionLoop(sink=sink, pollers=[bad_poller, good_poller])
        await lp._poll_once()

        assert obs in sink.flat()

    asyncio.run(_run())


def test_failing_poller_does_not_prevent_other_pollers():
    """Order must not matter: healthy poller before bad one must still deliver."""
    async def _run():
        sink = _RecordingSink()
        obs = _obs("good obs")

        async def good_poller():
            return [obs]

        async def bad_poller():
            raise ValueError("boom")

        lp = PerceptionLoop(sink=sink, pollers=[good_poller, bad_poller])
        await lp._poll_once()

        assert obs in sink.flat()

    asyncio.run(_run())


# ── empty cycle ───────────────────────────────────────────────────────────────

def test_empty_cycle_does_not_call_sink():
    async def _run():
        sink = _RecordingSink()

        async def empty_poller():
            return []

        lp = PerceptionLoop(sink=sink, pollers=[empty_poller])
        await lp._poll_once()

        assert sink.batches == []

    asyncio.run(_run())


def test_no_pollers_does_not_call_sink():
    async def _run():
        sink = _RecordingSink()
        lp = PerceptionLoop(sink=sink, pollers=[])
        await lp._poll_once()
        assert sink.batches == []

    asyncio.run(_run())


# ── dedup ─────────────────────────────────────────────────────────────────────

def test_duplicate_obs_on_consecutive_polls_delivered_once():
    async def _run():
        sink = _RecordingSink()
        obs = _obs("same content")

        async def poller():
            return [obs]

        lp = PerceptionLoop(sink=sink, pollers=[poller])
        await lp._poll_once()
        await lp._poll_once()

        assert len(sink.batches) == 1
        assert obs in sink.batches[0]

    asyncio.run(_run())


def test_different_content_second_poll_passes():
    async def _run():
        sink = _RecordingSink()
        obs_a = _obs("first content")
        obs_b = _obs("second content")

        turns = iter([[obs_a], [obs_b]])

        async def poller():
            return next(turns)

        lp = PerceptionLoop(sink=sink, pollers=[poller])
        await lp._poll_once()  # obs_a passes
        await lp._poll_once()  # obs_b is new — passes

        assert obs_a in sink.flat()
        assert obs_b in sink.flat()

    asyncio.run(_run())


def test_same_content_different_source_both_pass():
    """Dedup key includes source, so same content from two sources are distinct."""
    async def _run():
        sink = _RecordingSink()
        obs_a = _obs("hello", source="vision")
        obs_b = _obs("hello", source="audio")

        async def poller():
            return [obs_a, obs_b]

        lp = PerceptionLoop(sink=sink, pollers=[poller])
        await lp._poll_once()

        assert obs_a in sink.flat()
        assert obs_b in sink.flat()

    asyncio.run(_run())


def test_dedup_window_bounds_memory():
    """Once the dedup window is full, the oldest key is evicted and that
    observation can re-deliver on a later poll."""
    async def _run():
        sink = _RecordingSink()
        obs_a = _obs("alpha")
        obs_b = _obs("beta")

        turns = [[obs_a], [obs_b], [obs_a]]
        idx = [0]

        async def cycling_poller():
            result = turns[idx[0]]
            idx[0] += 1
            return result

        # window=1: after obs_b is seen, obs_a's key is evicted
        lp = PerceptionLoop(sink=sink, pollers=[cycling_poller], dedup_window=1)
        await lp._poll_once()  # obs_a passes; window = [key_a]
        await lp._poll_once()  # obs_b passes; window = [key_b] (key_a evicted)
        await lp._poll_once()  # obs_a passes again; key_a not in window

        delivered = sink.flat()
        assert delivered[0] == obs_a
        assert delivered[1] == obs_b
        assert delivered[2] == obs_a  # re-delivered after eviction

    asyncio.run(_run())


# ── lifecycle ─────────────────────────────────────────────────────────────────

def test_start_stop_lifecycle():
    async def _run():
        sink = _RecordingSink()

        async def poller():
            return []

        lp = PerceptionLoop(sink=sink, pollers=[poller], poll_seconds=0.05)
        assert lp._task is None
        lp.start()
        assert lp._task is not None
        assert not lp._task.done()
        await lp.stop()
        assert lp._task.done()

    asyncio.run(_run())


def test_start_twice_is_noop():
    async def _run():
        sink = _RecordingSink()

        async def poller():
            return []

        lp = PerceptionLoop(sink=sink, pollers=[poller], poll_seconds=1.0)
        lp.start()
        task1 = lp._task
        lp.start()  # idempotent — must not create a new task
        assert lp._task is task1
        await lp.stop()

    asyncio.run(_run())


def test_loop_delivers_via_timer():
    """The timed loop fires poll_once, which delivers the first detection.
    Subsequent fires dedup — so sink is called exactly once."""
    async def _run():
        sink = _RecordingSink()
        obs = _obs("timed observation")

        async def poller():
            return [obs]

        lp = PerceptionLoop(sink=sink, pollers=[poller], poll_seconds=0.05)
        lp.start()
        await asyncio.sleep(0.2)  # room for ~3 polls
        await lp.stop()

        assert len(sink.batches) == 1  # first fires; rest deduped
        assert obs in sink.batches[0]

    asyncio.run(_run())
