"""lyra_core.interface — Phase 0 contract.

Defines the three port types (Observation, Intent, AffectState) and the
CognitiveCore stub.  The reserved seams documented here are load-bearing:
changing field names or types later requires touching every peripheral.

Reserved seams present in Phase 0 (inert until noted phase):
  ObservationKind.external_affect   — empathy channel (Phase ?)
  Observation.predicted / .actual   — competence drive (Phase 3)
  Observation.affect_hint           — Wilson's sensed valence/arousal (Phase ?)
  AffectVector.control              — third affect axis / dominance (Phase ?)
  AffectState.mood                  — medium-term affect offset (Phase ?)
  AffectState.temperament           — trait-level time constants (Phase ?)
  IntentKind.research               — deliberate information-seeking (Phase ?)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


# ── Observation kinds ─────────────────────────────────────────────────────────

class ObservationKind(str, Enum):
    sensory          = "sensory"           # perception of the world
    action_outcome   = "action_outcome"    # result of an emitted intent
    external_affect  = "external_affect"   # RESERVED — empathy seam (Wilson's state)


# ── Intent kinds ──────────────────────────────────────────────────────────────

class IntentKind(str, Enum):
    speak      = "speak"
    look       = "look"
    set_state  = "set_state"
    noop       = "noop"
    research   = "research"   # RESERVED — deliberate information-seeking


# ── Port types ────────────────────────────────────────────────────────────────

@dataclass
class Observation:
    """Uniform event shape for everything arriving at the core (IN port).

    Reserved fields are accepted and stored but unused until their phase:
      predicted / actual  — Phase 3 competence drive
      affect_hint         — empathy seam; only meaningful on external_affect obs
    """
    kind:    ObservationKind
    source:  str
    content: str
    ts:      float = field(default_factory=time.time)

    # Reserved: prediction-outcome pairing for the competence drive (Phase 3).
    # An action_outcome observation may carry what was expected vs what happened.
    # NOTE: str | None is Phase 0 convenience only. The eventual competence-drive
    # shape is expected to be STRUCTURED (a value/confidence or distribution that
    # can be diffed into a numeric prediction-error magnitude). String is a
    # placeholder — not a decided contract.
    predicted: str | None = None
    actual:    str | None = None

    # Reserved: sensed valence/arousal of Wilson (external_affect only).
    # Shape TBD; stored as a free dict until the empathy channel is specified.
    affect_hint: dict | None = None


@dataclass
class Intent:
    """What the core wants to do (OUT port).

    The core emits intent only — never waveforms, hex colors, or raw audio.
    Peripherals translate intent into physical action.
    """
    kind:    IntentKind
    payload: dict
    ts:      float = field(default_factory=time.time)


@dataclass
class AffectVector:
    """A point in affect space: valence × arousal × control.

    control is the third axis (dominance / felt agency) — reserved until Phase ?.
    """
    valence: float       = 0.0
    arousal: float       = 0.0
    control: float | None = None   # RESERVED third axis


@dataclass
class AffectState:
    """How the core is feeling right now (OUT port).

    Three-timescale structure:
      emotion      — fast, current-moment vector  (Phase 0+, always set)
      mood         — medium-term offset            (RESERVED, None until Phase ?)
      temperament  — trait-level time constants    (RESERVED, None until Phase ?)

    The top-level valence / arousal / control properties delegate to emotion
    so that Phase 1+ can blend timescales without renaming the public surface.
    """
    emotion:      AffectVector        = field(default_factory=AffectVector)
    mood:         AffectVector | None = None   # RESERVED
    temperament:  AffectVector | None = None   # RESERVED

    @property
    def valence(self) -> float:
        return self.emotion.valence

    @property
    def arousal(self) -> float:
        return self.emotion.arousal

    @property
    def control(self) -> float | None:
        return self.emotion.control


# ── Core ──────────────────────────────────────────────────────────────────────

class CognitiveCore:
    """Phase 0 stub.  Contract only — no behavior yet.

    tick() is the single entry point.  Later phases will route observations
    through drives, update affect, and produce intents here.
    """

    def tick(self, observations: list[Observation]) -> tuple[list[Intent], AffectState]:
        """Ingest observations, return (intents, affect).

        Phase 0: ignores all input, returns empty intent list and neutral affect.
        """
        return [], AffectState()

    def introspect(self) -> AffectState:
        """Read current affect WITHOUT advancing the core (read-only port).

        Phase 0 stub: returns neutral AffectState().

        This is deliberately separate from tick() so that observing the
        box never mutates it. Later phases:
          - returns the core's actually-held current AffectState
          - will likely also expose recent affect history (metacognitive
            seam) — reserve that as a future addition, do NOT add a history
            param now.
        introspect() must NEVER mutate state or advance a tick. It is the
        port the telemetry/dashboard and Lyra's own self-reading use.
        """
        return AffectState()
