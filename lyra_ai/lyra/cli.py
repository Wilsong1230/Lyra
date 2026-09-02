"""lyra.cli — the `lyra` command. A thin client of the daemon.

Attaches to the daemon over loopback, sends turns, prints replies, and
detaches on /quit, EOF or Ctrl-C without signalling the daemon. If no
daemon is listening it says so on one line and exits nonzero; it never
starts a core of its own.

The peripherals the CLI already drove — avatar state, TTS playback,
push-to-talk recording — stay here. They are the body in the room, not
cognition, and the daemon does not need to know about them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from http.client import HTTPConnection
from urllib.parse import urlparse

import readchar

from lyra.assistant import DaemonError, DaemonGone, DaemonUnavailable, LyraClient, new_session_id
from lyra_core.config import DAEMON_HOST, DAEMON_PORT

HELP_TEXT = """\
Commands:
  /help             Show this help
  /quit             Exit (the daemon keeps running)
  /session          Show the current session id
  /session new      Start a fresh session
  /session <id>     Switch to an existing session
  /voice            Toggle voice output on/off

Push-to-talk: press Ctrl+R at the prompt, speak, press Enter to send.
"""

EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
VOICE_URL = os.getenv("VOICE_URL", "http://localhost:8001")
LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")


def _post_json(url: str, body: dict, timeout: float = 1.0) -> dict:
    parsed = urlparse(url)
    conn = HTTPConnection(parsed.netloc, timeout=timeout)
    data = json.dumps(body).encode()
    conn.request("POST", parsed.path, body=data, headers={"Content-Type": "application/json"})
    raw = conn.getresponse().read()
    try:
        return json.loads(raw)
    except Exception:
        return {}


def set_state(state: str) -> None:
    try:
        _post_json(f"{EMBODIMENT_URL}/state", {"state": state})
    except Exception:
        pass


def speak_response(text: str) -> None:
    try:
        _post_json(f"{VOICE_URL}/speak", {"text": text, "sync_emotion": True}, timeout=30.0)
    except Exception:
        pass


_PTT_KEY  = "\x12"   # Ctrl+R
_CTRL_C   = "\x03"
_CTRL_D   = "\x04"
_ESC      = "\x1b"
_BACKSPACE = ("\x7f", "\x08")


def _read_input() -> str | None:
    """Read a line from stdin. Returns None if Ctrl+R (push-to-talk) is pressed.

    Char-by-char on a terminal so Ctrl+R can be caught; plain line reads
    when stdin is a pipe, where there is no push-to-talk.
    """
    print("you> ", end="", flush=True)
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\r\n")
    buf: list[str] = []
    while True:
        ch = readchar.readchar()
        if ch == _PTT_KEY:
            print()
            return None
        elif ch in ("\r", "\n"):
            print()
            return "".join(buf)
        elif ch in _BACKSPACE:
            if buf:
                buf.pop()
                print("\b \b", end="", flush=True)
        elif ch == _CTRL_C:
            raise KeyboardInterrupt
        elif ch == _CTRL_D:
            raise EOFError
        elif ch == _ESC:
            try:
                nxt = readchar.readchar()
                if nxt == "[":
                    while True:
                        c = readchar.readchar()
                        if c.isalpha() or c == "~":
                            break
            except Exception:
                pass
        elif ch.isprintable():
            buf.append(ch)
            print(ch, end="", flush=True)


def _wait_for_enter() -> None:
    """Block until Enter is pressed, discarding other input."""
    while True:
        ch = readchar.readchar()
        if ch in ("\r", "\n"):
            return


def _read_input_in_thread(loop: asyncio.AbstractEventLoop) -> asyncio.Future:
    """Run _read_input on a daemon thread and hand back a future.

    A daemon thread rather than to_thread's pool: if the daemon connection
    drops while the user is sitting at the prompt, the process must exit
    now, and a pool thread blocked in readchar would be joined at interpreter
    exit and hang until a key was pressed.
    """
    future: asyncio.Future = loop.create_future()

    def _deliver(fn, value) -> None:
        if not future.done():
            fn(value)

    def _run() -> None:
        try:
            result = _read_input()
        except BaseException as exc:  # EOFError / KeyboardInterrupt travel to the loop
            outcome = (future.set_exception, exc)
        else:
            outcome = (future.set_result, result)
        try:
            loop.call_soon_threadsafe(_deliver, *outcome)
        except RuntimeError:
            # The loop is already closed: the CLI exited (daemon gone) while
            # this thread was still waiting on the keyboard. Nothing to tell.
            pass

    threading.Thread(target=_run, name="lyra-input", daemon=True).start()
    return future


class _Terminal:
    """Remembers the terminal's line discipline so an abrupt exit can restore it.

    readchar puts the tty into raw mode for each keypress. If the process
    exits while the input thread is inside one, the shell inherits raw mode.
    """

    def __init__(self) -> None:
        self._fd: int | None = None
        self._saved = None
        if os.name == "posix" and sys.stdin.isatty():
            import termios

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)

    def restore(self) -> None:
        if self._fd is not None and self._saved is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)


def _daemon_lost(terminal: _Terminal, reason: str) -> None:
    terminal.restore()
    print(f"\nlyra: {reason}", file=sys.stderr, flush=True)
    sys.exit(1)


async def _main() -> None:
    parser = argparse.ArgumentParser(prog="lyra", description="Lyra — talk to the running daemon")
    parser.add_argument("--session", help="Resume a specific session ID")
    args = parser.parse_args()

    try:
        client = await LyraClient.connect(DAEMON_HOST, DAEMON_PORT)
    except DaemonUnavailable as exc:
        print(f"lyra: {exc} — start it with `python -m lyra_core`", file=sys.stderr)
        sys.exit(1)

    terminal = _Terminal()
    session = args.session or new_session_id()
    print(f"Lyra  [daemon {DAEMON_HOST}:{DAEMON_PORT}]")
    print(f"Session {session}")
    print("Type /help for commands or /quit to exit.\n")

    voice = True
    loop = asyncio.get_running_loop()
    try:
        while True:
            input_future = _read_input_in_thread(loop)
            closed_wait = asyncio.create_task(client.closed.wait())
            done, _ = await asyncio.wait({input_future, closed_wait}, return_when=asyncio.FIRST_COMPLETED)
            if closed_wait in done and input_future not in done:
                _daemon_lost(terminal, f"daemon at {DAEMON_HOST}:{DAEMON_PORT} closed the connection")
            closed_wait.cancel()
            try:
                raw = input_future.result()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            user_input = ""
            if raw is None:
                print("[recording... press Enter to send]", flush=True)
                try:
                    _post_json(f"{LISTEN_URL}/record/start", {})
                    set_state("listening")
                    await asyncio.to_thread(_wait_for_enter)
                    result = _post_json(f"{LISTEN_URL}/record/stop",
                                        {"sync_emotion": False}, timeout=30.0)
                    user_input = result.get("text", "").strip()
                    set_state("thinking")
                    if not user_input:
                        print("[no speech detected]", flush=True)
                        set_state("idle")
                        continue
                    print(f"you> {user_input}")
                except Exception as e:
                    print(f"[ptt error] {e}", file=sys.stderr)
                    set_state("idle")
                    continue
            else:
                user_input = raw.strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                parts = user_input.split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("/quit", "/exit", "/q"):
                    print("Goodbye.")
                    break
                elif cmd == "/help":
                    print(HELP_TEXT)
                elif cmd == "/session":
                    if arg == "new":
                        session = new_session_id()
                        print(f"New session: {session}")
                    elif arg:
                        session = arg
                        print(f"Session {session}")
                    else:
                        print(f"Session {session}")
                elif cmd == "/voice":
                    voice = not voice
                    print("Voice on." if voice else "Voice off.")
                else:
                    print("Unknown command. Try /help.")
                continue

            set_state("thinking")
            try:
                response = await client.chat(user_input, session)
            except DaemonError as exc:
                set_state("idle")
                print(f"[error] {exc}", file=sys.stderr)
                continue
            except DaemonGone as exc:
                _daemon_lost(terminal, str(exc))
            print(f"\nlyra> {response}\n")
            if voice:
                speak_response(response)
            else:
                set_state("idle")
    finally:
        await client.close()


def main() -> None:
    asyncio.run(_main())
