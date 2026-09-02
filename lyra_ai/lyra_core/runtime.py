"""lyra_core.runtime — the daemon. Sole owner of the one CognitiveCore and the one store.

CP-A. One long-lived process constructs exactly one CognitiveCore, opens the
one memory store and the one history store, and answers turns that arrive
over lyra_core.transport. The `lyra` CLI is a client of this process and
constructs no cognition of its own.

CP-B: the one memory store is lyra_memory.store.Store (store.db + runs.db),
not the older lyra_memory.MemorySystem (memory.db). MemorySystem,
lyra_memory.retrieval, and lyra_memory.db keep working and keep their tests —
they are simply never imported on this path again. See DECISIONS.md.

CP-C: the daemon also emits one structured SELF_REPORT log line at startup
and every REPORT_INTERVAL_SECONDS after — the same measurements, the same
field names, as `python -m lyra_core --report` (lyra_core/report.py). A
separate asyncio task, never awaited by the turn path; a failure collecting
it is logged and the daemon stays up (see Runtime._emit_self_report).

Lifecycle:
  start : archive any leftover pre-CP-B memory.db (one-time courtesy) ->
          prepare the store (present? right schema? archive-and-recreate if
          not) -> open it (Store.open) -> warm the embedder (fatal if the
          model is missing and no offline opt-in is set) -> construct the
          core (CORE_CONSTRUCTED) -> start it (affect restore) -> open
          history -> listen -> start the self-report task
  stop  : cancel the self-report task -> stop listening (no new turns) ->
          stop the core (affect persisted) -> close the store

CP-D: the loop closes. CognitiveCore.tick() can now propose an
IntentKind.retrieval intent (interface.py, action_selection.py) for the
turn in progress; TurnHandler.handle() below executes it instead of
discarding it — retrieve_context(), an outcomes row per execution
(record_retrieval_outcome), and a minimal consolidator firing
(consolidate_retrieval_outcome) that may write one `candidates` row but
never promotes a trait. Every step logs one line on a stable marker
(INTENT_PRODUCED/EXECUTED/DECLINED, OUTCOME_RECORDED, CONSOLIDATOR_FIRED,
CANDIDATE_CREATED), all sharing one per-turn id. Every OTHER intent kind
(look, speak, noop) tick() may still produce is still bound and left
unexecuted — CP-D closes the loop for retrieval only.

Ticks happen when a turn arrives, not on a timer. dt is wall-clock seconds
since the previous tick, clamped to config.MAX_TICK_DT_SECONDS — see the
note there for why the clamp is what it is.

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
from lyra_core.config import DAEMON_HOST, DAEMON_PORT, MAX_TICK_DT_SECONDS, REPORT_INTERVAL_SECONDS
from lyra_core.expression import prose_hint
from lyra_core.interface import AffectVector, CognitiveCore, IntentKind, Observation, ObservationKind
from lyra_core.report import UNAVAILABLE, classify_misses, collect_from_path, format_log_line, path_label
from lyra_core.transport import TurnRejected, TurnServer
from lyra_memory.config import DB_PATH, EMBED_MODEL, STORE_PATH
from lyra_memory.embeddings import embed
from lyra_memory.store import Store

log = logging.getLogger("lyra_core.runtime")

# The literal marker. `grep CORE_CONSTRUCTED` on the log must find exactly one.
CORE_CONSTRUCTED = "CORE_CONSTRUCTED"
DT_CLAMP_ENGAGED = "DT_CLAMP_ENGAGED"

# CP-D: the intent loop, one marker per step, all sharing one per-turn id
# (TurnHandler._turn_seq) so `grep` on any one of them and following the
# turn= value finds the whole sequence for that turn.
INTENT_PRODUCED = "INTENT_PRODUCED"
INTENT_EXECUTED = "INTENT_EXECUTED"
INTENT_DECLINED = "INTENT_DECLINED"
OUTCOME_RECORDED = "OUTCOME_RECORDED"
CONSOLIDATOR_FIRED = "CONSOLIDATOR_FIRED"
CANDIDATE_CREATED = "CANDIDATE_CREATED"

# Tables the hot path and the current retrieval queries reference (CP-B —
# Store's schema, lyra_memory/store/schema.py, not the older db.py):
#   atoms       — every turn is written here (CognitiveCore._ingest_sensory)
#   atoms_fts   — its lexical index, store.context._lexical_hits
#   vec_atoms   — its embedding, store.context._semantic_hits
#   facts       — affect_state restore/persist AND store.context._facts_block
#   commitments — store.context._commitments_block
#   outcomes    — CP-D: one row per executed retrieval intent
#                 (CognitiveCore.record_retrieval_outcome)
#   traits      — store.context._traits_block
#   candidates  — CP-D: one row per consolidator firing on a retrieval
#                 outcome (CognitiveCore.consolidate_retrieval_outcome) —
#                 never promoted to traits (OUT OF SCOPE)
# Named, not the old memory.db's table names verbatim, so a pre-CP-B
# memory.db (which lacks atoms_fts, commitments, and outcomes) can never
# satisfy this check by accident — see DECISIONS.md (CP-B, change 3).
REQUIRED_TABLES: frozenset[str] = frozenset({
    "atoms", "atoms_fts", "vec_atoms", "facts", "commitments", "outcomes",
    "traits", "candidates",
})

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
    store = await Store.open(path)
    await store.close()


def archive_legacy_memory_db(legacy_path: Path = DB_PATH) -> Path | None:
    """One-time courtesy (CP-B): a leftover pre-CP-B memory.db is archived by
    rename so it cannot be mistaken for the live store. Never opened again.

    Idempotent: after the first call renames it away, legacy_path.is_file()
    is False and every later call is a no-op. Independent of the store path
    itself — DB_PATH and STORE_PATH are always different files.

    `legacy_path` defaults to the real DB_PATH but is a parameter, not a
    hardcoded read of Path.home(), specifically so a test using a tmp_path
    store can point this at a tmp_path memory.db instead — a hardcoded
    Path.home() here would let any test that exercises prepare_store rename
    a real user's actual ~/.lyra/memory.db out from under them.
    """
    if not legacy_path.is_file():
        return None
    archived = archive_store(legacy_path)
    log.warning("archived legacy memory.db (pre-CP-B, no longer opened) to %s", archived)
    return archived


async def prepare_store(
    path: Path, init_if_missing: bool = False, legacy_path: Path = DB_PATH
) -> None:
    """Make `path` a store the core can own, or raise StartupError saying why not.

    Missing store: fatal, unless `init_if_missing` (the --init-store flag),
    which is the one deliberate way to bring a store into existence.
    Wrong schema: archive by rename, create a fresh empty store, re-check.
    """
    archive_legacy_memory_db(legacy_path)

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


def construct_core(memory: Store) -> CognitiveCore:
    """Construct THE CognitiveCore. A second call in the same process is a bug."""
    global _core_constructions
    if _core_constructions >= 1:
        raise RuntimeError(
            "CognitiveCore already constructed in this process; the daemon owns exactly one"
        )
    core = CognitiveCore(memory=memory)
    _core_constructions += 1
    log.info("%s pid=%d store=%s", CORE_CONSTRUCTED, os.getpid(), memory.path)
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
    tick()/ingest_exchange() — happens in the daemon.

    CP-D.0: retrieval and the atom+context_log write are both owned by
    CognitiveCore now (retrieve_context()/ingest_exchange() — see
    interface.py), not called on Store directly from here. handle() fetches
    context once per exchange, reuses it for the prompt across every vision
    retry, and hands it back to ingest_exchange() at the end so what gets
    logged is exactly what was used, not a second computation of it.
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
        # Bound; look/speak/noop are still left unexecuted (CP-D closes the
        # loop for retrieval only). handle() reads this after every
        # "conversation" tick to see whether retrieval was produced.
        self.last_intents: list = []
        # CP-D: the shared identifier tying one turn's log lines together —
        # a plain per-process counter, not a store id (nothing durable needs
        # to reference it; it only has to be unique within one daemon's
        # log). Safe under concurrent turns: incremented in one statement
        # with no `await` inside it, so no other coroutine can interleave.
        self._turn_seq: int = 0

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

    def _compose_system_prompt(self, context) -> str:
        """LAYER1 + whatever retrieve_context() returned + the affect hint.

        Takes an already-fetched ContextResult (CP-D.0) rather than a query
        string and fetching it here: handle() needs that same ContextResult
        again afterward, for ingest_exchange() — fetching it twice would
        both double the retrieval cost and risk the two calls disagreeing.
        `context` is None when the turn declined to retrieve (CP-D change
        3) — the prompt then carries no retrieved context at all, same as
        an empty ContextResult would, just without ever having asked.
        Unguarded on purpose, same as before: a broken store and an empty
        store must not produce the same prompt.
        """
        parts = [self._system]
        if context is not None and context.text:
            parts.append(context.text)
        hint = prose_hint(self._core.introspect())
        if hint:
            parts.append(hint)
        return "\n\n".join(parts)

    def _retrieval_path(self, context) -> tuple[int, str]:
        """(atom_count, path) for one ContextResult — the same shape both
        INTENT_EXECUTED's log line and record_retrieval_outcome() need."""
        atom_count = len(context.atom_ids)
        vector_hit, fts_hit = classify_misses(context.misses)
        return atom_count, path_label(vector_hit, fts_hit)

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
        # The user turn is recorded to history BEFORE the prompt is
        # assembled so that affect reflects it; retrieval (retrieve_context)
        # runs before EITHER of this exchange's atoms exist in the store —
        # CP-D.0: no self-matching to exclude, because there is nothing yet
        # to match. tick("conversation", ...) still advances drives/affect
        # for the user's message arriving; it no longer writes an atom
        # (interface.py's _ingest_sensory) — ingest_exchange() at the end
        # does, for both halves of the exchange, atomically.
        self._turn_seq += 1
        turn_id = self._turn_seq

        self._history.add(session, "user", message)
        await self.tick("conversation", message)

        # CP-D: the core decided (CognitiveCore.tick -> ActionSelector) —
        # this reads that decision off last_intents rather than deciding
        # anything itself. Producing and executing are one step apart here
        # (nothing currently separates them), but logged as two distinct
        # markers per change 6.
        retrieval_intended = any(i.kind == IntentKind.retrieval for i in self.last_intents)
        if retrieval_intended:
            log.info("%s turn=%d kind=retrieval", INTENT_PRODUCED, turn_id)
            context = await self._core.retrieve_context(message)
            atom_count, path = self._retrieval_path(context)
            log.info(
                "%s turn=%d kind=retrieval atom_count=%d path=%s",
                INTENT_EXECUTED, turn_id, atom_count, path,
            )
        else:
            # Declined: frustration outweighed the retrieval pressure this
            # tick (action_selection.py) — valence is logged as the
            # legible reason, not a re-derivation of the flip's arithmetic.
            log.info(
                "%s turn=%d kind=retrieval valence=%.6f",
                INTENT_DECLINED, turn_id, self._core.introspect().valence,
            )
            context = None

        system = self._compose_system_prompt(context)
        for _ in range(_VISION_ATTEMPTS):
            response = await self._complete(session, system)
            source = parse_tool_call(response)
            if source is None:
                self._history.add(session, "assistant", response)
                await self.tick("lyra", response)
                await self._finish_exchange(message, response, context, session, turn_id, retrieval_intended)
                return response
            self._history.add(session, "assistant", truncate_at_tool_call(response))
            description = await asyncio.to_thread(self._vision_fn, source)
            await self.tick("vision", description)
            self._history.add(session, "user", f"[Vision result: {description}]")
        self._history.add(session, "assistant", _VISION_FALLBACK)
        await self.tick("lyra", _VISION_FALLBACK)
        await self._finish_exchange(message, _VISION_FALLBACK, context, session, turn_id, retrieval_intended)
        return _VISION_FALLBACK

    async def _finish_exchange(
        self, message: str, response: str, context, session: str, turn_id: int,
        retrieval_intended: bool,
    ) -> None:
        """ingest_exchange() always; the outcome/consolidator steps only
        when a retrieval intent actually executed this turn (change 4:
        "one outcome row per EXECUTED retrieval intent" — a declined turn
        produced neither an intent to execute nor context to score)."""
        user_atom_id, _lyra_atom_id, context_log_id = await self._core.ingest_exchange(
            message, response, context, session_id=session)

        if not retrieval_intended:
            return

        atom_count, path = self._retrieval_path(context)
        outcome_id = await self._core.record_retrieval_outcome(
            user_atom_id, context_log_id, atom_count, path)
        if outcome_id is None:
            return
        log.info(
            "%s turn=%d outcome_id=%d atom_count=%d path=%s context_log_id=%s",
            OUTCOME_RECORDED, turn_id, outcome_id, atom_count, path, context_log_id,
        )

        consolidated = await self._core.consolidate_retrieval_outcome(atom_count > 0)
        log.info("%s turn=%d outcome_id=%d", CONSOLIDATOR_FIRED, turn_id, outcome_id)
        if consolidated is not None:
            trait_name, _trait_value = consolidated
            log.info("%s turn=%d trait_name=%r", CANDIDATE_CREATED, turn_id, trait_name)


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
        legacy_db_path: Path = DB_PATH,
        history_path: Path | None = None,
        host: str = DAEMON_HOST,
        port: int = DAEMON_PORT,
        init_store: bool = False,
        vision_fn: Callable[[str], str] = call_vision,
        now: Callable[[], float] = time.time,
        report_interval: float = REPORT_INTERVAL_SECONDS,
    ) -> None:
        self._backend = backend
        self._db_path = db_path or STORE_PATH
        self._legacy_db_path = legacy_db_path
        self._history_path = history_path
        self._host = host
        self._port = port
        self._init_store = init_store
        self._vision_fn = vision_fn
        self._now = now
        self._report_interval = report_interval
        self._core: CognitiveCore | None = None
        self._handler: TurnHandler | None = None
        self._server: TurnServer | None = None
        self._report_task: asyncio.Task | None = None

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
        await prepare_store(
            self._db_path, init_if_missing=self._init_store, legacy_path=self._legacy_db_path
        )

        store = await Store.open(self._db_path)
        try:
            await embed("warmup")
        except Exception as exc:
            # Fatal, not a fallback (CP-B change 8): a daemon that silently
            # switched embedders would write vectors no later query can find,
            # with no symptom until retrieval quietly returns nothing.
            await store.close()
            raise StartupError(
                f"embedding model unavailable ({EMBED_MODEL}): {exc}. Cache the "
                "real model before starting the daemon, or set "
                "LYRA_EMBED_BACKEND=hashed to run offline deliberately."
            ) from exc
        log.info("runs store open at %s", store.runs_path)

        self._core = construct_core(store)
        await self._core.start()  # affect restore; raises on any failure
        log.info("core started; store open at %s", self._db_path)

        history = ConversationMemory(self._history_path)
        # Resolve None -> ConversationMemory's own default now, so the
        # self-report task (change 5) can report history_db_bytes without
        # re-deriving that default itself.
        self._history_path = history.db_path
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

        # CP-C change 5: a separate task, not awaited here — the turn queue
        # is never blocked waiting on telemetry. Emits once immediately
        # (the startup line) then every self._report_interval seconds.
        self._report_task = asyncio.create_task(self._self_report_loop(), name="lyra-self-report")

    async def _emit_self_report(self) -> None:
        """Collect and log one SELF_REPORT line, through the exact same
        read-only path `python -m lyra_core --report` uses
        (collect_from_path — a fresh mode=ro connection, opened and closed
        here, never the daemon's own live write connection). Two reasons:
        it is what change 5 means by "the same measurements... as the
        report", and it keeps the two failure domains apart — the turn
        path's long-lived write connection is unaffected by whatever makes
        a *fresh* open of store.db fail (permissions revoked, the file
        replaced), so a broken self-report can never take the turn path
        down with it, and vice versa.

        Never raises: collect_from_path()/collect_measurements() already
        mark individual fields unavailable rather than raising; this is a
        second net in case opening the read-only connection itself fails
        outright (e.g. the file is gone or unreadable) — the whole line is
        still emitted, this time with everything unavailable, and the
        daemon keeps running."""
        try:
            runs_path = getattr(
                self._core.memory, "runs_path", self._db_path.with_name("runs.db"))
            measurements = await collect_from_path(
                self._db_path, runs_path, self._history_path, now=self._now(),
            )
        except Exception as exc:
            log.warning("self-report: could not open store read-only: %s", exc)
            measurements = {}

        # Unlike the external `--report` CLI, the daemon has the live core
        # in hand: introspect() reads current affect without touching the
        # store, so "current emotion/mood/temperament" doesn't have to wait
        # for CognitiveCore.stop()'s once-per-shutdown persist to the facts
        # table. Left through collect_from_path()'s own read, "current"
        # affect is `unavailable` for the entire life of a daemon that
        # hasn't yet been cleanly stopped once — see DECISIONS.md (CP-C).
        try:
            state = self._core.introspect()
            mood = state.mood if state.mood is not None else AffectVector()
            temperament = state.temperament if state.temperament is not None else AffectVector()
            measurements.update({
                "emotion_v": state.emotion.valence, "emotion_a": state.emotion.arousal,
                "mood_v": mood.valence, "mood_a": mood.arousal,
                "temperament_v": temperament.valence, "temperament_a": temperament.arousal,
                "temperament_c": temperament.control if temperament.control is not None else UNAVAILABLE,
            })
        except Exception as exc:
            log.warning("self-report: introspect() failed: %s", exc)

        log.info(format_log_line(measurements))

    async def _self_report_loop(self) -> None:
        await self._emit_self_report()
        while True:
            await asyncio.sleep(self._report_interval)
            await self._emit_self_report()

    async def stop(self) -> None:
        if self._report_task is not None:
            self._report_task.cancel()
            try:
                await self._report_task
            except asyncio.CancelledError:
                pass
            self._report_task = None
        if self._server is not None:
            await self._server.stop()
        if self._core is not None:
            await self._core.stop()  # persists affect
            await self._core.memory.close()
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
