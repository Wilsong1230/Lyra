"""lyra_core.actuators — output peripherals for gate-approved intents.

Symmetric to senses.py. senses.py turns the world into Observations; this
turns approved Intents into effects. Neither is imported by interface.py —
the core still emits intent only and never performs I/O itself, exactly as
the Intent docstring requires ("Peripherals translate intent into physical
action").

SCOPE: exactly one intent kind is executed — IntentKind.speak, via the
existing lyra-voice /speak endpoint the CLI already uses. Every other kind
is left approved-and-unexecuted. Kinds are not filtered by the actuator as a
security measure; the harm gate in tick() has already run, and this only ever
receives intents that passed it.

TWO INJECTABLE SEAMS:
  utterance_fn — Intent → the words to say. Defaults to a minimal templated
    line derived from the intent's payload reason. Generating genuinely
    situated unprompted speech means an LLM call with memory and system
    prompt, which belongs to a later step; the seam is here so that swapping
    it in requires no restructuring.
  send_fn — the transport. Defaults to an HTTP POST at lyra-voice. Injectable
    so tests never need a live service.
"""
from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from lyra_core.interface import Intent, IntentKind

VOICE_URL = os.environ.get("VOICE_URL", "http://localhost:8001")

# Minimum seconds between spoken utterances. Boredom keeps proposing speech
# every tick it is above threshold, and nothing relieves it until the outcome
# path exists (next step), so without a floor here Lyra would talk at the
# poll rate. This is an actuator concern — not spamming a downstream service —
# and deliberately not a drive or selector concern.
_DEFAULT_COOLDOWN_SECONDS: float = 60.0

UtteranceFn = Callable[[Intent], str]
SendFn = Callable[[str], Awaitable[bool]]


def default_utterance(intent: Intent) -> str:
    """Placeholder wording keyed off the intent's reason.

    Deliberately plain. This is a stand-in for generated speech, and it should
    look like one rather than like authored personality.
    """
    reason = intent.payload.get("reason", "")
    if reason == "boredom":
        return "I've been sitting with nothing to work on for a while."
    if reason == "friction":
        return "Something keeps coming up that we haven't resolved."
    return "I have something I want to say."


async def send_to_voice(text: str, timeout: float = 30.0) -> bool:
    """POST to lyra-voice /speak — the same endpoint the CLI uses.

    Returns True when the utterance was accepted. Never raises: a missing
    voice service must not take down the perception loop.
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{VOICE_URL}/speak",
                json={"text": text, "sync_emotion": True},
                timeout=timeout,
            )
        return r.status_code == 200
    except Exception as exc:
        print(f"[{datetime.now().isoformat()}] [actuator] voice unavailable: {exc}")
        return False


class SpeechActuator:
    """Executes approved IntentKind.speak intents; ignores every other kind."""

    def __init__(
        self,
        utterance_fn: UtteranceFn | None = None,
        send_fn: SendFn | None = None,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._utterance_fn = utterance_fn or default_utterance
        self._send_fn = send_fn or send_to_voice
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._last_spoke_at: float | None = None

    @property
    def last_spoke_at(self) -> float | None:
        return self._last_spoke_at

    def _in_cooldown(self) -> bool:
        if self._last_spoke_at is None:
            return False
        return (self._clock() - self._last_spoke_at) < self._cooldown

    async def execute(self, intents: list[Intent]) -> list[Intent]:
        """Execute the speak intents in `intents`. Returns those actually spoken.

        The return value is the seam the outcome path will need in the next
        step: it distinguishes "selected and approved" from "actually happened."
        """
        spoken: list[Intent] = []
        for intent in intents:
            if intent.kind is not IntentKind.speak:
                continue  # every other kind: approved, deliberately unexecuted
            if self._in_cooldown():
                continue
            text = self._utterance_fn(intent)
            if not text:
                continue
            if await self._send_fn(text):
                self._last_spoke_at = self._clock()
                spoken.append(intent)
                print(f"[{datetime.now().isoformat()}] [actuator] SPOKE: {text}")
        return spoken
