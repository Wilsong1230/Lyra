"""Contract tests for lyra_core.interface.

Observation/Intent/AffectVector/AffectState tests assert shape and
construction. CognitiveCore and Harness tests drive the live cognitive loop:
tick() is async (asyncio.run() wrappers, matching test_runtime.py), ingests
observations, advances drives + affect, and selects gated intents.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from lyra_core.interface import (
    AffectState,
    AffectVector,
    CognitiveCore,
    Intent,
    IntentKind,
    Observation,
    ObservationKind,
)
from lyra_memory.store import Store


# ── Observation ───────────────────────────────────────────────────────────────

def test_sensory_observation_minimal():
    obs = Observation(
        kind=ObservationKind.sensory,
        source="vision",
        content="a terminal window",
    )
    assert obs.kind == ObservationKind.sensory
    assert obs.source == "vision"
    assert obs.content == "a terminal window"
    assert isinstance(obs.ts, float)
    assert obs.ts > 0


def test_observation_reserved_prediction_slots_default_none():
    obs = Observation(kind=ObservationKind.action_outcome, source="self", content="done")
    assert obs.predicted is None
    assert obs.actual is None


def test_action_outcome_observation_carries_prediction_pairing():
    """Reserved competence-drive seam: expected vs actual outcome."""
    obs = Observation(
        kind=ObservationKind.action_outcome,
        source="motor",
        content="arm moved",
        predicted="arm reaches within 5 cm",
        actual="arm reached within 3 cm",
    )
    assert obs.predicted == "arm reaches within 5 cm"
    assert obs.actual == "arm reached within 3 cm"


def test_external_affect_observation_reserved_kind_is_constructable():
    """Reserved empathy seam — kind accepted now, unused until later."""
    obs = Observation(
        kind=ObservationKind.external_affect,
        source="conversation",
        content="Wilson seems tense",
    )
    assert obs.kind == ObservationKind.external_affect


def test_external_affect_observation_carry_affect_hint():
    """Reserved empathy slot: sensed valence/arousal of Wilson."""
    obs = Observation(
        kind=ObservationKind.external_affect,
        source="conversation",
        content="Wilson seems tense",
        affect_hint={"valence": -0.5, "arousal": 0.8},
    )
    assert obs.affect_hint == {"valence": -0.5, "arousal": 0.8}


def test_observation_affect_hint_defaults_none():
    obs = Observation(kind=ObservationKind.sensory, source="ears", content="quiet")
    assert obs.affect_hint is None


def test_observation_accepts_explicit_ts():
    t = time.time() - 10.0
    obs = Observation(kind=ObservationKind.sensory, source="self", content="x", ts=t)
    assert obs.ts == pytest.approx(t)


# ── Intent ────────────────────────────────────────────────────────────────────

def test_intent_construction():
    intent = Intent(kind=IntentKind.speak, payload={"text": "hello"})
    assert intent.kind == IntentKind.speak
    assert intent.payload == {"text": "hello"}
    assert isinstance(intent.ts, float)


def test_all_intent_kinds_are_constructable():
    """Every IntentKind must be constructable — reserved kinds included."""
    for kind in IntentKind:
        intent = Intent(kind=kind, payload={})
        assert intent.kind == kind


def test_intent_noop_has_empty_payload():
    intent = Intent(kind=IntentKind.noop, payload={})
    assert intent.payload == {}


# ── AffectVector ──────────────────────────────────────────────────────────────

def test_affect_vector_neutral_default():
    v = AffectVector()
    assert v.valence == 0.0
    assert v.arousal == 0.0
    assert v.control is None  # third axis reserved


def test_affect_vector_control_axis_reserved_but_settable():
    v = AffectVector(valence=0.2, arousal=0.4, control=0.6)
    assert v.control == pytest.approx(0.6)


# ── AffectState ───────────────────────────────────────────────────────────────

def test_affect_state_neutral_default():
    state = AffectState()
    assert state.valence == pytest.approx(0.0)
    assert state.arousal == pytest.approx(0.0)
    assert state.control is None


def test_affect_state_valence_arousal_delegate_to_emotion():
    """Top-level scalars must reflect the emotion vector, not be stored separately."""
    emotion = AffectVector(valence=0.7, arousal=0.3)
    state = AffectState(emotion=emotion)
    assert state.valence == pytest.approx(0.7)
    assert state.arousal == pytest.approx(0.3)


def test_affect_state_timescale_structure_phase0():
    """Phase 0: emotion populated, mood and temperament reserved seams (None)."""
    state = AffectState()
    assert isinstance(state.emotion, AffectVector)
    assert state.mood is None          # reserved seam
    assert state.temperament is None   # reserved seam


def test_affect_state_mood_and_temperament_accept_affect_vector():
    """Reserved seams must accept AffectVector when activated in later phases."""
    state = AffectState(
        emotion=AffectVector(valence=0.5, arousal=0.3),
        mood=AffectVector(valence=0.1, arousal=0.0),
        temperament=AffectVector(valence=0.0, arousal=0.2),
    )
    assert state.mood is not None
    assert state.temperament is not None


# ── CognitiveCore.tick() ───────────────────────────────────────────────────────

def test_tick_returns_correct_types():
    async def _run():
        core = CognitiveCore(memory=_RecordingMemory())
        obs = Observation(kind=ObservationKind.sensory, source="test", content="x")
        return await core.tick([obs])

    intents, affect = asyncio.run(_run())
    assert isinstance(intents, list)
    assert isinstance(affect, AffectState)


def test_tick_with_no_observations_emits_look_intent_from_boredom():
    """An idle tick: boredom pressure (0.01) outweighs the near-zero
    frustration from one tick's worth of idle drift, so the selector emits
    a look intent."""
    async def _run():
        core = CognitiveCore()
        return await core.tick([])

    intents, _ = asyncio.run(_run())
    assert len(intents) == 1
    assert intents[0].kind == IntentKind.look


def test_tick_with_no_observations_affect_stays_near_neutral():
    """One idle tick nudges affect slightly negative (boredom's own push) but
    stays bounded — not the exact 0.0 of the old stub, but small."""
    async def _run():
        core = CognitiveCore()
        return await core.tick([])

    _, affect = asyncio.run(_run())
    assert abs(affect.valence) < 0.01
    assert abs(affect.arousal) < 0.01


def test_tick_observations_without_signal_match_empty_tick():
    """Observations that carry no competence/memory signal — a sensory obs
    from a non-conversation source (recorded by the fake memory, no drive signal),
    an action_outcome with no predicted/actual, and the reserved
    external_affect kind — leave the core in the same state as an empty tick."""
    observations = [
        Observation(kind=ObservationKind.sensory, source="vision", content="screen"),
        Observation(kind=ObservationKind.action_outcome, source="self", content="done"),
        Observation(
            kind=ObservationKind.external_affect,
            source="conversation",
            content="Wilson smiles",
            affect_hint={"valence": 0.8, "arousal": 0.3},
        ),
    ]

    async def _run():
        with_obs = await CognitiveCore(memory=_RecordingMemory()).tick(observations)
        without_obs = await CognitiveCore(memory=_RecordingMemory()).tick([])
        return with_obs, without_obs

    (intents_a, affect_a), (intents_b, affect_b) = asyncio.run(_run())
    assert [(i.kind, i.payload) for i in intents_a] == [(i.kind, i.payload) for i in intents_b]
    assert affect_a.valence == pytest.approx(affect_b.valence)
    assert affect_a.arousal == pytest.approx(affect_b.arousal)


# ── CP-D: retrieval intent production ──────────────────────────────────────────

def test_tick_produces_retrieval_intent_for_a_conversation_observation():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="conversation", content="hello")

    intents, _ = asyncio.run(core.tick([obs]))

    assert any(i.kind == IntentKind.retrieval for i in intents)


def test_tick_does_not_produce_retrieval_intent_for_a_lyra_observation():
    """The SECOND tick of an exchange (her reply) must not itself propose a
    fresh retrieval — retrieval already happened for this exchange."""
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="lyra", content="hi there")

    intents, _ = asyncio.run(core.tick([obs]))

    assert not any(i.kind == IntentKind.retrieval for i in intents)


def test_tick_does_not_produce_retrieval_intent_for_a_vision_observation():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="vision", content="a window")

    intents, _ = asyncio.run(core.tick([obs]))

    assert not any(i.kind == IntentKind.retrieval for i in intents)


def test_tick_does_not_produce_retrieval_intent_for_an_empty_tick():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)

    intents, _ = asyncio.run(core.tick([]))

    assert not any(i.kind == IntentKind.retrieval for i in intents)


# ── Ingest routing: source threading ──────────────────────────────────────────

class _RecordingMemory:
    """Fake memory whose append_atom/ingest_turn record calls (no errors).

    CP-B: the daemon writes one Store atom per turn instead of calling
    add_turn/add_observation on a MemorySystem — see interface.py's
    _ingest_sensory and DECISIONS.md. CP-D.0: "conversation"/"lyra" text no
    longer goes through append_atom at all — see ingest_turn below and
    DECISIONS.md (CP-D.0).
    """

    def __init__(self) -> None:
        self.atoms: list[tuple[str, str, str]] = []  # (speaker, source, text)
        self.ingest_calls: list[dict] = []
        self.candidate_pool = None
        self.identity_engine = None
        self.structured_state = None

    async def append_atom(self, speaker: str, source: str, text: str, **_: object) -> int:
        self.atoms.append((speaker, source, text))
        return len(self.atoms)

    async def ingest_turn(
        self, user_text: str, lyra_text: str, source: str = "cli", injected: dict | None = None,
        **_: object,
    ) -> tuple[int, int]:
        self.ingest_calls.append({
            "user_text": user_text, "lyra_text": lyra_text, "source": source,
            "injected": injected or {},
        })
        return (len(self.ingest_calls) * 2 - 1, len(self.ingest_calls) * 2)


def test_ingest_no_longer_writes_an_atom_for_conversation_source():
    """CP-D.0: "conversation"/"lyra" text is persisted by ingest_exchange()
    (via Store.ingest_turn, bundled with the lyra half and the retrieval
    provenance), not by ticking a sensory observation through
    _ingest_sensory — see DECISIONS.md."""
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="conversation", content="hello")

    asyncio.run(core.tick([obs]))

    assert memory.atoms == []
    assert memory.ingest_calls == []


def test_ingest_no_longer_writes_an_atom_for_lyra_source():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="lyra", content="hi there")

    asyncio.run(core.tick([obs]))

    assert memory.atoms == []


def test_ingest_routes_vision_source_to_vision_channel():
    """Vision is not part of an "exchange" pair — it still writes its own
    solo atom the old way, unaffected by CP-D.0."""
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="vision", content="a terminal window")

    asyncio.run(core.tick([obs]))

    assert memory.atoms == [("system", "vision", "a terminal window")]


# ── ingest_exchange / retrieve_context (CP-D.0) ────────────────────────────────

class _FakeContextResult:
    def __init__(self, row: dict) -> None:
        self._row = row

    def as_log_row(self) -> dict:
        return dict(self._row)


def test_ingest_exchange_calls_store_ingest_turn_with_both_texts():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    context = _FakeContextResult({"atom_ids": [1, 2], "fact_ids": [], "dream_ids": [], "budget_used": 40})

    asyncio.run(core.ingest_exchange("hello", "hi there", context, session_id="s1"))

    assert len(memory.ingest_calls) == 1
    call = memory.ingest_calls[0]
    assert call["user_text"] == "hello"
    assert call["lyra_text"] == "hi there"
    assert call["source"] == "cli"


def test_ingest_exchange_threads_session_id_into_injected():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    context = _FakeContextResult({"atom_ids": [], "fact_ids": [], "dream_ids": [], "budget_used": 0})

    asyncio.run(core.ingest_exchange("hello", "hi there", context, session_id="session-xyz"))

    assert memory.ingest_calls[0]["injected"]["session_id"] == "session-xyz"


def test_ingest_exchange_carries_the_context_result_as_log_row():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    row = {"atom_ids": [3], "fact_ids": [4], "dream_ids": [], "budget_used": 12, "misses": ["lexical: no BM25 match"]}
    context = _FakeContextResult(row)

    asyncio.run(core.ingest_exchange("q", "a", context, session_id=1))

    injected = memory.ingest_calls[0]["injected"]
    for key, value in row.items():
        assert injected[key] == value


def test_ingest_exchange_returns_the_two_atom_ids_and_a_context_log_id():
    """CP-D: context_log_id is None here because _RecordingMemory has no
    `.db` for _latest_context_log_id to query — the real lookup against a
    live Store is covered below (test_ingest_exchange_against_a_real_store)."""
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    context = _FakeContextResult({"atom_ids": [], "fact_ids": [], "dream_ids": [], "budget_used": 0})

    result = asyncio.run(core.ingest_exchange("q", "a", context, session_id="s1"))

    assert result == (1, 2, None)


def test_ingest_exchange_with_none_context_marks_the_turn_declined():
    """CP-D change 3: a turn that declined to retrieve still gets a
    context_log row, marked via a distinguished misses entry rather than
    being silently absent."""
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)

    asyncio.run(core.ingest_exchange("q", "a", None, session_id="s1"))

    injected = memory.ingest_calls[0]["injected"]
    assert injected["atom_ids"] == []
    assert injected["misses"] == ["retrieval: declined"]


def test_retrieve_context_calls_through_to_build_context(monkeypatch):
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)

    async def _fake_build_context(store, query):
        assert store is memory
        assert query == "what's the plan"
        return "SENTINEL"

    import lyra_core.interface as interface_module
    monkeypatch.setattr(interface_module, "_build_context", _fake_build_context)

    result = asyncio.run(core.retrieve_context("what's the plan"))
    assert result == "SENTINEL"


# ── record_retrieval_outcome / consolidate_retrieval_outcome (CP-D) ────────────

def test_record_retrieval_outcome_returns_none_without_a_record_outcome_method():
    """_RecordingMemory has no record_outcome — same duck-typed graceful
    absence as every other Store-only method on CognitiveCore."""
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)

    result = asyncio.run(core.record_retrieval_outcome(1, context_log_id=2, atom_count=3, path="both"))

    assert result is None


def test_consolidate_retrieval_outcome_returns_none_without_a_db():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)

    result = asyncio.run(core.consolidate_retrieval_outcome(had_context=True))

    assert result is None


def test_ingest_routes_other_sources_to_system_speaker_over_cli():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)
    obs = Observation(kind=ObservationKind.sensory, source="ears", content="ambient sound: rain")

    asyncio.run(core.tick([obs]))

    assert memory.atoms == [("system", "cli", "ambient sound: rain")]


# ── CognitiveCore.introspect() ────────────────────────────────────────────────

def test_introspect_returns_affect_state():
    core = CognitiveCore()
    result = core.introspect()
    assert isinstance(result, AffectState)


def test_introspect_returns_neutral_affect_before_any_tick():
    core = CognitiveCore()
    state = core.introspect()
    assert state.valence == pytest.approx(0.0)
    assert state.arousal == pytest.approx(0.0)


def test_introspect_is_non_mutating():
    """introspect() must never change what tick() or a subsequent introspect()
    returns.

    Sequence: introspect (neutral, pre-tick) -> tick([]) (legitimately moves
    affect) -> introspect (must match the tick's result exactly) -> introspect
    again (must be idempotent)."""
    async def _run():
        core = CognitiveCore()
        before = core.introspect()
        _, after_tick = await core.tick([])
        after = core.introspect()
        again = core.introspect()
        return before, after_tick, after, again

    before, after_tick, after, again = asyncio.run(_run())

    assert before.valence == pytest.approx(0.0)
    assert before.arousal == pytest.approx(0.0)

    assert after.valence == pytest.approx(after_tick.valence)
    assert after.arousal == pytest.approx(after_tick.arousal)

    assert again.valence == pytest.approx(after.valence)
    assert again.arousal == pytest.approx(after.arousal)


# ── Harness ───────────────────────────────────────────────────────────────────

from lyra_core.gate import ALLOWED_KINDS, GateDecision
from lyra_core.harness import Harness, Script, TickRecord, failures, outcome, sensory


def test_run_produces_one_record_per_tick():
    async def _run():
        h = Harness(core=CognitiveCore(memory=_RecordingMemory()))
        script: Script = [
            [sensory("frame 0")],
            [sensory("frame 1"), sensory("frame 2")],
            [],  # empty tick is valid
        ]
        return await h.run(script)

    trace = asyncio.run(_run())
    assert len(trace) == 3


def test_tick_records_have_sequential_indices():
    async def _run():
        h = Harness(core=CognitiveCore(memory=_RecordingMemory()))
        return await h.run([[sensory("a")], [sensory("b")], [sensory("c")]])

    trace = asyncio.run(_run())
    assert [r.index for r in trace] == [0, 1, 2]


def test_tick_record_observations_match_input():
    obs0 = sensory("hello", source="vision")
    obs1 = outcome("done", predicted="fast", actual="slow")
    script: Script = [[obs0], [obs1]]

    async def _run():
        h = Harness(core=CognitiveCore(memory=_RecordingMemory()))
        return await h.run(script)

    trace = asyncio.run(_run())
    assert trace[0].observations == [obs0]
    assert trace[1].observations == [obs1]


def test_tick_record_intents_are_allowed_kinds():
    """Every intent the live core proposes must be within ALLOWED_KINDS —
    the selector never has a path to a blocked kind."""
    async def _run():
        h = Harness(core=CognitiveCore(memory=_RecordingMemory()))
        return await h.run([[sensory("x")], [sensory("y")]])

    trace = asyncio.run(_run())
    for r in trace:
        for i in r.intents:
            assert i.kind in ALLOWED_KINDS


def test_tick_record_affect_is_bounded_and_typed():
    async def _run():
        h = Harness(core=CognitiveCore(memory=_RecordingMemory()))
        return await h.run([[sensory("x")], [sensory("y")], []])

    trace = asyncio.run(_run())
    for r in trace:
        assert isinstance(r.affect, AffectState)
        assert abs(r.affect.valence) < 0.1
        assert abs(r.affect.arousal) < 0.1


def test_run_empty_script_returns_empty_trace():
    async def _run():
        return await Harness().run([])

    assert asyncio.run(_run()) == []


def test_harness_constructs_own_core_when_none_given():
    """Harness() with no argument must build its own CognitiveCore and run.

    An empty tick only: since CP-B the self-constructed core has no memory
    injected at all (Store can't be default-constructed — opening one is
    async), and a sensory observation into one is an error, not a no-op."""
    async def _run():
        h = Harness()
        return await h.run([[]])

    trace = asyncio.run(_run())
    assert len(trace) == 1


def test_harness_accepts_injected_core():
    async def _run():
        core = CognitiveCore(memory=_RecordingMemory())
        h = Harness(core=core)
        return await h.run([[sensory("injected")]])

    trace = asyncio.run(_run())
    assert len(trace) == 1


def test_failures_scaffold_produces_correct_tick_count():
    script = failures(5)
    assert len(script) == 5


def test_failures_scaffold_each_tick_has_one_action_outcome_observation():
    script = failures(5)
    for batch in script:
        assert len(batch) == 1
        assert batch[0].kind == ObservationKind.action_outcome


# ── Full-loop: failures -> frustration -> flip -> outcome -> candidate ────────

class _RecordingPool:
    """Fake CandidatePool — records what the consolidator sends it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def add_observation(self, trait_name: str, trait_value: str, category: str,
                              closed_vocabulary: bool = False) -> None:
        self.calls.append((trait_name, trait_value, category))


class _FakeMemory:
    """Minimal memory stand-in: only candidate_pool is populated.

    working_memory/identity_engine stay None, so ingest is a no-op and no
    bias is computed — isolates the affect/drive/selector/consolidator loop.
    """

    def __init__(self, pool: _RecordingPool) -> None:
        self.candidate_pool = pool
        self.working_memory = None
        self.identity_engine = None
        self.structured_state = None


def test_failures_full_loop_accumulates_frustration_overrides_drive_and_records_outcomes():
    """The live loop, end to end, against failures(10):
      - frustration accumulates: valence trends increasingly negative each tick
      - the flip: frustration overrides the boredom-driven 'look' intent
      - every intent stays within the allowed set
      - each failure outcome is recorded into the candidate pool
    """
    pool = _RecordingPool()
    core = CognitiveCore(memory=_FakeMemory(pool))

    async def _run():
        h = Harness(core=core)
        return await h.run(failures(10))

    trace = asyncio.run(_run())

    # Frustration accumulates.
    valences = [r.affect.valence for r in trace]
    for i in range(len(valences) - 1):
        assert valences[i + 1] < valences[i], f"valence did not worsen at step {i}: {valences}"

    # The flip: boredom's 'look' intent is overridden by frustration on at
    # least one tick.
    assert any(
        IntentKind.look not in [i.kind for i in r.intents]
        for r in trace
    ), "boredom drive was never overridden by frustration"

    # Gate boundary holds throughout.
    for r in trace:
        for i in r.intents:
            assert i.kind in ALLOWED_KINDS

    # Outcome -> candidate: every failure was recorded for trait consolidation.
    assert len(pool.calls) == 10
    assert all(category == "behavioral" for _, _, category in pool.calls)


# ── CognitiveCore gate wiring ─────────────────────────────────────────────────

def test_tick_intents_all_pass_gate_check():
    """Gate pass-through: tick()'s output already satisfies the gate — every
    intent it returns is in ALLOWED_KINDS."""
    async def _run():
        core = CognitiveCore()
        return await core.tick([])

    intents, affect = asyncio.run(_run())
    for i in intents:
        assert i.kind in ALLOWED_KINDS
    assert abs(affect.valence) < 0.01


def test_gate_intents_passes_allowed_kinds_through():
    core = CognitiveCore()
    speak = Intent(kind=IntentKind.speak, payload={"text": "hello"})
    noop = Intent(kind=IntentKind.noop, payload={})
    result = core._gate_intents([speak, noop])
    assert result == [speak, noop]


def test_gate_intents_drops_blocked_kind():
    core = CognitiveCore()
    research = Intent(kind=IntentKind.research, payload={})
    assert core._gate_intents([research]) == []


def test_gate_intents_mixed_list_keeps_allowed_drops_blocked():
    core = CognitiveCore()
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    research = Intent(kind=IntentKind.research, payload={})
    noop = Intent(kind=IntentKind.noop, payload={})
    result = core._gate_intents([speak, research, noop])
    assert result == [speak, noop]


def test_gate_intents_logs_blocked_intent(capsys):
    core = CognitiveCore()
    research = Intent(kind=IntentKind.research, payload={})
    core._gate_intents([research])
    captured = capsys.readouterr()
    assert "[gate] BLOCKED" in captured.out
    assert "research" in captured.out


def test_gate_intents_allowed_intent_produces_no_log(capsys):
    core = CognitiveCore()
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    core._gate_intents([speak])
    captured = capsys.readouterr()
    assert captured.out == ""


def test_default_gate_blocks_research():
    """Safe-by-default: no-arg CognitiveCore uses a real HarmGate and drops research."""
    core = CognitiveCore()
    research = Intent(kind=IntentKind.research, payload={})
    assert core._gate_intents([research]) == []


def test_injected_gate_that_blocks_all_returns_empty():
    """Injectable gate: a stub that blocks everything produces empty output."""
    class _BlockAll:
        def check(self, intent):
            return GateDecision(allowed=False, reason="stub: block all")

    core = CognitiveCore(gate=_BlockAll())
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    noop = Intent(kind=IntentKind.noop, payload={})
    assert core._gate_intents([speak, noop]) == []


def test_injected_gate_that_allows_all_passes_everything_through():
    """Injectable gate: a stub that allows everything lets all intents pass."""
    class _AllowAll:
        def check(self, intent):
            return GateDecision(allowed=True)

    core = CognitiveCore(gate=_AllowAll())
    research = Intent(kind=IntentKind.research, payload={})
    speak = Intent(kind=IntentKind.speak, payload={"text": "hi"})
    assert core._gate_intents([research, speak]) == [research, speak]


def test_introspect_unaffected_by_gate_wiring():
    """introspect() is read-only; gate must not be involved."""
    core = CognitiveCore()
    state = core.introspect()
    assert state.valence == pytest.approx(0.0)
    assert state.arousal == pytest.approx(0.0)


# ── CP-D against a real Store: context_log_id, outcomes, candidates ────────────

@pytest.fixture
async def store(tmp_path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def test_ingest_exchange_against_a_real_store(store):
    core = CognitiveCore(memory=store)
    context = await core.retrieve_context("hello")

    user_id, lyra_id, context_log_id = await core.ingest_exchange(
        "hello", "hi there", context, session_id="s1")

    assert user_id != lyra_id
    assert context_log_id is not None
    async with store.db.execute(
        "SELECT session_id, atom_ids FROM context_log WHERE id = ?", (context_log_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "s1"


async def test_record_retrieval_outcome_writes_an_outcomes_row(store):
    core = CognitiveCore(memory=store)
    user_id = await store.append_atom(speaker="wilson", source="cli", text="hello")

    outcome_id = await core.record_retrieval_outcome(
        user_id, context_log_id=7, atom_count=3, path="both")

    assert outcome_id is not None
    async with store.db.execute(
        "SELECT intent_atom_id, valence, actual, predicted, environment FROM outcomes WHERE id = ?",
        (outcome_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == user_id
    assert row[1] == 1.0
    assert row[2] == "both"
    assert row[3] == "context_available"
    assert "context_log_id=7" in row[4]
    assert "atom_count=3" in row[4]


async def test_record_retrieval_outcome_valence_is_zero_when_nothing_was_found(store):
    core = CognitiveCore(memory=store)
    user_id = await store.append_atom(speaker="wilson", source="cli", text="hello")

    outcome_id = await core.record_retrieval_outcome(
        user_id, context_log_id=1, atom_count=0, path="neither")

    async with store.db.execute("SELECT valence FROM outcomes WHERE id = ?", (outcome_id,)) as cur:
        row = await cur.fetchone()
    assert row[0] == 0.0


async def test_consolidate_retrieval_outcome_writes_a_candidates_row(store):
    core = CognitiveCore(memory=store)

    result = await core.consolidate_retrieval_outcome(had_context=True)

    assert result == (
        "retrieval finds relevant context",
        "an assembled context contained at least one atom above the retrievability floor",
    )
    async with store.db.execute("SELECT trait_name, category FROM candidates") as cur:
        rows = await cur.fetchall()
    assert rows == [("retrieval finds relevant context", "retrieval")]


async def test_consolidate_retrieval_outcome_names_the_other_branch_when_empty(store):
    core = CognitiveCore(memory=store)

    result = await core.consolidate_retrieval_outcome(had_context=False)

    assert result[0] == "retrieval finds nothing"


async def test_consolidate_retrieval_outcome_never_writes_to_traits(store):
    """CP-D change 5: must not promote — this consolidator has no path to
    `traits` at all."""
    core = CognitiveCore(memory=store)

    await core.consolidate_retrieval_outcome(had_context=True)
    await core.consolidate_retrieval_outcome(had_context=False)

    async with store.db.execute("SELECT COUNT(*) FROM traits") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0


# ── promote_traits (CP-F) ────────────────────────────────────────────────────

def test_promote_traits_returns_empty_list_without_a_db():
    memory = _RecordingMemory()
    core = CognitiveCore(memory=memory)

    result = asyncio.run(core.promote_traits())

    assert result == []


async def test_promote_traits_is_a_noop_below_the_surface_threshold(store):
    core = CognitiveCore(memory=store)
    for _ in range(4):
        await core.consolidate_retrieval_outcome(had_context=True)

    promotions = await core.promote_traits()

    assert promotions == []
    async with store.db.execute("SELECT COUNT(*) FROM traits") as cur:
        assert (await cur.fetchone())[0] == 0


async def test_promote_traits_promotes_once_evidence_crosses_surface(store):
    """5 identical closed-vocabulary retrieval outcomes strengthen one
    candidate to evidence_count=5 (TRAIT_THRESHOLDS['surface']); the next
    promote_traits() call must cross it into `traits`."""
    core = CognitiveCore(memory=store)
    for _ in range(5):
        await core.consolidate_retrieval_outcome(had_context=True)

    promotions = await core.promote_traits()

    assert promotions == [{
        "trait_name": "retrieval finds relevant context",
        "evidence_count": 5,
        "threshold": 5,
        "stability": "surface",
    }]
    async with store.db.execute(
        "SELECT name, stability, evidence_count FROM traits"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("retrieval finds relevant context", "surface", 5)]


async def test_promote_traits_returns_empty_on_the_second_call_for_the_same_trait(store):
    """A candidate that already promoted stays a no-op promotion (event
    becomes confidence_change/tier_change, never a second "promoted") until
    it crosses the NEXT threshold — see identity_engine.py's `_upsert_trait`.
    """
    core = CognitiveCore(memory=store)
    for _ in range(5):
        await core.consolidate_retrieval_outcome(had_context=True)
    first = await core.promote_traits()
    assert len(first) == 1

    await core.consolidate_retrieval_outcome(had_context=True)  # evidence_count=6, still surface
    second = await core.promote_traits()

    assert second == []


async def test_promote_traits_writes_a_trait_history_row(store):
    core = CognitiveCore(memory=store)
    for _ in range(5):
        await core.consolidate_retrieval_outcome(had_context=True)

    await core.promote_traits()

    async with store.db.execute(
        "SELECT trait_label, event, evidence_count FROM trait_history"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("retrieval finds relevant context", "promoted", 5)]
