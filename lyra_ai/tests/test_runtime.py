"""Tests for lyra_core.runtime — store preparation, the one core, the tick
clock, the daemon-side turn handler, and the Runtime lifecycle over a real
loopback socket.

No LLM: the backend is a fake. No model download: conftest selects the
offline embedder when MiniLM is not cached. Stores are per-test tmp files.
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
from lyra_core.interface import AffectState, CognitiveCore
from lyra_core.runtime import (
    CORE_CONSTRUCTED,
    DT_CLAMP_ENGAGED,
    REQUIRED_TABLES,
    Runtime,
    StoreMissing,
    StoreSchemaMismatch,
    TickClock,
    TurnHandler,
    archive_store,
    check_store_schema,
    construct_core,
    prepare_store,
    store_tables,
)
from lyra_core.transport import TurnRejected


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
    """A store on the schema memory.db was on before atoms existed."""
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
    missing = tmp_path / "memory.db"
    with pytest.raises(StoreMissing, match=str(missing)):
        asyncio.run(prepare_store(missing))
    assert not missing.exists(), "the daemon must not create a store on its own"


def test_init_store_creates_an_empty_store_with_the_required_tables(tmp_path):
    path = tmp_path / "memory.db"
    asyncio.run(prepare_store(path, init_if_missing=True))
    assert REQUIRED_TABLES <= store_tables(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM atoms").fetchone()[0] == 0


def test_pre_atoms_store_is_archived_by_mtime_date_and_replaced(tmp_path, caplog):
    path = tmp_path / "memory.db"
    _pre_atoms_store(path, datetime(2026, 6, 10, 14, 30))
    with pytest.raises(StoreSchemaMismatch, match="atoms"):
        check_store_schema(path)

    with caplog.at_level(logging.INFO, logger="lyra_core.runtime"):
        asyncio.run(prepare_store(path))

    archive = tmp_path / "memory.db.2026-06-10.archive"
    assert archive.is_file(), "archived by rename, named for the store's own mtime"
    with sqlite3.connect(archive) as conn:
        assert conn.execute("SELECT content FROM episodes").fetchone() == ("an old essay",)
    assert REQUIRED_TABLES <= store_tables(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM atoms").fetchone()[0] == 0
    assert str(archive) in caplog.text
    assert len(list(tmp_path.iterdir())) == 2, "nothing deleted, nothing extra"


def test_archive_never_clobbers_an_existing_archive(tmp_path):
    path = tmp_path / "memory.db"
    when = datetime(2026, 6, 10, 9, 5, 7)
    _pre_atoms_store(path, when)
    first = archive_store(path)
    _pre_atoms_store(path, when)
    second = archive_store(path)
    assert first != second
    assert first.is_file() and second.is_file()
    assert second.name == "memory.db.2026-06-10-090507.archive"


def test_archive_carries_wal_sidecars_along(tmp_path):
    path = tmp_path / "memory.db"
    _pre_atoms_store(path, datetime(2026, 6, 10))
    (tmp_path / "memory.db-wal").write_bytes(b"wal")
    (tmp_path / "memory.db-shm").write_bytes(b"shm")
    archive = archive_store(path)
    assert (tmp_path / (archive.name + "-wal")).read_bytes() == b"wal"
    assert (tmp_path / (archive.name + "-shm")).read_bytes() == b"shm"
    assert not (tmp_path / "memory.db-wal").exists()


# ── the one core ─────────────────────────────────────────────────────────────

def test_construct_core_logs_the_marker_once_and_refuses_a_second(caplog):
    memory = MagicMock()
    memory._db_path = Path("/nowhere/memory.db")
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
    """Records ticks; introspects a fixed affect; has no memory to retrieve from."""

    def __init__(self, affect: AffectState | None = None) -> None:
        self.ticks: list[tuple[str, str, float]] = []
        self._affect = affect or AffectState()
        self.memory = MagicMock()

    async def tick(self, observations, dt=0.1):
        for obs in observations:
            self.ticks.append((obs.source, obs.content, dt))
        return [], self._affect

    def introspect(self) -> AffectState:
        return self._affect


def _handler(backend, core=None, vision=None, history=None, now=None):
    core = core or _FakeCore()
    history = history if history is not None else MagicMock(get_history=MagicMock(return_value=[]))
    clock = TickClock(max_dt=0.1, now=now or time.time)
    return TurnHandler(core, backend, history, clock, vision_fn=vision or MagicMock()), core, history


def _no_context():
    return patch("lyra_core.runtime.retrieval.build_context", new=AsyncMock(return_value=""))


def test_handle_writes_history_and_ticks_user_then_lyra():
    backend = _FakeBackend(["hi there"])
    handler, core, history = _handler(backend)
    with _no_context():
        reply = asyncio.run(handler.handle("hello", "s1"))
    assert reply == "hi there"
    assert history.add.call_args_list == [call("s1", "user", "hello"), call("s1", "assistant", "hi there")]
    assert [(s, c) for s, c, _ in core.ticks] == [("conversation", "hello"), ("lyra", "hi there")]


def test_handle_calls_vision_and_reprompts():
    backend = _FakeBackend(["[TOOL:see:screen]", "You have a Python file open."])
    vision = MagicMock(return_value="A code editor with Python open.")
    handler, core, history = _handler(backend, vision=vision)
    with _no_context():
        reply = asyncio.run(handler.handle("what's on my screen?", "s1"))
    assert reply == "You have a Python file open."
    vision.assert_called_once_with("screen")
    history.add.assert_any_call("s1", "assistant", "[TOOL:see:screen]")
    history.add.assert_any_call("s1", "user", "[Vision result: A code editor with Python open.]")
    assert ("vision", "A code editor with Python open.", 0.1) in [(s, c, round(d, 3)) for s, c, d in core.ticks] or \
        any(s == "vision" for s, _, _ in core.ticks)


def test_handle_webcam_source():
    backend = _FakeBackend(["[TOOL:see:webcam]", "I see you."])
    vision = MagicMock(return_value="A person at a desk.")
    handler, _, _ = _handler(backend, vision=vision)
    with _no_context():
        assert asyncio.run(handler.handle("what do you see?", "s1")) == "I see you."
    vision.assert_called_once_with("webcam")


def test_handle_caps_vision_at_three_attempts():
    backend = _FakeBackend(["[TOOL:see:screen]"] * 3)
    vision = MagicMock(return_value="still a screen")
    handler, _, history = _handler(backend, vision=vision)
    with _no_context():
        reply = asyncio.run(handler.handle("what's on my screen?", "s1"))
    assert len(backend.calls) == 3 and vision.call_count == 3
    assert "unable to determine" in reply
    assert history.add.call_args_list.count(call("s1", "assistant", "[TOOL:see:screen]")) == 3
    history.add.assert_any_call("s1", "assistant", reply)


def test_handle_discards_confabulated_text_after_the_tool_call_and_keeps_preamble():
    backend = _FakeBackend([
        "Let me look.\n[TOOL:see:screen]\nThe screen shows 'Hello Lyra'.",
        "Done.",
    ])
    vision = MagicMock(return_value="A terminal window.")
    handler, _, history = _handler(backend, vision=vision)
    with _no_context():
        assert asyncio.run(handler.handle("look", "s1")) == "Done."
    stored = [c.args[2] for c in history.add.call_args_list]
    assert "Let me look.\n[TOOL:see:screen]" in stored
    assert not any("Hello Lyra" in s for s in stored)


def test_backend_failure_is_rejected_not_fatal():
    backend = _FakeBackend(fail=ConnectionRefusedError("ollama down"))
    handler, core, history = _handler(backend)
    with _no_context():
        with pytest.raises(TurnRejected, match="ollama down"):
            asyncio.run(handler.handle("hello", "s1"))
    # The user turn was still recorded before the model was asked.
    history.add.assert_called_once_with("s1", "user", "hello")
    assert [s for s, _, _ in core.ticks] == ["conversation"]


def test_context_failure_propagates_instead_of_degrading_to_layer1():
    """The old _get_system swallowed build_context errors and quietly served
    Layer 1 alone. Now the failure is the turn's failure."""
    handler, _, _ = _handler(_FakeBackend(["never"]))
    with patch("lyra_core.runtime.retrieval.build_context", new=AsyncMock(side_effect=RuntimeError("store gone"))):
        with pytest.raises(RuntimeError, match="store gone"):
            asyncio.run(handler.handle("hello", "s1"))


def test_system_prompt_is_layer1_plus_context_plus_hint():
    negative = AffectEngine.from_dict({
        "accum_rate": 1.0, "emotion_decay": 2.0, "mood_drift": 0.2,
        "emotion_v": -0.6, "emotion_a": 0.6, "mood_v": 0.0, "mood_a": 0.0,
    }).state
    handler, _, _ = _handler(_FakeBackend(), core=_FakeCore(affect=negative))
    with patch("lyra_core.runtime.retrieval.build_context", new=AsyncMock(return_value="## Persona Traits\n- curiosity")):
        system = asyncio.run(handler.system_prompt("hello"))
    assert system.startswith(LAYER1_FACTS)
    assert "curiosity" in system
    assert "Keep responses brief and direct. Don't soften or elaborate." in system


def test_system_prompt_is_layer1_alone_when_neutral_and_empty():
    handler, _, _ = _handler(_FakeBackend())
    with _no_context():
        assert asyncio.run(handler.system_prompt("hello")) == LAYER1_FACTS


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

def _runtime(tmp_path, backend=None, **kw) -> Runtime:
    return Runtime(
        backend or _FakeBackend(),
        db_path=tmp_path / "memory.db",
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
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        atoms = conn.execute("SELECT role, content FROM atoms ORDER BY id").fetchall()
        affect = conn.execute("SELECT value FROM facts WHERE key = 'affect_state'").fetchone()
    assert atoms == [("user", "hello"), ("lyra", "echo: hello")]
    assert affect is not None, "affect persisted on clean stop"


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
    rt = Runtime(_FakeBackend(), db_path=tmp_path / "memory.db", history_path=tmp_path / "history.db", port=0)
    with pytest.raises(StoreMissing, match="memory store missing"):
        asyncio.run(rt.run_forever(asyncio.Event()))
    assert not (tmp_path / "memory.db").exists()


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
