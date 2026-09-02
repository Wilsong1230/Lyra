"""lyra_core.runtime — the daemon. Sole owner of the one CognitiveCore and the one store.

CP-A. One long-lived process constructs exactly one CognitiveCore, opens the
one memory store and the one history store, and answers turns that arrive
over lyra_core.transport. The `lyra` CLI is a client of this process and
constructs no cognition of its own.

Lifecycle:
  start : prepare the store (present? right schema? archive-and-recreate if
          not) -> construct the core (CORE_CONSTRUCTED) -> start it (memory,
          dreaming loop, affect restore) -> open history -> listen
  stop  : stop listening (no new turns) -> stop the core (affect persisted,
          store closed)

Ticks happen when a turn arrives, not on a timer. dt is wall-clock seconds
since the previous tick, clamped to config.MAX_TICK_DT_SECONDS — see the
note there for why the clamp is what it is. tick() still returns intents;
they are bound and left unconsumed, as CP-A requires.

Every tick logs one structured INFO line (TurnHandler.tick): raw elapsed,
applied dt, whether the clamp engaged, both drives' pressure, and the
emotion/mood/temperament axes — key=value, stable field names, `grep
DT_CLAMP_ENGAGED` still finds the clamped ones (CP-A.1: instrumentation
only, see tools/affect_probe.py and docs/AFFECT_CHARACTERIZATION.md for
what the numbers mean offline).

Failure policy: a missing store, a store whose schema cannot be made right,
a sqlite-vec that will not load, a backend that cannot be selected — each
ends the process with a nonzero exit and a log line naming the cause. On
the turn path, a backend (LLM) failure is answered with an error frame and
the daemon stays up; any failure in memory, the core, or history is fatal.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlparse

from lyra.assistant import DEFAULT_SYSTEM
from lyra.backends import Backend
from lyra.memory import ConversationMemory
from lyra_core.config import DAEMON_HOST, DAEMON_PORT, MAX_TICK_DT_SECONDS
from lyra_core.expression import prose_hint
from lyra_core.interface import AffectVector, CognitiveCore, Observation, ObservationKind
from lyra_core.transport import TurnRejected, TurnServer
from lyra_memory import MemorySystem, retrieval
from lyra_memory.config import DB_PATH
from lyra_memory.db import init_db

log = logging.getLogger("lyra_core.runtime")

# The literal marker. `grep CORE_CONSTRUCTED` on the log must find exactly one.
CORE_CONSTRUCTED = "CORE_CONSTRUCTED"
DT_CLAMP_ENGAGED = "DT_CLAMP_ENGAGED"

# Tables the hot path and the current retrieval queries reference:
#   facts     — affect_state restore/persist (CognitiveCore.start/stop)
#   atoms     — every turn is written here (MemorySystem.add_turn)
#   vec_atoms — its embedding, and retrieval.search_episodes' KNN
#   traits    — retrieval.build_context via IdentityEngine.get_top_traits
# A store missing any of these is on a schema this code does not write to.
REQUIRED_TABLES: frozenset[str] = frozenset({"atoms", "vec_atoms", "facts", "traits"})

VISION_URL = os.getenv("VISION_URL", "http://localhost:8003")

# The exact tokens the Layer 1 prompt tells her to emit, mapped to vision sources.
_TOOL_TOKENS = {
    "[TOOL:see:screen]": "screen",
    "[TOOL:see:webcam]": "webcam",
}
_VISION_ATTEMPTS = 3
_VISION_FALLBACK = "I was unable to determine what you're looking at after several attempts."


# ── startup errors ───────────────────────────────────────────────────────────

class StartupError(RuntimeError):
    """The daemon cannot start; the message names the specific cause."""


class StoreMissing(StartupError):
    pass


class StoreSchemaMismatch(StartupError):
    def __init__(self, path: Path, missing: set[str]) -> None:
        self.path = path
        self.missing = missing
        super().__init__(
            f"memory store {path} is missing tables {sorted(missing)}; "
            f"required: {sorted(REQUIRED_TABLES)}"
        )


# ── store preparation ────────────────────────────────────────────────────────

def store_tables(path: Path) -> set[str]:
    """Names of every table (virtual tables included) in the store, read-only."""
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def check_store_schema(path: Path) -> None:
    """Raise StoreMissing or StoreSchemaMismatch; return None when the store is usable."""
    if not path.is_file():
        raise StoreMissing(
            f"memory store missing: {path} — the daemon does not create a store on its own; "
            "run `python -m lyra_core --init-store` once to create an empty one deliberately"
        )
    missing = REQUIRED_TABLES - store_tables(path)
    if missing:
        raise StoreSchemaMismatch(path, missing)


def archive_store(path: Path) -> Path:
    """Rename the store (and its -wal/-shm sidecars) out of the way. Never deletes.

    The suffix is the store's own last-modified date, so the archive name
    says when the data in it was last touched rather than when it was moved.
    """
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    target = path.with_name(f"{path.name}.{stamp}.archive")
    if target.exists():
        # Two archives from the same day. Disambiguate rather than clobber.
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d-%H%M%S")
        target = path.with_name(f"{path.name}.{stamp}.archive")
        if target.exists():
            raise StartupError(f"archive target already exists: {target}")
    # Sidecars are renamed to the names SQLite derives from the archive's own
    # name, so opening the archive later still applies its WAL.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.rename(target.with_name(target.name + suffix))
    path.rename(target)
    return target


async def _create_empty_store(path: Path) -> None:
    conn = await init_db(path)
    await conn.close()


async def prepare_store(path: Path, init_if_missing: bool = False) -> None:
    """Make `path` a store the core can own, or raise StartupError saying why not.

    Missing store: fatal, unless `init_if_missing` (the --init-store flag),
    which is the one deliberate way to bring a store into existence.
    Wrong schema: archive by rename, create a fresh empty store, re-check.
    """
    if not path.is_file() and init_if_missing:
        path.parent.mkdir(parents=True, exist_ok=True)
        await _create_empty_store(path)
        log.info("created empty memory store at %s (--init-store)", path)

    try:
        check_store_schema(path)
    except StoreSchemaMismatch as exc:
        log.warning("%s", exc)
        archived = archive_store(path)
        log.warning("archived old memory store to %s", archived)
        await _create_empty_store(path)
        log.info("created fresh empty memory store at %s", path)
        check_store_schema(path)  # a fresh store that still fails is a real bug
    log.info("memory store ok: %s (tables: %s)", path, ", ".join(sorted(REQUIRED_TABLES)))


# ── the one core ─────────────────────────────────────────────────────────────

_core_constructions = 0


def construct_core(memory: MemorySystem) -> CognitiveCore:
    """Construct THE CognitiveCore. A second call in the same process is a bug."""
    global _core_constructions
    if _core_constructions >= 1:
        raise RuntimeError(
            "CognitiveCore already constructed in this process; the daemon owns exactly one"
        )
    core = CognitiveCore(memory=memory)
    _core_constructions += 1
    log.info("%s pid=%d store=%s", CORE_CONSTRUCTED, os.getpid(), memory._db_path)
    return core


# ── tick clock ───────────────────────────────────────────────────────────────

class TickClock:
    """Wall-clock dt between ticks, clamped.

    Wall-clock (time.time) rather than monotonic: a laptop asleep overnight
    does not advance the monotonic clock on every platform, and the gap that
    matters here is exactly the one the machine slept through.
    """

    def __init__(self, max_dt: float = MAX_TICK_DT_SECONDS, now: Callable[[], float] = time.time) -> None:
        self._max_dt = max_dt
        self._now = now
        self._last = now()

    def next_dt(self) -> tuple[float, float, bool]:
        """Return (elapsed, dt, clamped) and mark this instant as the last tick."""
        t = self._now()
        elapsed = t - self._last
        self._last = t
        if elapsed < 0.0:
            # The wall clock stepped backwards. No time passed that we can vouch for.
            log.warning("wall clock moved backwards by %.1fs; using dt=0", -elapsed)
            return elapsed, 0.0, False
        if elapsed > self._max_dt:
            return elapsed, self._max_dt, True
        return elapsed, elapsed, False


# ── vision (the daemon calls lyra-vision; the client never sees the tool loop) ──

def _post_json(url: str, body: dict, timeout: float) -> dict:
    parsed = urlparse(url)
    conn = HTTPConnection(parsed.netloc, timeout=timeout)
    conn.request("POST", parsed.path, body=json.dumps(body).encode(),
                 headers={"Content-Type": "application/json"})
    raw = conn.getresponse().read()
    return json.loads(raw)


def call_vision(source: str) -> str:
    """Ask lyra-vision to look. Unavailability is reported AS the observation:
    she asked to see and could not, and that is what goes on the record."""
    try:
        result = _post_json(f"{VISION_URL}/see",
                            {"source": source, "prompt": "Describe what you see in detail."},
                            timeout=30.0)
        return result.get("description", "Error: empty vision response")
    except Exception as exc:
        return f"Error: vision service unavailable — {exc}"


# ── turn handler ─────────────────────────────────────────────────────────────

class TurnHandler:
    """Daemon-side conversation: history, prompt assembly, the vision tool loop.

    Exactly what Assistant.chat_with_tools did in the CLI process, now run
    where the core lives. Every write here — history.db, the atom store via
    tick() — happens in the daemon.
    """

    def __init__(
        self,
        core: CognitiveCore,
        backend: Backend,
        history: ConversationMemory,
        clock: TickClock,
        vision_fn: Callable[[str], str] = call_vision,
        system: str = DEFAULT_SYSTEM,
    ) -> None:
        self._core = core
        self._backend = backend
        self._history = history
        self._clock = clock
        self._vision_fn = vision_fn
        self._system = system
        # Bound, not executed. CP-A leaves tick() output unconsumed.
        self.last_intents: list = []

    async def tick(self, source: str, content: str) -> None:
        obs = Observation(kind=ObservationKind.sensory, source=source, content=content)
        elapsed, dt, clamped = self._clock.next_dt()
        self.last_intents, affect = await self._core.tick([obs], dt)

        # CognitiveCore.tick() returns only (intents, affect) — interface.py
        # is outside this checkpoint's closed file set, so drive pressure is
        # read off the core's own drive objects rather than adding a new
        # accessor there. See DECISIONS.md (CP-A.1).
        boredom_pressure = getattr(getattr(self._core, "_boredom", None), "pressure", float("nan"))
        relational_pressure = getattr(getattr(self._core, "_relational", None), "pressure", float("nan"))

        emotion = affect.emotion
        mood = affect.mood if affect.mood is not None else AffectVector()
        temperament = affect.temperament if affect.temperament is not None else AffectVector()
        temperament_c = temperament.control if temperament.control is not None else float("nan")

        log.info(
            "%s source=%s elapsed=%.6f dt=%.6f clamped=%s "
            "boredom_pressure=%.6f relational_pressure=%.6f "
            "emotion_v=%.6f emotion_a=%.6f mood_v=%.6f mood_a=%.6f "
            "temperament_v=%.6f temperament_a=%.6f temperament_c=%.6f",
            DT_CLAMP_ENGAGED if clamped else "tick",
            source, elapsed, dt, clamped,
            boredom_pressure, relational_pressure,
            emotion.valence, emotion.arousal, mood.valence, mood.arousal,
            temperament.valence, temperament.arousal, temperament_c,
        )

    async def system_prompt(self, query: str) -> str:
        # Unguarded on purpose. A broken store and an empty store must not
        # produce the same prompt; the failure surfaces as a fatal turn.
        parts = [self._system]
        context = await retrieval.build_context(self._core.memory, query=query)
        if context:
            parts.append(context)
        hint = prose_hint(self._core.introspect())
        if hint:
            parts.append(hint)
        return "\n\n".join(parts)

    async def _complete(self, session: str, system: str) -> str:
        history = self._history.get_history(session)
        try:
            # http.client blocks; keep the event loop free for other clients'
            # frames and the dreaming loop. Turns are still serialized by the
            # transport's single worker.
            return await asyncio.to_thread(self._backend.chat, history, system=system)
        except Exception as exc:
            # The model did not answer. The daemon is fine; this turn is not.
            log.error("backend %s failed: %s", self._backend.name, exc)
            raise TurnRejected(f"backend {self._backend.name} failed: {exc}") from exc

    async def handle(self, message: str, session: str) -> str:
        # The user turn is recorded BEFORE the prompt is assembled so that
        # retrieval keys off this message and affect reflects it.
        self._history.add(session, "user", message)
        await self.tick("conversation", message)
        system = await self.system_prompt(message)
        for _ in range(_VISION_ATTEMPTS):
            response = await self._complete(session, system)
            source = parse_tool_call(response)
            if source is None:
                self._history.add(session, "assistant", response)
                await self.tick("lyra", response)
                return response
            self._history.add(session, "assistant", truncate_at_tool_call(response))
            description = await asyncio.to_thread(self._vision_fn, source)
            await self.tick("vision", description)
            self._history.add(session, "user", f"[Vision result: {description}]")
        self._history.add(session, "assistant", _VISION_FALLBACK)
        await self.tick("lyra", _VISION_FALLBACK)
        return _VISION_FALLBACK


def parse_tool_call(response: str) -> str | None:
    for line in response.splitlines():
        source = _TOOL_TOKENS.get(line.strip())
        if source is not None:
            return source
    return None


def truncate_at_tool_call(response: str) -> str:
    """Keep everything up to and including the tool-call line; drop the rest.

    A model that ignores "on its own line and nothing else" will emit the
    token and then invent the result it has not seen yet. Once that invented
    text is in the transcript it is indistinguishable from a real
    observation, and it is replayed to the model as her own words on every
    later turn. Text BEFORE the token is kept: "Let me look." is a legitimate
    preamble. Text after it is discarded; the real result arrives next turn.
    """
    lines = response.splitlines()
    for i, line in enumerate(lines):
        if line.strip() in _TOOL_TOKENS:
            return "\n".join(lines[: i + 1]).strip()
    return response


# ── runtime ──────────────────────────────────────────────────────────────────

class Runtime:
    """The daemon process: one core, one store, one listener."""

    def __init__(
        self,
        backend: Backend,
        *,
        db_path: Path | None = None,
        history_path: Path | None = None,
        host: str = DAEMON_HOST,
        port: int = DAEMON_PORT,
        init_store: bool = False,
        vision_fn: Callable[[str], str] = call_vision,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._backend = backend
        self._db_path = db_path or DB_PATH
        self._history_path = history_path
        self._host = host
        self._port = port
        self._init_store = init_store
        self._vision_fn = vision_fn
        self._now = now
        self._core: CognitiveCore | None = None
        self._handler: TurnHandler | None = None
        self._server: TurnServer | None = None

    @property
    def core(self) -> CognitiveCore:
        if self._core is None:
            raise RuntimeError("Runtime has not been started")
        return self._core

    @property
    def server(self) -> TurnServer:
        if self._server is None:
            raise RuntimeError("Runtime has not been started")
        return self._server

    async def start(self) -> None:
        await prepare_store(self._db_path, init_if_missing=self._init_store)

        self._core = construct_core(MemorySystem(db_path=self._db_path))
        await self._core.start()  # init_db, dreaming loop, embed warm-up, affect restore; raises on any failure
        log.info("core started; store open at %s", self._db_path)

        history = ConversationMemory(self._history_path)
        log.info("history store open at %s", history.db_path)

        self._handler = TurnHandler(
            core=self._core,
            backend=self._backend,
            history=history,
            clock=TickClock(now=self._now),
            vision_fn=self._vision_fn,
        )
        self._server = TurnServer(self._handler.handle, host=self._host, port=self._port)
        await self._server.start()
        log.info(
            "daemon ready: backend=%s model=%s listening on %s:%d",
            self._backend.name, self._backend.default_model, self._host, self._server.port,
        )

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.stop()
        if self._core is not None:
            await self._core.stop()
            log.info("store closed cleanly: %s", self._db_path)

    async def run_forever(self, stop: asyncio.Event | None = None) -> int:
        """Start, serve until `stop` is set or the turn path fails, tear down.

        Returns the process exit code: 0 on a requested stop, 1 when the
        turn worker died — the cause is logged before teardown.
        """
        if stop is None:
            stop = asyncio.Event()
        try:
            await self.start()
        except BaseException:
            # Whatever did start (a store opened before the listener failed
            # to bind, say) is closed before the cause propagates.
            await self.stop()
            raise
        stop_task = asyncio.create_task(stop.wait(), name="lyra-stop-wait")
        fatal = self.server.fatal
        try:
            await asyncio.wait({stop_task, fatal}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not stop_task.done():
                stop_task.cancel()
        if not fatal.done():
            log.info("stop requested")
            await self.stop()
            return 0

        exc = fatal.exception()
        log.critical("FATAL: turn path failed: %s", exc, exc_info=exc)
        try:
            # Best effort: the thing that failed may be the store itself.
            await self.stop()
        except Exception as close_exc:
            log.error("store could not be closed cleanly after the failure: %s", close_exc)
        log.critical("FATAL: exiting 1: %s", exc)
        return 1
