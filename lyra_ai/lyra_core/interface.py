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

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
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

# Recurrence id used to route action_outcome failures into RelationalDrive —
# "the recurring problem" framing for repeated action failures (Phase 3.6).
_FAILURE_PROBLEM_ID = "action_outcome_failure"

# CP-B: obs.source names WHO/WHAT produced an observation (conversation / lyra
# / vision / ...), which is a different axis from Store's `speaker` vocabulary
# (wilson | lyra | system) and `source` channel vocabulary (cli | wakeword |
# ambient | vision | sandbox_read) — see DECISIONS.md (CP-B) for why this
# mapping is what it is. Everything that isn't her own turn and isn't a vision
# observation is attributed to the daemon's one channel today: the CLI.
_ATOM_SPEAKER_BY_OBS_SOURCE: dict[str, str] = {"conversation": "wilson", "lyra": "lyra"}
_ATOM_SOURCE_BY_OBS_SOURCE: dict[str, str] = {"vision": "vision"}
_DEFAULT_ATOM_SOURCE = "cli"

# CP-B: affect_state has no home in Store's schema (it is not a semantic fact
# about the world, and OUT OF SCOPE forbids adding a table for it) — persisted
# as a `facts` row instead, under a subject no real query will ever type. See
# DECISIONS.md (CP-B, "affect_state has no table of its own").
_AFFECT_STATE_FACT_SUBJECT = "_lyra_internal_affect_state"


class CognitiveCore:
    """The live cognitive loop.

    tick() is the single entry point: ingest observations into memory and the
    drives, advance affect, select intents (biased by promoted traits), and
    gate them. introspect() reads the live affect without advancing anything.

    Every component is constructed with real defaults but is injectable — the
    test harness can build a fully-faked core by passing fakes for any of
    affect / competence / boredom / relational / selector / consolidator /
    memory / gate.
    """

    def __init__(
        self,
        gate=None,
        affect=None,
        competence=None,
        boredom=None,
        relational=None,
        selector=None,
        consolidator=None,
        memory=None,
    ) -> None:
        from lyra_core.gate import HarmGate
        from lyra_core.affect import AffectEngine
        from lyra_core.drives import BoredomDrive, CompetenceTracker, RelationalDrive
        from lyra_core.action_selection import ActionSelector
        from lyra_core.development import OutcomeConsolidator

        self._gate = gate if gate is not None else HarmGate()
        self._affect = affect if affect is not None else AffectEngine()
        self._competence = competence if competence is not None else CompetenceTracker()
        self._boredom = boredom if boredom is not None else BoredomDrive(self._competence)
        self._relational = relational if relational is not None else RelationalDrive()
        self._selector = selector if selector is not None else ActionSelector()
        # CP-B: no default memory. The daemon always injects a Store (async
        # open, not constructible here — CognitiveCore.__init__ is sync); a
        # core built with no memory injected simply has none, and any ingest
        # that reaches it fails loudly rather than reaching for a store this
        # process never opened. See DECISIONS.md (CP-B, item 1).
        self._memory = memory

        self._consolidator_injected = consolidator is not None
        self._consolidator = consolidator if consolidator is not None else OutcomeConsolidator(
            pool=getattr(self._memory, "candidate_pool", None),
            working_memory=getattr(self._memory, "working_memory", None),
        )

    @property
    def memory(self):
        """The memory (a Store, in the daemon; whatever was injected in tests)."""
        return self._memory

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the owned memory system (if it has a start()) and restore
        persisted affect, if any.

        CP-B: Store is opened by the caller (Runtime.start — Store.open() is
        async and this constructor is sync, so it cannot happen here) and has
        no start() of its own; `getattr` keeps this call working for anything
        that still has the old MemorySystem-style lifecycle (fakes, tests)
        without requiring Store to grow a method it doesn't need.
        """
        start_fn = getattr(self._memory, "start", None)
        if start_fn is not None:
            await start_fn()

        structured_state = getattr(self._memory, "structured_state", None)
        if structured_state is not None:
            saved = await structured_state.get_fact("affect_state")
            if saved is not None:
                from lyra_core.affect import AffectEngine
                self._affect = AffectEngine.from_dict(saved)
        else:
            saved = await self._read_affect_fact()
            if saved is not None:
                from lyra_core.affect import AffectEngine
                self._affect = AffectEngine.from_dict(saved)

        if not self._consolidator_injected:
            from lyra_core.development import OutcomeConsolidator
            self._consolidator = OutcomeConsolidator(
                pool=getattr(self._memory, "candidate_pool", None),
                working_memory=getattr(self._memory, "working_memory", None),
            )

    async def stop(self) -> None:
        """Persist current affect state and stop the owned memory system.

        CP-B: Store has no stop() (Runtime closes it explicitly after this
        returns) — only MemorySystem-style memories are stopped here.
        """
        structured_state = getattr(self._memory, "structured_state", None)
        if structured_state is not None:
            await structured_state.set_fact("affect_state", self._affect.to_dict())
        else:
            await self._write_affect_fact()

        stop_fn = getattr(self._memory, "stop", None)
        if stop_fn is not None:
            await stop_fn()

    # ── affect persistence against a Store (no structured_state) ───────────────

    async def _read_affect_fact(self) -> dict | None:
        """Latest current affect_state row from Store's `facts` table, if any.

        Store has no generic key-value state table (OUT OF SCOPE forbids
        adding one) — see `_AFFECT_STATE_FACT_SUBJECT` and DECISIONS.md.
        """
        db = getattr(self._memory, "db", None)
        if db is None:
            return None
        async with db.execute(
            "SELECT text FROM facts WHERE subject = ? AND valid_until IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (_AFFECT_STATE_FACT_SUBJECT,),
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row is not None else None

    async def _write_affect_fact(self) -> None:
        db = getattr(self._memory, "db", None)
        if db is None:
            return
        now = time.time()
        await db.execute(
            "UPDATE facts SET valid_until = ? WHERE subject = ? AND valid_until IS NULL",
            (now, _AFFECT_STATE_FACT_SUBJECT),
        )
        await db.execute(
            "INSERT INTO facts (ts, subject, text, source_kind, confidence, valid_from)"
            " VALUES (?, ?, ?, 'inferred', 1.0, ?)",
            (now, _AFFECT_STATE_FACT_SUBJECT, json.dumps(self._affect.to_dict()), now),
        )
        await db.commit()

    # ── Ingest ───────────────────────────────────────────────────────────────

    async def _ingest_sensory(self, obs: Observation) -> None:
        # Unguarded (CP-A; still true under CP-B's Store). A memory that is
        # not open, or a store that cannot take the write, is a turn that did
        # not happen; the daemon treats it as fatal rather than answering
        # over a store that is silently dropping her record.
        #
        # One atom per turn (CP-B change 2): obs.source names who/what this
        # observation is FROM, which Store's append_atom splits into two
        # different vocabularies — see _ATOM_SPEAKER_BY_OBS_SOURCE and
        # DECISIONS.md for the mapping.
        speaker = _ATOM_SPEAKER_BY_OBS_SOURCE.get(obs.source, "system")
        source = _ATOM_SOURCE_BY_OBS_SOURCE.get(obs.source, _DEFAULT_ATOM_SOURCE)
        await self._memory.append_atom(speaker=speaker, source=source, text=obs.content)

    async def _ingest_outcome(self, obs: Observation) -> None:
        if obs.predicted is None or obs.actual is None:
            return

        from lyra_core.development import Outcome

        success = obs.predicted == obs.actual
        self._competence.observe_error(0.0 if success else 1.0)

        if not success:
            self._relational.observe_recurrence(_FAILURE_PROBLEM_ID)

        outcome = Outcome(
            intent=Intent(kind=IntentKind.noop, payload={}),
            success=success,
            affect=self._affect.state,
            drive="boredom" if success else "relational",
        )
        if getattr(self._memory, "candidate_pool", None) is not None:
            await self._consolidator.record_outcome(outcome)

    async def _get_promoted_traits(self) -> list:
        identity_engine = getattr(self._memory, "identity_engine", None)
        if identity_engine is None:
            return []
        return await identity_engine.get_top_traits()

    # ── Tick ─────────────────────────────────────────────────────────────────

    def _gate_intents(self, intents: list[Intent]) -> list[Intent]:
        """Run intents through the harm gate; drop blocked ones and log each block.

        This is the single chokepoint all intents must pass before leaving the
        core.  Action-selection produces intents that flow into tick() and
        therefore through here automatically — nothing bypasses it.  A blocked
        intent must never vanish silently; the log line is the trace.
        """
        allowed = []
        for intent in intents:
            decision = self._gate.check(intent)
            if decision.allowed:
                allowed.append(intent)
            else:
                print(
                    f"[{datetime.now().isoformat()}] [gate] BLOCKED"
                    f" {intent.kind}: {decision.reason}"
                )
        return allowed

    async def tick(self, observations: list[Observation], dt: float = 0.1) -> tuple[list[Intent], AffectState]:
        """Ingest observations, advance affect and drives, return (intents, affect).

        a. Sensory observations go to memory (add_turn for conversation/lyra
           sources, add_observation otherwise); action_outcome observations
           with predicted/actual feed CompetenceTracker, RelationalDrive (on
           failure), and the OutcomeConsolidator — all using the affect-at-
           the-time of this tick (before this tick's affect update).
        b. Drives advance by dt; "engaged" means at least one observation
           arrived this tick.
        c. AffectEngine advances by dt using the summed drive AffectPushes.
        d. Promoted traits bias ActionSelector; intents pass through the gate.
        """
        from lyra_core.development import bias_from_traits

        for obs in observations:
            if obs.kind == ObservationKind.sensory:
                await self._ingest_sensory(obs)
            elif obs.kind == ObservationKind.action_outcome:
                await self._ingest_outcome(obs)

        engaged = len(observations) > 0
        self._boredom.update(dt, engaged=engaged)
        self._relational.update(dt)

        boredom_push = self._boredom.affect_push
        relational_push = self._relational.affect_push
        self._affect.update(
            dt,
            valence_input=boredom_push.valence_delta + relational_push.valence_delta,
            arousal_input=boredom_push.arousal_delta + relational_push.arousal_delta,
        )

        traits = await self._get_promoted_traits()
        bias = bias_from_traits(traits)

        pressures = {"boredom": self._boredom.pressure, "relational": self._relational.pressure}
        intents = self._selector.select(pressures, self._affect.state, bias=bias)
        intents = self._gate_intents(intents)

        return intents, self._affect.state

    def introspect(self) -> AffectState:
        """Read current affect WITHOUT advancing the core (read-only port).

        Deliberately separate from tick() so that observing the box never
        mutates it. This is the port the telemetry/dashboard and Lyra's own
        self-reading use.
        """
        return self._affect.state
