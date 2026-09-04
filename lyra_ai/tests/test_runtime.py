"""Tests for lyra_core.runtime — store preparation, the one core, the tick
clock, the daemon-side turn handler, and the Runtime lifecycle over a real
loopback socket.

CP-B: the store is lyra_memory.store.Store (store.db + runs.db), not the
older lyra_memory.MemorySystem (memory.db) — atoms carry speaker/text, not
role/content; facts carry subject/text, not key/value. See DECISIONS.md.

No LLM: the backend is a fake. No model download: LYRA_EMBED_BACKEND=hashed
must be set explicitly to run offline (conftest.py no longer auto-detects a
MiniLM cache miss — CP-B change 8). Stores are per-test tmp files, and
`legacy_path`/`legacy_db_path` are always pointed at a tmp_path file too —
never the real DB_PATH — so a test run never touches a real ~/.lyra/memory.db.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from lyra.assistant import LAYER1_FACTS, LyraClient
from lyra_core import runtime
from lyra_core.affect import AffectEngine
from lyra_core.interface import (
    AffectState,
    AffectVector,
    CognitiveCore,
    Intent,
    IntentKind,
    RepoContext,
    RetrievalPass,
)
from lyra_core.runtime import (
    CANDIDATE_CREATED,
    CONSOLIDATOR_FIRED,
    CORE_CONSTRUCTED,
    DT_CLAMP_ENGAGED,
    INTENT_DECLINED,
    INTENT_EXECUTED,
    INTENT_PRODUCED,
    OUTCOME_RECORDED,
    REQUIRED_TABLES,
    Runtime,
    StoreMissing,
    StoreSchemaMismatch,
    TickClock,
    TurnHandler,
    archive_legacy_memory_db,
    archive_store,
    check_store_schema,
    construct_core,
    prepare_store,
    store_tables,
)
from lyra_core.transport import TurnRejected
from lyra_memory.store.context import ContextResult

_AFFECT_SUBJECT = "_lyra_internal_affect_state"


@pytest.fixture(autouse=True)
def _one_core_per_test(monkeypatch):
    """The process-level guard is real; each test is its own 'process'."""
    monkeypatch.setattr(runtime, "_core_constructions", 0)


class _FakeBackend:
    name = "fake"
    default_model = "fake-model"

    def __init__(self, replies: list[str] | None = None, fail: Exception | None = None) -> None:
        self.calls: list[tuple[list[dict], str | None]] = []
        self._replies = list(replies) if replies else None
        self._fail = fail

    def chat(self, messages, model=None, system=None) -> str:
        self.calls.append((list(messages), system))
        if self._fail is not None:
            raise self._fail
        if self._replies:
            return self._replies.pop(0)
        return f"echo: {messages[-1]['content']}"


def _pre_atoms_store(path: Path, mtime: datetime) -> None:
    """A store on the schema memory.db was on before atoms existed — and
    still, under CP-B, missing everything Store's schema requires (atoms_fts,
    vec_atoms, commitments, outcomes, candidates included)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE facts (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE episodes (id INTEGER PRIMARY KEY, content TEXT NOT NULL, ts REAL NOT NULL,
                               source_items_json TEXT NOT NULL);
        CREATE TABLE traits (id INTEGER PRIMARY KEY, name TEXT NOT NULL, value TEXT NOT NULL,
                             confidence REAL NOT NULL, stability TEXT NOT NULL,
                             evidence_count INTEGER NOT NULL, updated_at REAL NOT NULL);
        INSERT INTO episodes VALUES (1, 'an old essay', 1.0, '[]');
    """)
    conn.commit()
    conn.close()
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))


# ── store preparation ────────────────────────────────────────────────────────

def test_missing_store_is_fatal_and_names_the_path(tmp_path):
    missing = tmp_path / "store.db"
    with pytest.raises(StoreMissing, match=str(missing)):
        asyncio.run(prepare_store(missing, legacy_path=tmp_path / "legacy_memory.db"))
    assert not missing.exists(), "the daemon must not create a store on its own"


def test_init_store_creates_an_empty_store_with_the_required_tables(tmp_path):
    path = tmp_path / "store.db"
    asyncio.run(prepare_store(
        path, init_if_missing=True, legacy_path=tmp_path / "legacy_memory.db"))
    assert REQUIRED_TABLES <= store_tables(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM atoms").fetchone()[0] == 0


def test_pre_atoms_store_is_archived_by_mtime_date_and_replaced(tmp_path, caplog):
    path = tmp_path / "store.db"
    _pre_atoms_store(path, datetime(2026, 6, 10, 14, 30))
    with pytest.raises(StoreSchemaMismatch, match="atoms"):
        check_store_schema(path)

    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(prepare_store(path, legacy_path=tmp_path / "legacy_memory.db"))

    archive = tmp_path / "store.db.2026-06-10.archive"
    assert archive.is_file(), "archived by rename, named for the store's own mtime"
    with sqlite3.connect(archive) as conn:
        assert conn.execute("SELECT content FROM episodes").fetchone() == ("an old essay",)
    assert REQUIRED_TABLES <= store_tables(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM atoms").fetchone()[0] == 0
    assert str(archive) in caplog.text
    # store.db + its archive + store.db-derived runs.db (Store.open creates
    # runs.db alongside it, and WAL mode leaves -wal/-shm sidecars) — nothing
    # deleted, nothing unaccounted for.
    expected = {"store.db", "store.db.2026-06-10.archive", "runs.db"}
    actual = {p.name for p in tmp_path.iterdir()}
    assert expected <= actual
    assert actual - expected <= {"store.db-wal", "store.db-shm", "runs.db-wal", "runs.db-shm"}


def test_archive_never_clobbers_an_existing_archive(tmp_path):
    path = tmp_path / "store.db"
    when = datetime(2026, 6, 10, 9, 5, 7)
    _pre_atoms_store(path, when)
    first = archive_store(path)
    _pre_atoms_store(path, when)
    second = archive_store(path)
    assert first != second
    assert first.is_file() and second.is_file()
    assert second.name == "store.db.2026-06-10-090507.archive"


def test_archive_carries_wal_sidecars_along(tmp_path):
    path = tmp_path / "store.db"
    _pre_atoms_store(path, datetime(2026, 6, 10))
    (tmp_path / "store.db-wal").write_bytes(b"wal")
    (tmp_path / "store.db-shm").write_bytes(b"shm")
    archive = archive_store(path)
    assert (tmp_path / (archive.name + "-wal")).read_bytes() == b"wal"
    assert (tmp_path / (archive.name + "-shm")).read_bytes() == b"shm"
    assert not (tmp_path / "store.db-wal").exists()


# ── legacy memory.db archiving (CP-B change 4) ─────────────────────────────────

def test_archive_legacy_memory_db_renames_a_leftover_pre_cp_b_store(tmp_path, caplog):
    legacy = tmp_path / "memory.db"
    _pre_atoms_store(legacy, datetime(2026, 6, 10, 8, 0))
    with caplog.at_level(logging.WARNING, logger="lyra_core.runtime"):
        archived = archive_legacy_memory_db(legacy)
    assert archived == tmp_path / "memory.db.2026-06-10.archive"
    assert archived.is_file()
    assert not legacy.exists()
    assert str(archived) in caplog.text


def test_archive_legacy_memory_db_is_a_noop_when_nothing_is_there(tmp_path):
    assert archive_legacy_memory_db(tmp_path / "memory.db") is None


def test_archive_legacy_memory_db_is_idempotent(tmp_path):
    legacy = tmp_path / "memory.db"
    _pre_atoms_store(legacy, datetime(2026, 6, 10))
    first = archive_legacy_memory_db(legacy)
    assert first is not None
    assert archive_legacy_memory_db(legacy) is None, "nothing left to archive a second time"


def test_prepare_store_archives_the_legacy_db_on_cold_start(tmp_path, caplog):
    legacy = tmp_path / "memory.db"
    _pre_atoms_store(legacy, datetime(2026, 6, 10, 8, 0))
    store_path = tmp_path / "store.db"
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(prepare_store(store_path, init_if_missing=True, legacy_path=legacy))
    archive = tmp_path / "memory.db.2026-06-10.archive"
    assert archive.is_file()
    assert not legacy.exists()
    assert REQUIRED_TABLES <= store_tables(store_path)
    assert str(archive) in caplog.text


# ── the one core ─────────────────────────────────────────────────────────────

def test_construct_core_logs_the_marker_once_and_refuses_a_second(caplog):
    memory = MagicMock()
    memory.path = Path("/nowhere/store.db")
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        core = construct_core(memory)
    assert isinstance(core, CognitiveCore)
    assert caplog.text.count(CORE_CONSTRUCTED) == 1
    with pytest.raises(RuntimeError, match="exactly one"):
        construct_core(memory)
    assert caplog.text.count(CORE_CONSTRUCTED) == 1


# ── tick clock ───────────────────────────────────────────────────────────────

def test_tick_clock_passes_small_gaps_through_and_clamps_large_ones():
    now = [1000.0]
    clock = TickClock(max_dt=0.1, now=lambda: now[0])

    now[0] += 0.05
    assert clock.next_dt() == (pytest.approx(0.05), pytest.approx(0.05), False)

    now[0] += 8 * 3600  # idled overnight
    elapsed, dt, clamped = clock.next_dt()
    assert elapsed == pytest.approx(8 * 3600)
    assert dt == 0.1 and clamped is True

    now[0] -= 5.0  # wall clock stepped back
    assert clock.next_dt() == (pytest.approx(-5.0), 0.0, False)


# ── turn handler ─────────────────────────────────────────────────────────────

class _FakeCore:
    """Records ticks, ingest_exchange, record_retrieval_outcome, and
    consolidate_retrieval_outcome calls; introspects a fixed affect;
    retrieve_context() returns a fixed (default empty) ContextResult —
    CP-D.0 moved retrieval and the atom+context_log write onto
    CognitiveCore, CP-D added the retrieval intent itself plus the
    outcome/consolidator steps, so the fake stands in for all of it.

    produces_retrieval=True (the default) makes tick() propose
    IntentKind.retrieval on every "conversation"-sourced observation,
    matching the real CognitiveCore/ActionSelector (interface.py,
    action_selection.py) — set False to test the declined path without
    depending on frustration actually being high enough."""

    def __init__(
        self, affect: AffectState | None = None, context: "ContextResult | None" = None,
        retrieve_context_error: Exception | None = None,
        produces_retrieval: bool = True,
        produces_repo_query: bool = False,
        repo_context: "RepoContext | None" = None,
        repo_citations: tuple[int, int] = (0, 0),
        repo_query_pressure: float | None = None,
        context_passes: list | None = None,
    ) -> None:
        self.ticks: list[tuple[str, str, float]] = []
        self.exchanges: list[tuple[str, str, object, object]] = []
        self.retrieval_outcomes: list[tuple] = []
        self.deliberations: list[tuple] = []
        self.consolidations: list[tuple] = []
        self.promote_traits_calls: int = 0
        self._promotions: list[dict] = []
        self._affect = affect or AffectState()
        self._context = context if context is not None else _context_result("")
        self._retrieve_context_error = retrieve_context_error
        # CP-J: None (the default) means a single pass using self._context,
        # preserving every existing single-pass test's behavior exactly.
        # Pass a list of ContextResult-like objects to simulate a real
        # multi-pass turn — each becomes one yielded RetrievalPass, "new"
        # computed the same way the real retrieve_context_passes() computes
        # it (against atom_ids accumulated across the earlier ones in the
        # list).
        self._context_passes = context_passes
        self._produces_retrieval = produces_retrieval
        self._produces_repo_query = produces_repo_query
        self._repo_context = repo_context if repo_context is not None else RepoContext(text="", commit_hashes=[])
        self._repo_citations = repo_citations
        # CP-I: independent of _produces_repo_query — set explicitly to
        # simulate "was a candidate this tick (pressure > 0) but LOST"
        # (produces_repo_query=False, repo_query_pressure=1.0), the third
        # state alongside WON (produces_repo_query=True) and NOT-A-
        # CANDIDATE (both default/zero). Mirrors interface.py's real
        # CognitiveCore._last_repo_query_pressure exactly.
        self._repo_query_pressure_setting = (
            repo_query_pressure if repo_query_pressure is not None
            else (1.0 if produces_repo_query else 0.0)
        )
        self._last_repo_query_pressure: float = 0.0
        self.repo_context_calls: list[str] = []
        self.repo_citation_calls: list[str] = []
        self.repo_outcomes: list[tuple] = []
        self.repo_citation_consolidations: list[tuple] = []
        self.repo_query_losses: list[tuple] = []
        self.memory = MagicMock()

    async def tick(self, observations, dt=0.1):
        for obs in observations:
            self.ticks.append((obs.source, obs.content, dt))
        intents = []
        if self._produces_retrieval and any(o.source == "conversation" for o in observations):
            intents.append(Intent(kind=IntentKind.retrieval, payload={"reason": "turn"}))
        is_candidate = any(o.source == "conversation" for o in observations)
        self._last_repo_query_pressure = self._repo_query_pressure_setting if is_candidate else 0.0
        if self._produces_repo_query and is_candidate:
            intents.append(Intent(kind=IntentKind.repo_query, payload={"reason": "repo"}))
        return intents, self._affect

    def introspect(self) -> AffectState:
        return self._affect

    async def retrieve_context(self, query):
        if self._retrieve_context_error is not None:
            raise self._retrieve_context_error
        return self._context

    async def retrieve_context_passes(self, query):
        """Fake of CP-J's async-generator multi-pass retrieval. Default
        (context_passes=None): one pass, self._context — identical to
        every pre-CP-J test's expectation of retrieve_context()'s old
        single-pass behavior."""
        if self._retrieve_context_error is not None:
            raise self._retrieve_context_error
        contexts = self._context_passes if self._context_passes is not None else [self._context]
        assembled_ids: set = set()
        for i, ctx in enumerate(contexts, start=1):
            new_ids = [aid for aid in getattr(ctx, "atom_ids", []) if aid not in assembled_ids]
            assembled_ids.update(new_ids)
            yield RetrievalPass(
                index=i, query=query, context=ctx, new_atom_ids=new_ids,
                is_final=(i == len(contexts)),
            )

    async def ingest_exchange(self, user_text, lyra_text, context, session_id, source="cli"):
        self.exchanges.append((user_text, lyra_text, context, session_id))
        return (1, 2, 99)

    async def record_retrieval_outcome(self, user_atom_id, context_log_id, atom_count, path, pass_index=1):
        self.retrieval_outcomes.append((user_atom_id, context_log_id, atom_count, path, pass_index))
        return len(self.retrieval_outcomes)

    async def record_deliberation_pass(self, pass_index, query, atom_count, new_atom_count):
        self.deliberations.append((pass_index, query, atom_count, new_atom_count))
        return len(self.deliberations)

    async def consolidate_retrieval_outcome(self, had_context, outcome_id=None):
        self.consolidations.append((had_context, outcome_id))
        return ("retrieval finds relevant context", "...") if had_context else ("retrieval finds nothing", "...")

    async def promote_traits(self):
        self.promote_traits_calls += 1
        return self._promotions

    async def retrieve_repo_context(self, query):
        self.repo_context_calls.append(query)
        return self._repo_context

    async def check_repo_citations(self, reply_text):
        self.repo_citation_calls.append(reply_text)
        return self._repo_citations

    async def record_repo_query_outcome(self, user_atom_id, context_log_id, hit_count, miss_count):
        self.repo_outcomes.append((user_atom_id, context_log_id, hit_count, miss_count))
        return len(self.repo_outcomes)

    async def consolidate_repo_citation_outcome(self, had_repo_context, hit_count, miss_count, outcome_id=None):
        self.repo_citation_consolidations.append((had_repo_context, hit_count, miss_count, outcome_id))
        if hit_count + miss_count == 0:
            label = "repo context given but nothing cited" if had_repo_context else "no repo context to cite"
        elif miss_count > 0:
            label = "repo citations include a false hash"
        else:
            label = "repo citations verified"
        return (label, "...")

    async def record_repo_query_loss(self, user_atom_id, context_log_id, pressure, affect_valence):
        self.repo_query_losses.append((user_atom_id, context_log_id, pressure, affect_valence))
        return len(self.repo_query_losses)


def _handler(backend, core=None, vision=None, history=None, now=None):
    core = core or _FakeCore()
    history = history if history is not None else MagicMock(get_history=MagicMock(return_value=[]))
    clock = TickClock(max_dt=0.1, now=now or time.time)
    return TurnHandler(core, backend, history, clock, vision_fn=vision or MagicMock()), core, history


def _context_result(text: str) -> ContextResult:
    return ContextResult(
        text=text, blocks={}, atom_ids=[], fact_ids=[], dream_ids=[], budget_used=0, misses=[])


def test_handle_writes_history_and_ticks_user_then_lyra():
    backend = _FakeBackend(["hi there"])
    handler, core, history = _handler(backend)
    reply = asyncio.run(handler.handle("hello", "s1"))
    assert reply == "hi there"
    assert history.add.call_args_list == [call("s1", "user", "hello"), call("s1", "assistant", "hi there")]
    assert [(s, c) for s, c, _ in core.ticks] == [("conversation", "hello"), ("lyra", "hi there")]


def test_handle_ingests_the_exchange_once_with_both_texts_and_the_session():
    """CP-D.0: one ingest_exchange() call per handle(), not two atom writes —
    change 3's "verify the per-exchange atom count is unchanged at two" is
    interface.py's job (Store.ingest_turn always writes exactly two); this
    is the daemon-side half: exactly one call, carrying both texts."""
    backend = _FakeBackend(["hi there"])
    handler, core, history = _handler(backend)
    asyncio.run(handler.handle("hello", "s1"))
    assert len(core.exchanges) == 1
    user_text, lyra_text, context, session_id = core.exchanges[0]
    assert user_text == "hello"
    assert lyra_text == "hi there"
    assert session_id == "s1"
    assert context is core._context, "the exact ContextResult retrieve_context() returned, not a second one"


# ── CP-D: the intent loop, its log markers, and the declined path ─────────────

def test_handle_logs_the_full_loop_in_order_with_a_shared_turn_id(caplog):
    backend = _FakeBackend(["hi there"])
    handler, core, history = _handler(backend)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("hello", "s1"))

    markers = [INTENT_PRODUCED, INTENT_EXECUTED, OUTCOME_RECORDED, CONSOLIDATOR_FIRED]
    lines = [l for l in caplog.text.splitlines() if any(m in l for m in markers)]
    seen = [next(m for m in markers if m in l) for l in lines]
    assert seen == markers, f"expected {markers} in order, got {seen}"

    turn_ids = {l.split("turn=")[1].split()[0] for l in lines}
    assert turn_ids == {"1"}, "all four lines must share one turn id"


def test_handle_declined_retrieval_logs_declined_not_produced(caplog):
    backend = _FakeBackend(["hi there"])
    core = _FakeCore(produces_retrieval=False)
    handler, _, _ = _handler(backend, core=core)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("hello", "s1"))

    assert any(INTENT_DECLINED in l for l in caplog.text.splitlines())
    assert not any(INTENT_PRODUCED in l for l in caplog.text.splitlines())
    assert not any(OUTCOME_RECORDED in l for l in caplog.text.splitlines())
    assert not any(CONSOLIDATOR_FIRED in l for l in caplog.text.splitlines())


def test_handle_declined_retrieval_still_ingests_the_exchange_with_no_context():
    """Change 3: a turn with no retrieval must still produce a context_log
    row (via ingest_exchange with context=None), not skip persistence."""
    backend = _FakeBackend(["hi there"])
    core = _FakeCore(produces_retrieval=False)
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("hello", "s1"))

    assert len(core.exchanges) == 1
    _user, _lyra, context, _session = core.exchanges[0]
    assert context is None


def test_handle_declined_retrieval_never_calls_record_outcome_or_consolidator():
    backend = _FakeBackend(["hi there"])
    core = _FakeCore(produces_retrieval=False)
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("hello", "s1"))

    assert core.retrieval_outcomes == []
    assert core.consolidations == []


def test_handle_executed_retrieval_calls_record_outcome_with_atom_count_and_path():
    context = _context_result("## Recall\n- something")
    context.atom_ids = [1, 2, 3]
    backend = _FakeBackend(["hi there"])
    core = _FakeCore(context=context)
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("hello", "s1"))

    assert len(core.retrieval_outcomes) == 1
    user_atom_id, context_log_id, atom_count, path, pass_index = core.retrieval_outcomes[0]
    assert atom_count == 3
    assert path == "both"  # no misses recorded on this ContextResult
    assert context_log_id == 99  # the third element of _FakeCore.ingest_exchange's return
    assert pass_index == 1


def test_handle_executed_retrieval_fires_the_consolidator_with_had_context():
    context = _context_result("## Recall\n- something")
    context.atom_ids = [1]
    backend = _FakeBackend(["hi there"])
    core = _FakeCore(context=context)
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("hello", "s1"))

    assert core.consolidations == [(True, 1)]


def test_handle_executed_retrieval_with_no_atoms_fires_consolidator_with_false():
    backend = _FakeBackend(["hi there"])
    core = _FakeCore()  # default empty ContextResult: atom_ids == []
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("hello", "s1"))

    assert core.consolidations == [(False, 1)]


def test_handle_logs_candidate_created_when_consolidator_returns_one(caplog):
    backend = _FakeBackend(["hi there"])
    handler, core, _ = _handler(backend)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("hello", "s1"))

    assert any(CANDIDATE_CREATED in l for l in caplog.text.splitlines())


def test_handle_calls_promote_traits_every_retrieval_turn():
    backend = _FakeBackend(["hi there"])
    handler, core, _ = _handler(backend)
    asyncio.run(handler.handle("hello", "s1"))

    assert core.promote_traits_calls == 1


def test_handle_logs_trait_promoted_when_promote_traits_returns_one(caplog):
    from lyra_core.runtime import TRAIT_PROMOTED

    backend = _FakeBackend(["hi there"])
    handler, core, _ = _handler(backend)
    core._promotions = [{"trait_name": "curiosity", "evidence_count": 5, "threshold": 5}]
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("hello", "s1"))

    lines = [l for l in caplog.text.splitlines() if TRAIT_PROMOTED in l]
    assert len(lines) == 1
    assert "trait_name='curiosity'" in lines[0]
    assert "evidence_count=5" in lines[0]
    assert "threshold=5" in lines[0]


def test_handle_logs_no_trait_promoted_when_promote_traits_returns_none(caplog):
    from lyra_core.runtime import TRAIT_PROMOTED

    backend = _FakeBackend(["hi there"])
    handler, core, _ = _handler(backend)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("hello", "s1"))

    assert not any(TRAIT_PROMOTED in l for l in caplog.text.splitlines())


# ── repo_query wiring (CP-G) ─────────────────────────────────────────────────

def test_handle_does_not_call_repo_methods_when_not_produced():
    backend = _FakeBackend(["hi there"])
    handler, core, _ = _handler(backend)  # produces_repo_query=False by default
    asyncio.run(handler.handle("hello", "s1"))

    assert core.repo_context_calls == []
    assert core.repo_citation_calls == []
    assert core.repo_outcomes == []


def test_handle_logs_repo_query_produced_and_executed_when_signaled(caplog):
    from lyra_core.runtime import REPO_QUERY_EXECUTED, REPO_QUERY_PRODUCED

    backend = _FakeBackend(["here is [aaaaaaa]"])
    core = _FakeCore(produces_repo_query=True, repo_context=RepoContext(
        text="## Repository history\n- [aaaaaaa] ...", commit_hashes=["a" * 40],
    ))
    handler, _, _ = _handler(backend, core=core)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("what commit was that", "s1"))

    lines = caplog.text.splitlines()
    assert any(REPO_QUERY_PRODUCED in l for l in lines)
    assert any(REPO_QUERY_EXECUTED in l and "commit_count=1" in l for l in lines)


def test_handle_includes_repo_context_in_the_system_prompt():
    backend = _FakeBackend(["hi there"])
    core = _FakeCore(produces_repo_query=True, repo_context=RepoContext(
        text="## Repository history\n- [aaaaaaa] a commit line", commit_hashes=["a" * 40],
    ))
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit was that", "s1"))

    system = backend.calls[0][1]
    assert "## Repository history" in system
    assert "[aaaaaaa]" in system


def test_handle_calls_check_repo_citations_with_the_reply_text():
    backend = _FakeBackend(["cited [aaaaaaa] right here"])
    core = _FakeCore(produces_repo_query=True)
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit was that", "s1"))

    assert core.repo_citation_calls == ["cited [aaaaaaa] right here"]


def test_handle_records_repo_outcome_with_hit_and_miss_counts():
    backend = _FakeBackend(["reply"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(2, 1))
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit was that", "s1"))

    assert core.repo_outcomes == [(1, 99, 2, 1)]


def test_handle_logs_repo_outcome_recorded(caplog):
    from lyra_core.runtime import REPO_OUTCOME_RECORDED

    backend = _FakeBackend(["reply"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(1, 1))
    handler, _, _ = _handler(backend, core=core)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("what commit was that", "s1"))

    lines = [l for l in caplog.text.splitlines() if REPO_OUTCOME_RECORDED in l]
    assert len(lines) == 1
    assert "hits=1" in lines[0]
    assert "misses=1" in lines[0]


def test_handle_records_repo_outcome_even_with_zero_zero_citations():
    """Change 6: no repo rows retrieved / nothing cited is still recorded."""
    backend = _FakeBackend(["reply with nothing cited"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(0, 0))
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit was that", "s1"))

    assert core.repo_outcomes == [(1, 99, 0, 0)]


def test_repo_query_and_retrieval_both_fire_and_are_independently_recorded():
    backend = _FakeBackend(["reply [aaaaaaa]"])
    core = _FakeCore(produces_retrieval=True, produces_repo_query=True, repo_citations=(1, 0))
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit fixed the retrieval loop", "s1"))

    assert len(core.retrieval_outcomes) == 1
    assert len(core.repo_outcomes) == 1


# ── citation consolidation wiring (CP-H) ─────────────────────────────────────

def test_handle_calls_consolidate_repo_citation_outcome_with_had_context_and_counts():
    backend = _FakeBackend(["reply [aaaaaaa]"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(1, 1), repo_context=RepoContext(
        text="## Repository history\n- [aaaaaaa] ...", commit_hashes=["a" * 40],
    ))
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit was that", "s1"))

    assert core.repo_citation_consolidations == [(True, 1, 1, 1)]


def test_handle_passes_had_repo_context_false_when_nothing_was_retrieved():
    backend = _FakeBackend(["reply"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(0, 0), repo_context=RepoContext(
        text="", commit_hashes=[],
    ))
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit was that", "s1"))

    assert core.repo_citation_consolidations == [(False, 0, 0, 1)]


def test_handle_logs_citation_outcome(caplog):
    from lyra_core.runtime import CITATION_OUTCOME

    backend = _FakeBackend(["reply [aaaaaaa]"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(1, 0), repo_context=RepoContext(
        text="## Repository history\n- [aaaaaaa] ...", commit_hashes=["a" * 40],
    ))
    handler, _, _ = _handler(backend, core=core)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("what commit was that", "s1"))

    lines = [l for l in caplog.text.splitlines() if CITATION_OUTCOME in l]
    assert len(lines) == 1
    assert "label='repo citations verified'" in lines[0]
    assert "hits=1" in lines[0]
    assert "misses=0" in lines[0]


def test_handle_does_not_call_consolidate_repo_citation_when_repo_query_not_produced():
    backend = _FakeBackend(["hi there"])
    handler, core, _ = _handler(backend)  # produces_repo_query=False by default
    asyncio.run(handler.handle("hello", "s1"))

    assert core.repo_citation_consolidations == []


def test_handle_distinguishes_context_no_citation_from_no_context(caplog):
    """DONE-WHEN: a turn with repo context and no citation, and a turn with
    no repo context, land under different labels."""
    from lyra_core.runtime import CITATION_OUTCOME

    backend = _FakeBackend(["reply with nothing cited", "another reply, nothing cited"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(0, 0), repo_context=RepoContext(
        text="## Repository history\n- [aaaaaaa] ...", commit_hashes=["a" * 40],
    ))
    handler, _, _ = _handler(backend, core=core)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("what commit was that", "s1"))

    core._repo_context = RepoContext(text="", commit_hashes=[])
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("what commit was that", "s1"))

    lines = [l for l in caplog.text.splitlines() if CITATION_OUTCOME in l]
    assert len(lines) == 2
    assert "label='repo context given but nothing cited'" in lines[0]
    assert "label='no repo context to cite'" in lines[1]


# ── repo_query as a scored, declinable candidate (CP-I) ─────────────────────

def test_handle_does_not_call_repo_methods_when_not_a_candidate():
    """pressure stays 0.0 (no keyword match) — not a candidate at all, not
    even a loss."""
    backend = _FakeBackend(["hi there"])
    core = _FakeCore()  # produces_repo_query=False, repo_query_pressure=None -> 0.0
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("hello", "s1"))

    assert core.repo_context_calls == []
    assert core.repo_outcomes == []
    assert core.repo_query_losses == []


def test_handle_records_a_repo_query_loss_when_candidate_but_not_won():
    backend = _FakeBackend(["hi there"])
    core = _FakeCore(produces_repo_query=False, repo_query_pressure=1.0)
    handler, _, _ = _handler(backend, core=core)
    asyncio.run(handler.handle("what commit was that", "s1"))

    assert core.repo_query_losses == [(1, 99, 1.0, 0.0)]
    # the WIN path must not have run
    assert core.repo_context_calls == []
    assert core.repo_outcomes == []


def test_handle_logs_repo_query_lost(caplog):
    from lyra_core.runtime import REPO_QUERY_LOST

    backend = _FakeBackend(["hi there"])
    core = _FakeCore(
        produces_repo_query=False, repo_query_pressure=1.0,
        affect=AffectState(emotion=AffectVector(valence=-0.8, arousal=0.0)),
    )
    handler, _, _ = _handler(backend, core=core)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("what commit was that", "s1"))

    lines = [l for l in caplog.text.splitlines() if REPO_QUERY_LOST in l]
    assert len(lines) == 1
    assert "outcome_id=1" in lines[0]
    assert "pressure=1.000000" in lines[0]
    assert "valence=-0.800000" in lines[0]


def test_handle_win_path_still_unregressed_when_repo_query_wins(caplog):
    """DONE-WHEN: repo-read intent wins at least once and still executes
    correctly — CP-G/CP-H behavior (citation checking, consolidation)
    unregressed by CP-I's scoring change."""
    from lyra_core.runtime import CITATION_OUTCOME, REPO_QUERY_EXECUTED, REPO_QUERY_LOST, REPO_QUERY_PRODUCED

    backend = _FakeBackend(["cited [aaaaaaa]"])
    core = _FakeCore(produces_repo_query=True, repo_citations=(1, 0), repo_context=RepoContext(
        text="## Repository history\n- [aaaaaaa] ...", commit_hashes=["a" * 40],
    ))
    handler, _, _ = _handler(backend, core=core)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("what commit was that", "s1"))

    assert any(REPO_QUERY_PRODUCED in l for l in caplog.text.splitlines())
    assert any(REPO_QUERY_EXECUTED in l for l in caplog.text.splitlines())
    assert any(CITATION_OUTCOME in l for l in caplog.text.splitlines())
    assert not any(REPO_QUERY_LOST in l for l in caplog.text.splitlines())
    assert core.repo_citation_calls == ["cited [aaaaaaa]"]
    assert core.repo_query_losses == []


def test_two_turns_get_two_different_turn_ids(caplog):
    backend = _FakeBackend(["first reply", "second reply"])
    handler, _, _ = _handler(backend)
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.handle("one", "s1"))
        asyncio.run(handler.handle("two", "s1"))

    produced = [l for l in caplog.text.splitlines() if INTENT_PRODUCED in l]
    turn_ids = [l.split("turn=")[1].split()[0] for l in produced]
    assert turn_ids == ["1", "2"]


def test_handle_calls_vision_and_reprompts():
    backend = _FakeBackend(["[TOOL:see:screen]", "You have a Python file open."])
    vision = MagicMock(return_value="A code editor with Python open.")
    handler, core, history = _handler(backend, vision=vision)
    reply = asyncio.run(handler.handle("what's on my screen?", "s1"))
    assert reply == "You have a Python file open."
    vision.assert_called_once_with("screen")
    history.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    history.add.assert_any_call("s1", "user", "[Vision result: A code editor with Python open.]")
    assert ("vision", "A code editor with Python open.", 0.1) in [(s, c, round(d, 3)) for s, c, d in core.ticks] or \
        any(s == "vision" for s, _, _ in core.ticks)
    # The vision detour writes its own atom (interface.py's _ingest_sensory,
    # unchanged); ingest_exchange still pairs the ORIGINAL question with the
    # FINAL answer, once.
    assert len(core.exchanges) == 1
    assert core.exchanges[0][0] == "what's on my screen?"
    assert core.exchanges[0][1] == "You have a Python file open."


def test_handle_webcam_source():
    backend = _FakeBackend(["[TOOL:see:webcam]", "I see you."])
    vision = MagicMock(return_value="A person at a desk.")
    handler, _, _ = _handler(backend, vision=vision)
    assert asyncio.run(handler.handle("what do you see?", "s1")) == "I see you."
    vision.assert_called_once_with("webcam")


def test_handle_caps_vision_at_three_attempts():
    backend = _FakeBackend(["[TOOL:see:screen]"] * 3)
    vision = MagicMock(return_value="still a screen")
    handler, core, history = _handler(backend, vision=vision)
    reply = asyncio.run(handler.handle("what's on my screen?", "s1"))
    assert len(backend.calls) == 3 and vision.call_count == 3
    assert "unable to determine" in reply
    assert history.add.call_args_list.count(call("s1", "assistant", "[TOOL:see:screen]")) == 3
    history.add.assert_any_call("s1", "assistant", reply)
    # The fallback still gets ingested as the exchange's "lyra" half.
    assert core.exchanges[0][1] == reply


def test_handle_discards_confabulated_text_after_the_tool_call_and_keeps_preamble():
    backend = _FakeBackend([
        "Let me look.\n[TOOL:see:screen]\nThe screen shows 'Hello Lyra'.",
        "Done.",
    ])
    vision = MagicMock(return_value="A terminal window.")
    handler, _, history = _handler(backend, vision=vision)
    assert asyncio.run(handler.handle("look", "s1")) == "Done."
    stored = [c.args[2] for c in history.add.call_args_list]
    assert "Let me look.\n[TOOL:see:screen]" in stored
    assert not any("Hello Lyra" in s for s in stored)


def test_backend_failure_is_rejected_not_fatal():
    backend = _FakeBackend(fail=ConnectionRefusedError("ollama down"))
    handler, core, history = _handler(backend)
    with pytest.raises(TurnRejected, match="ollama down"):
        asyncio.run(handler.handle("hello", "s1"))
    # The user turn was still recorded to history (a separate store) before
    # the model was asked; the drive/affect tick for it still happened.
    history.add.assert_called_once_with("s1", "user", "hello")
    assert [s for s, _, _ in core.ticks] == ["conversation"]
    # CP-D.0: a rejected turn is not a completed exchange — ingest_turn()
    # requires both texts, and a turn that half-lands is exactly what it
    # exists to prevent (Store's own docstring). No atom is written for it.
    assert core.exchanges == []


def test_context_failure_propagates_instead_of_degrading_to_layer1():
    """The old _get_system swallowed build_context errors and quietly served
    Layer 1 alone. Now the failure is the turn's failure — retrieve_context()
    is unguarded (interface.py), and handle() calls it before ever asking
    the backend for a reply."""
    core = _FakeCore(retrieve_context_error=RuntimeError("store gone"))
    handler, _, _ = _handler(_FakeBackend(["never"]), core=core)
    with pytest.raises(RuntimeError, match="store gone"):
        asyncio.run(handler.handle("hello", "s1"))


def test_compose_system_prompt_is_layer1_plus_context_plus_hint():
    negative = AffectEngine.from_dict({
        "accum_rate": 1.0, "emotion_decay": 2.0, "mood_drift": 0.2,
        "emotion_v": -0.6, "emotion_a": 0.6, "mood_v": 0.0, "mood_a": 0.0,
    }).state
    handler, _, _ = _handler(_FakeBackend(), core=_FakeCore(affect=negative))
    system = handler._compose_system_prompt(_context_result("## Persona Traits\n- curiosity"))
    assert system.startswith(LAYER1_FACTS)
    assert "curiosity" in system
    assert "Keep responses brief and direct. Don't soften or elaborate." in system


def test_compose_system_prompt_is_layer1_alone_when_neutral_and_empty():
    handler, _, _ = _handler(_FakeBackend())
    assert handler._compose_system_prompt(_context_result("")) == LAYER1_FACTS


def test_tick_logs_the_clamp_with_the_raw_gap(caplog):
    now = [1000.0]
    handler, _, _ = _handler(_FakeBackend(), now=lambda: now[0])
    now[0] += 8 * 3600
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(handler.tick("conversation", "morning"))
    line = next(l for l in caplog.text.splitlines() if DT_CLAMP_ENGAGED in l)
    assert "elapsed=28800.000000" in line and "dt=0.100000" in line
    assert "clamped=True" in line
    assert "emotion_v=" in line and "emotion_a=" in line
    assert "mood_v=" in line and "mood_a=" in line
    assert "boredom_pressure=" in line and "relational_pressure=" in line


def test_tick_logs_one_stable_line_per_tick_whether_or_not_clamped(caplog):
    """CP-A.1: a single unified per-tick line, clamped or not — same field
    names either way, so a log can be grepped without special-casing."""
    now = [1000.0]
    handler, _, _ = _handler(_FakeBackend(), now=lambda: now[0])
    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        now[0] += 0.05  # under the 0.1 clamp: not clamped
        asyncio.run(handler.tick("conversation", "hi"))
    lines = [l for l in caplog.text.splitlines() if "source=conversation" in l]
    assert len(lines) == 1
    line = lines[0]
    assert DT_CLAMP_ENGAGED not in line
    assert "clamped=False" in line
    assert "elapsed=0.050000" in line and "dt=0.050000" in line
    for field in (
        "boredom_pressure=", "relational_pressure=",
        "emotion_v=", "emotion_a=", "mood_v=", "mood_a=",
        "temperament_v=", "temperament_a=", "temperament_c=",
    ):
        assert field in line


# ── runtime over a real socket ───────────────────────────────────────────────

def _runtime(tmp_path, backend=None, legacy_db_path=None, **kw) -> Runtime:
    return Runtime(
        backend or _FakeBackend(),
        db_path=tmp_path / "store.db",
        legacy_db_path=legacy_db_path if legacy_db_path is not None else tmp_path / "legacy_memory.db",
        history_path=tmp_path / "history.db",
        port=0,
        init_store=True,
        **kw,
    )


def test_runtime_serves_a_turn_and_writes_history_and_atoms_in_the_daemon(tmp_path, caplog):
    async def _run():
        rt = _runtime(tmp_path)
        await rt.start()
        try:
            client = await LyraClient.connect("127.0.0.1", rt.server.port)
            reply = await client.chat("hello", "s1")
            await client.close()
            return reply
        finally:
            await rt.stop()

    with caplog.at_level(logging.INFO):
        reply = asyncio.run(_run())

    assert reply == "echo: hello"
    assert caplog.text.count(CORE_CONSTRUCTED) == 1
    with sqlite3.connect(tmp_path / "history.db") as conn:
        rows = conn.execute("SELECT session, role, content FROM turns ORDER BY id").fetchall()
    assert rows == [("s1", "user", "hello"), ("s1", "assistant", "echo: hello")]
    with sqlite3.connect(tmp_path / "store.db") as conn:
        atoms = conn.execute("SELECT speaker, source, text FROM atoms ORDER BY id").fetchall()
        affect = conn.execute(
            f"SELECT text FROM facts WHERE subject = '{_AFFECT_SUBJECT}' AND valid_until IS NULL"
        ).fetchone()
    # CP-J: a real retrieval-executed turn now also writes one deliberation
    # atom per pass (change 3) — here, one pass, nothing found, so exactly
    # one "system"/"deliberation" atom lands between the two exchange atoms.
    assert atoms == [
        ("wilson", "cli", "hello"),
        ("lyra", "cli", "echo: hello"),
        ("system", "deliberation", "[retrieval pass 1] query='hello' found 0 atom(s), 0 new"),
    ]
    assert affect is not None, "affect persisted on clean stop"
    assert (tmp_path / "runs.db").is_file()


def test_runtime_survives_client_disconnects_and_serves_the_next_client(tmp_path):
    async def _run():
        rt = _runtime(tmp_path)
        await rt.start()
        try:
            port = rt.server.port
            a = await LyraClient.connect("127.0.0.1", port)
            await a.chat("first", "s1")
            await a.close()  # detach without telling the daemon anything
            b = await LyraClient.connect("127.0.0.1", port)
            reply = await b.chat("second", "s2")
            await b.close()
            return reply
        finally:
            await rt.stop()

    assert asyncio.run(_run()) == "echo: second"
    with sqlite3.connect(tmp_path / "history.db") as conn:
        rows = conn.execute("SELECT session, role, content FROM turns ORDER BY id").fetchall()
    assert [r[2] for r in rows] == ["first", "echo: first", "second", "echo: second"]


def test_two_clients_at_once_land_in_one_history_in_arrival_order(tmp_path):
    async def _run():
        rt = _runtime(tmp_path)
        await rt.start()
        try:
            port = rt.server.port
            a = await LyraClient.connect("127.0.0.1", port)
            b = await LyraClient.connect("127.0.0.1", port)
            ta = asyncio.create_task(a.chat("from A", "sa"))
            await asyncio.sleep(0.01)
            tb = asyncio.create_task(b.chat("from B", "sb"))
            replies = await asyncio.gather(ta, tb)
            await a.close(); await b.close()
            return replies
        finally:
            await rt.stop()

    assert asyncio.run(_run()) == ["echo: from A", "echo: from B"]
    with sqlite3.connect(tmp_path / "history.db") as conn:
        rows = conn.execute("SELECT session, content FROM turns ORDER BY id").fetchall()
    assert rows == [("sa", "from A"), ("sa", "echo: from A"), ("sb", "from B"), ("sb", "echo: from B")]


def test_memory_failure_on_the_turn_path_exits_nonzero(tmp_path, caplog):
    async def _run():
        rt = _runtime(tmp_path)
        stop = asyncio.Event()
        serving = asyncio.create_task(rt.run_forever(stop))
        while rt._server is None or rt._server._server is None:
            await asyncio.sleep(0.01)
        # The store goes away underneath a running daemon.
        await rt.core.memory.db.close()
        client = await LyraClient.connect("127.0.0.1", rt.server.port)
        try:
            await client.chat("hello", "s1")
        except Exception as exc:  # noqa: BLE001 - the error frame is the assertion below
            err = str(exc)
        else:
            err = None
        await client.close()
        return err, await asyncio.wait_for(serving, 10.0)

    with caplog.at_level(logging.INFO):
        err, code = asyncio.run(_run())
    assert code == 1
    assert err is not None and "stopping" in err
    assert "FATAL" in caplog.text


def test_runtime_refuses_to_start_without_a_store_and_leaves_none_behind(tmp_path):
    rt = Runtime(
        _FakeBackend(),
        db_path=tmp_path / "store.db",
        legacy_db_path=tmp_path / "legacy_memory.db",
        history_path=tmp_path / "history.db",
        port=0,
    )
    with pytest.raises(StoreMissing, match="memory store missing"):
        asyncio.run(rt.run_forever(asyncio.Event()))
    assert not (tmp_path / "store.db").exists()


def test_affect_persists_across_daemon_restarts(tmp_path):
    """Two runtimes in sequence (never at once) share the store and the affect."""
    async def _run():
        rt1 = _runtime(tmp_path)
        await rt1.start()
        c = await LyraClient.connect("127.0.0.1", rt1.server.port)
        for i in range(3):
            await c.chat(f"turn {i}", "s")
        await c.close()
        before = rt1.core.introspect()
        await rt1.stop()

        runtime._core_constructions = 0  # a new process
        rt2 = _runtime(tmp_path)
        await rt2.start()
        after = rt2.core.introspect()
        await rt2.stop()
        return before, after

    before, after = asyncio.run(_run())
    assert (before.valence, before.arousal) != (0.0, 0.0)
    assert after.valence == pytest.approx(before.valence)
    assert after.arousal == pytest.approx(before.arousal)


def test_runtime_cold_start_archives_legacy_memory_db_and_logs_all_three_paths(tmp_path, caplog):
    """DONE-WHEN: a machine with only memory.db present — the daemon archives
    it by rename, creates store.db and runs.db, and starts. All three paths
    appear in the log."""
    legacy = tmp_path / "memory.db"
    _pre_atoms_store(legacy, datetime(2026, 6, 10, 8, 0))

    async def _run():
        rt = _runtime(tmp_path, legacy_db_path=legacy)
        await rt.start()
        await rt.stop()

    with caplog.at_level(logging.INFO):
        asyncio.run(_run())

    archive = tmp_path / "memory.db.2026-06-10.archive"
    assert archive.is_file()
    assert not legacy.exists()
    assert (tmp_path / "store.db").is_file()
    assert (tmp_path / "runs.db").is_file()
    assert str(archive) in caplog.text
    assert str(tmp_path / "store.db") in caplog.text
    assert str(tmp_path / "runs.db") in caplog.text
