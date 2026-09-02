"""Tests for lyra_core.actuators and the intent-execution wiring.

Covers the Step 2 contract: tick()'s return value is bound at both call
sites, and exactly one intent kind (IntentKind.speak) is executed.
"""
from __future__ import annotations

import asyncio

from lyra_core.actuators import SpeechActuator, default_utterance
from lyra_core.action_selection import ActionSelector
from lyra_core.interface import AffectState, AffectVector, Intent, IntentKind


class _RecordingSend:
    def __init__(self, ok: bool = True) -> None:
        self.sent: list[str] = []
        self._ok = ok

    async def __call__(self, text: str) -> bool:
        self.sent.append(text)
        return self._ok


def _speak(reason: str = "boredom") -> Intent:
    return Intent(kind=IntentKind.speak, payload={"reason": reason})


# ── kind filtering ────────────────────────────────────────────────────────────

def test_speak_intent_is_executed():
    send = _RecordingSend()
    act = SpeechActuator(send_fn=send)
    spoken = asyncio.run(act.execute([_speak()]))
    assert len(send.sent) == 1
    assert spoken == [_speak()] or len(spoken) == 1


def test_non_speak_kinds_are_approved_but_not_executed():
    """look / set_state / noop reach the actuator (they passed the gate) and
    are deliberately left unexecuted in this step."""
    send = _RecordingSend()
    act = SpeechActuator(send_fn=send)
    others = [
        Intent(kind=IntentKind.look, payload={"reason": "curiosity"}),
        Intent(kind=IntentKind.set_state, payload={"state": "idle"}),
        Intent(kind=IntentKind.noop, payload={}),
    ]
    spoken = asyncio.run(act.execute(others))
    assert send.sent == []
    assert spoken == []


def test_failed_send_is_not_recorded_as_spoken():
    """A down voice service must not count as an utterance — the next step
    builds the outcome path on this distinction."""
    send = _RecordingSend(ok=False)
    act = SpeechActuator(send_fn=send)
    spoken = asyncio.run(act.execute([_speak()]))
    assert send.sent == ["I've been sitting with nothing to work on for a while."]
    assert spoken == []
    assert act.last_spoke_at is None


# ── cooldown ──────────────────────────────────────────────────────────────────

def test_cooldown_suppresses_repeat_utterances():
    now = [1000.0]
    send = _RecordingSend()
    act = SpeechActuator(send_fn=send, cooldown_seconds=60.0, clock=lambda: now[0])

    asyncio.run(act.execute([_speak()]))
    now[0] += 10.0
    asyncio.run(act.execute([_speak()]))
    assert len(send.sent) == 1, "second utterance inside cooldown must be suppressed"

    now[0] += 60.0
    asyncio.run(act.execute([_speak()]))
    assert len(send.sent) == 2, "utterance after cooldown must go through"


def test_utterance_fn_is_injectable():
    send = _RecordingSend()
    act = SpeechActuator(utterance_fn=lambda i: "custom line", send_fn=send)
    asyncio.run(act.execute([_speak()]))
    assert send.sent == ["custom line"]


def test_default_utterance_differs_by_reason():
    assert default_utterance(_speak("boredom")) != default_utterance(_speak("friction"))


# ── selector: boredom escalates to speech ─────────────────────────────────────

def _neutral() -> AffectState:
    return AffectState(emotion=AffectVector(valence=0.0, arousal=0.0))


def test_boredom_below_threshold_looks_but_does_not_speak():
    sel = ActionSelector(speak_threshold=1.0)
    kinds = [i.kind for i in sel.select({"boredom": 0.5}, _neutral())]
    assert IntentKind.look in kinds
    assert IntentKind.speak not in kinds


def test_boredom_at_threshold_also_speaks():
    sel = ActionSelector(speak_threshold=1.0)
    intents = sel.select({"boredom": 1.0}, _neutral())
    kinds = [i.kind for i in intents]
    assert IntentKind.look in kinds
    assert IntentKind.speak in kinds
    speak = next(i for i in intents if i.kind is IntentKind.speak)
    assert speak.payload["reason"] == "boredom"


def test_frustration_override_still_suppresses_both():
    """Affect overriding the drive must suppress the speech escalation too,
    not just the look."""
    sel = ActionSelector(affect_weight=1.0, speak_threshold=1.0)
    frustrated = AffectState(emotion=AffectVector(valence=-2.0, arousal=0.0))
    kinds = [i.kind for i in sel.select({"boredom": 1.5}, frustrated)]
    assert kinds == [IntentKind.noop]
