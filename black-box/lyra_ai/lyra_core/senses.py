"""lyra_core.senses — passive sense pollers for PerceptionLoop.

poll_ambient() and poll_wakeword() are Poller callables (async, no args,
return list[Observation]) that translate lyra-listen REST responses into
typed Observations.

Two load-bearing seams:

  (a) DEDUP STABILITY: content is derived ONLY from the stable label, never
      from per-poll volatile data (confidence, timestamp). The same real-world
      detection arriving on consecutive polls produces identical content, so
      the loop's (source, content) dedup key fires correctly.

  (b) LOW SALIENCE: the sink (Phase 1.3) must call
          wm.add_observation(obs.content, source=obs.source)
      WorkingMemory applies a lower base importance for source in
      {"ears", "wakeword"} so background audio does not dominate the
      dreaming queue. No changes to this file are needed for that to work —
      the source field on Observation carries the signal.
"""
from __future__ import annotations

import os

import httpx

from lyra_core.interface import Observation, ObservationKind

AMBIENT_URL = os.environ.get("AMBIENT_URL", "http://localhost:8004")
WAKEWORD_URL = os.environ.get("WAKEWORD_URL", "http://localhost:8005")

# Edge-triggered wakeword counter: emit only when detections_today increases.
_last_wakeword_count: int = 0


async def _get(url: str, timeout: float = 1.0) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(url, timeout=timeout)


async def poll_ambient() -> list[Observation]:
    """Poll /ambient for the latest recognized sound label.

    Returns one Observation when a sound is detected, [] otherwise.
    Connection errors and non-200 responses are swallowed quietly.
    """
    try:
        r = await _get(f"{AMBIENT_URL}/ambient")
        if r.status_code != 200:
            return []
        sound = r.json().get("sound")
        if not sound:
            return []
        return [Observation(
            kind=ObservationKind.sensory,
            source="ears",
            # Only the stable label — not confidence or timestamp (seam a).
            content=f"ambient sound: {sound}",
        )]
    except Exception:
        return []


async def poll_wakeword() -> list[Observation]:
    """Poll /wakeword/status and emit once per new detection.

    Tracks detections_today across calls; emits only when the counter
    increases. Returns [] on unchanged count or service error.
    """
    global _last_wakeword_count
    try:
        r = await _get(f"{WAKEWORD_URL}/wakeword/status")
        if r.status_code != 200:
            return []
        count = r.json().get("detections_today", 0)
    except Exception:
        return []

    if count > _last_wakeword_count:
        _last_wakeword_count = count
        return [Observation(
            kind=ObservationKind.sensory,
            source="wakeword",
            content="wake word detected",
        )]
    return []
