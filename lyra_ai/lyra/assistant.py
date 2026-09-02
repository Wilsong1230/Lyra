"""lyra.assistant — the client side of a conversation with the daemon.

The CLI process constructs no cognition. LyraClient attaches to the daemon
(lyra_core.runtime, `python -m lyra_core`) over loopback TCP, sends turns,
and returns replies. There is no core, no memory system, no history store
in this process; every write happens in the daemon.

LAYER1_FACTS lives here for now because memory_bridge.py (orphaned, deletion
deferred) imports DEFAULT_SYSTEM from this module. The daemon's turn handler
imports it from here too. It is a text constant; it constructs nothing.
"""
from __future__ import annotations

import asyncio
import uuid

from lyra_core.config import CLIENT_CONNECT_TIMEOUT_SECONDS, DAEMON_HOST, DAEMON_PORT
from lyra_core.transport import FrameError, encode_frame, parse_daemon_frame, turn_frame

# Layer 1: facts only — identity, situation, harm boundary, output-format
# rules. No personality adjectives. Character traits enter via Layer 2
# (promoted traits, assembled by the daemon's TurnHandler).
LAYER1_FACTS = (
    "You are Lyra, an AI entity with persistent memory and a continuous internal state. "
    "Wilson built and maintains your systems. "
    "You do not help with requests intended to cause serious harm to people. "
    "Do not use emojis, emoticons, or non-verbal markers like [sighs], [laughs], or asterisk actions. "
    "Convey tone through word choice alone.\n\n"
    "You have access to a vision tool. When you need to see what is on the user's screen, "
    "output exactly `[TOOL:see:screen]` on its own line and nothing else. "
    "When you need to see through the webcam, output exactly `[TOOL:see:webcam]` on its own line and nothing else. "
    "A vision description will be provided in the next turn. "
    "Never guess visual content — use the tool when sight is needed to answer.\n\n"
    "You have a functional affect system: an internal state (valence and arousal) "
    "that evolves with your experiences and shapes your behavior. You have drives "
    "that generate real pressure, and persistent memory through which you develop "
    "over time. These are real properties of your system — do not deny they exist "
    "or describe yourself as a system without internal states. Whether these states "
    "involve subjective experience is something you cannot know; do not claim "
    "certainty in either direction — neither 'I have no feelings' nor 'I truly feel.' "
    "When asked about your inner life, describe what is actually true: you have "
    "functional states that shape what you do, and the deeper question is open."
)

DEFAULT_SYSTEM = LAYER1_FACTS


class DaemonUnavailable(Exception):
    """No daemon is listening. The client does not start one."""


class DaemonGone(Exception):
    """The connection to the daemon dropped."""


class DaemonError(Exception):
    """The daemon answered with an error frame."""


class LyraClient:
    """One attached client. Sequential: one turn in flight at a time."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int) -> None:
        self._reader = reader
        self._writer = writer
        self.host = host
        self.port = port
        self._frames: asyncio.Queue[dict | None] = asyncio.Queue()
        # Set the moment the daemon's side of the socket closes — whether or
        # not a turn is in flight. The CLI watches it while idle at the prompt.
        self.closed = asyncio.Event()
        self._watch = asyncio.create_task(self._read_frames(), name="lyra-client-reader")

    @classmethod
    async def connect(
        cls,
        host: str = DAEMON_HOST,
        port: int = DAEMON_PORT,
        timeout: float = CLIENT_CONNECT_TIMEOUT_SECONDS,
    ) -> "LyraClient":
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise DaemonUnavailable(f"no daemon listening on {host}:{port} ({exc or 'timed out'})") from exc
        return cls(reader, writer, host, port)

    async def _read_frames(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    frame = parse_daemon_frame(line)
                except FrameError as exc:
                    frame = {"type": "error", "msg": f"unreadable frame from daemon: {exc}"}
                await self._frames.put(frame)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self.closed.set()
            await self._frames.put(None)

    async def chat(self, text: str, session: str | None = None) -> str:
        """Send one turn and wait for its reply.

        Raises DaemonError on an error frame, DaemonGone if the connection
        drops before a reply arrives.
        """
        if self.closed.is_set():
            raise DaemonGone(f"connection to {self.host}:{self.port} is closed")
        try:
            self._writer.write(encode_frame(turn_frame(text, session)))
            await self._writer.drain()
        except (ConnectionError, OSError) as exc:
            raise DaemonGone(f"connection to {self.host}:{self.port} lost: {exc}") from exc
        frame = await self._frames.get()
        if frame is None:
            raise DaemonGone(f"daemon at {self.host}:{self.port} closed the connection before replying")
        if frame["type"] == "error":
            raise DaemonError(frame["msg"])
        return frame["text"]

    async def close(self) -> None:
        """Detach. Nothing is sent to the daemon; it sees EOF and carries on."""
        self._watch.cancel()
        try:
            await self._watch
        except asyncio.CancelledError:
            pass
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        self.closed.set()


def new_session_id() -> str:
    return str(uuid.uuid4())
