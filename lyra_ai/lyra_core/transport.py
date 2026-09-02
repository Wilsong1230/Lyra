"""lyra_core.transport — line-delimited JSON over loopback TCP.

One frame per line. Every frame is a JSON object carrying an integer "v".

  client -> daemon   {"v": 1, "type": "turn",  "text": "...", "session": "..."?}
  daemon -> client   {"v": 1, "type": "reply", "text": "..."}
                     {"v": 1, "type": "error", "msg":  "..."}

"session" is optional. A client that omits it gets a session generated for
its connection; the CLI always sends one so that `--session` can resume.

TurnServer owns the ONE queue through which every turn reaches the core.
Any number of clients may be connected; a single worker task drains the
queue, so the turn function is never running for two clients at once and
turns are answered in arrival order.

Bad client input is answered with an error frame and never reaches the
core; nothing a client sends can take the daemon down. A failure INSIDE the
turn function is a different matter: TurnRejected means "this turn could not
be answered, the daemon is healthy" (the LLM backend refused, for instance)
and becomes an error frame; anything else is fatal, surfaces on
TurnServer.fatal, and the runtime shuts the process down with a nonzero
exit rather than answering the next turn over a broken store.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from lyra_core.config import DAEMON_HOST, DAEMON_PORT, MAX_FRAME_BYTES, PROTOCOL_VERSION

log = logging.getLogger("lyra_core.transport")

TurnFn = Callable[[str, str], Awaitable[str]]


class FrameError(ValueError):
    """A line from a client that is not a valid frame. Answered, never raised out."""


class TurnRejected(Exception):
    """The turn could not be answered and the daemon is healthy. Sent as an error frame."""


# ── frames ───────────────────────────────────────────────────────────────────

def encode_frame(frame: dict) -> bytes:
    return (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")


def turn_frame(text: str, session: str | None = None) -> dict:
    frame = {"v": PROTOCOL_VERSION, "type": "turn", "text": text}
    if session is not None:
        frame["session"] = session
    return frame


def reply_frame(text: str) -> dict:
    return {"v": PROTOCOL_VERSION, "type": "reply", "text": text}


def error_frame(msg: str) -> dict:
    return {"v": PROTOCOL_VERSION, "type": "error", "msg": msg}


def _decode_object(line: bytes) -> dict:
    """The checks shared by both directions: UTF-8, JSON, object, version."""
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrameError(f"frame is not valid UTF-8: {exc.reason} at byte {exc.start}") from None
    text = text.rstrip("\r\n")
    if not text.strip():
        raise FrameError("empty frame")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FrameError(f"frame is not valid JSON: {exc.msg} at column {exc.colno}") from None
    if not isinstance(obj, dict):
        raise FrameError(f"frame must be a JSON object, got {type(obj).__name__}")
    v = obj.get("v")
    # bool is a subclass of int; {"v": true} is not a version.
    if isinstance(v, bool) or not isinstance(v, int):
        raise FrameError("frame has no integer 'v' field")
    if v != PROTOCOL_VERSION:
        raise FrameError(
            f"unsupported protocol version {v}; this daemon speaks v{PROTOCOL_VERSION}"
        )
    return obj


def parse_turn(line: bytes) -> tuple[str, str | None]:
    """Validate a client frame. Returns (text, session) or raises FrameError."""
    obj = _decode_object(line)
    kind = obj.get("type")
    if not isinstance(kind, str):
        raise FrameError("frame has no string 'type' field")
    if kind != "turn":
        raise FrameError(f"unknown frame type {kind!r}; expected 'turn'")
    text = obj.get("text")
    if not isinstance(text, str):
        raise FrameError("turn has no string 'text' field")
    if not text.strip():
        raise FrameError("turn text is empty")
    session = obj.get("session")
    if session is not None and (not isinstance(session, str) or not session.strip()):
        raise FrameError("'session' must be a non-empty string when present")
    return text, session


def parse_daemon_frame(line: bytes) -> dict:
    """Validate a daemon frame on the client side. Returns the frame dict."""
    obj = _decode_object(line)
    kind = obj.get("type")
    if kind == "reply":
        if not isinstance(obj.get("text"), str):
            raise FrameError("reply has no string 'text' field")
    elif kind == "error":
        if not isinstance(obj.get("msg"), str):
            raise FrameError("error frame has no string 'msg' field")
    else:
        raise FrameError(f"unknown frame type {kind!r} from daemon")
    return obj


# ── server ───────────────────────────────────────────────────────────────────

@dataclass
class _Pending:
    text: str
    session: str
    future: asyncio.Future = field(repr=False)


async def _send(writer: asyncio.StreamWriter, frame: dict) -> None:
    writer.write(encode_frame(frame))
    await writer.drain()


class TurnServer:
    """Accepts loopback connections and feeds their turns through one queue."""

    def __init__(
        self,
        turn_fn: TurnFn,
        host: str = DAEMON_HOST,
        port: int = DAEMON_PORT,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        self._turn_fn = turn_fn
        self._host = host
        self._port = port
        self._max_frame_bytes = max_frame_bytes
        self._queue: asyncio.Queue[_Pending] = asyncio.Queue()
        self._server: asyncio.AbstractServer | None = None
        self._worker: asyncio.Task | None = None
        self._fatal: asyncio.Future | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.turns_completed = 0

    @property
    def fatal(self) -> asyncio.Future:
        """Resolves with the exception that broke the turn worker. Never resolves otherwise."""
        if self._fatal is None:
            raise RuntimeError("TurnServer has not been started")
        return self._fatal

    @property
    def port(self) -> int:
        """The bound port (useful when constructed with port=0 for tests)."""
        if self._server is None or not self._server.sockets:
            raise RuntimeError("TurnServer has not been started")
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._fatal = loop.create_future()
        self._server = await asyncio.start_server(
            self._on_connect, self._host, self._port, limit=self._max_frame_bytes
        )
        self._worker = asyncio.create_task(self._drain(), name="lyra-turn-worker")
        log.info("listening on %s:%d", self._host, self.port)

    async def stop(self) -> None:
        # Order matters. Stop accepting; stop the worker (which fails the
        # in-flight turn); fail every queued turn; hang up on every client so
        # their handlers see EOF; only then wait for the server, which on
        # Python 3.12+ waits for those handlers to finish.
        if self._server is not None:
            self._server.close()
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        while not self._queue.empty():
            pending = self._queue.get_nowait()
            if not pending.future.done():
                pending.future.set_exception(TurnRejected("daemon is shutting down"))
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        if self._server is not None:
            await self._server.wait_closed()
        log.info("listener closed")

    # ── the one queue ────────────────────────────────────────────────────────

    async def _drain(self) -> None:
        while True:
            pending = await self._queue.get()
            try:
                reply = await self._turn_fn(pending.text, pending.session)
            except TurnRejected as exc:
                log.warning("turn rejected (session=%s): %s", pending.session, exc)
                if not pending.future.done():
                    pending.future.set_exception(exc)
            except asyncio.CancelledError:
                if not pending.future.done():
                    pending.future.set_exception(TurnRejected("daemon is shutting down"))
                raise
            except Exception as exc:
                # Not a rejected turn: the turn path itself is broken. Answer
                # this client honestly, then hand the cause to the runtime,
                # which exits nonzero. The worker stops here; no further turn
                # runs over whatever just failed.
                if not pending.future.done():
                    pending.future.set_exception(
                        TurnRejected(f"daemon failed on this turn and is stopping: {exc}")
                    )
                if not self._fatal.done():
                    self._fatal.set_exception(exc)
                return
            else:
                self.turns_completed += 1
                if not pending.future.done():
                    pending.future.set_result(reply)

    # ── per-connection ───────────────────────────────────────────────────────

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        connection_session = uuid.uuid4().hex
        self._writers.add(writer)
        log.info("client connected %s", peer)
        try:
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    # readline cannot resynchronise after an overrun; the
                    # only honest answer is to say why and hang up.
                    await _send(writer, error_frame(
                        f"frame exceeds {self._max_frame_bytes} bytes; closing connection"
                    ))
                    break
                if not line:
                    break  # EOF: the client detached
                try:
                    text, session = parse_turn(line)
                except FrameError as exc:
                    log.warning("bad frame from %s: %s", peer, exc)
                    await _send(writer, error_frame(str(exc)))
                    continue
                pending = _Pending(text, session or connection_session, asyncio.get_running_loop().create_future())
                await self._queue.put(pending)
                try:
                    reply = await pending.future
                except TurnRejected as exc:
                    await _send(writer, error_frame(str(exc)))
                else:
                    await _send(writer, reply_frame(reply))
        except (ConnectionError, OSError) as exc:
            # The client went away mid-turn. The turn itself already
            # completed and was recorded; only the delivery failed.
            log.info("client %s vanished: %s", peer, exc)
        finally:
            self._writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            log.info("client disconnected %s", peer)
