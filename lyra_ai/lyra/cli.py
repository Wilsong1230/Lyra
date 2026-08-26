from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import uuid
from http.client import HTTPConnection
from urllib.parse import urlparse

import readchar

from lyra.assistant import Assistant
from lyra.backends import BACKENDS, auto_select_backend, available_backends, create_backend
from lyra.memory import ConversationMemory

HELP_TEXT = """\
Commands:
  /help             Show this help
  /quit             Exit
  /stream           Toggle streaming output on/off
  /history          Print this session's conversation
  /clear            Erase this session's history
  /session list     List all saved sessions
  /session new      Start a fresh session
  /session <id>     Switch to an existing session
  /backend          Show current backend
  /model            Show current model
  /voice            Toggle voice output on/off

Push-to-talk: press Ctrl+R at the prompt, speak, press Enter to send.
"""

EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
VOICE_URL = os.getenv("VOICE_URL", "http://localhost:8001")
LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")
VISION_URL = os.getenv("VISION_URL", "http://localhost:8003")


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


def _call_vision(source: str) -> str:
    try:
        result = _post_json(f"{VISION_URL}/see", {"source": source, "prompt": "Describe what you see in detail."}, timeout=30.0)
        return result.get("description", "Error: empty vision response")
    except Exception as e:
        return f"Error: vision service unavailable — {e}"


_PTT_KEY  = "\x12"   # Ctrl+R
_CTRL_C   = "\x03"
_CTRL_D   = "\x04"
_ESC      = "\x1b"
_BACKSPACE = ("\x7f", "\x08")


def _read_input() -> str | None:
    """Read a line from stdin char-by-char. Returns None if Ctrl+R pressed."""
    print("you> ", end="", flush=True)
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


async def _main() -> None:
    parser = argparse.ArgumentParser(prog="lyra", description="Lyra — conversational assistant")
    parser.add_argument("--backend", choices=list(BACKENDS), help="Backend to use")
    parser.add_argument("--model", help="Model name (overrides backend default)")
    parser.add_argument("--session", help="Resume a specific session ID")
    parser.add_argument("--list-backends", action="store_true", help="Show available backends and exit")
    parser.add_argument("--list-models", action="store_true", help="List models for the chosen backend and exit")
    args = parser.parse_args()

    if args.list_backends:
        avail = available_backends()
        for name in BACKENDS:
            marker = "+" if name in avail else "-"
            print(f"  [{marker}] {name}")
        return

    if args.backend:
        try:
            backend = create_backend(args.backend)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            backend = auto_select_backend()
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    if args.model:
        backend.default_model = args.model

    if args.list_models:
        try:
            for m in backend.list_models():
                print(f"  {m}")
        except Exception as exc:
            print(f"error fetching models: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    memory = ConversationMemory()
    session = args.session or str(uuid.uuid4())
    assistant = Assistant(backend=backend, memory=memory)

    print("Starting memory system...", end="", flush=True)
    try:
        await assistant.start()
        print(" ready.")
    except Exception as exc:
        print(f" unavailable ({exc}).")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(assistant.stop()))
        except NotImplementedError:
            # Windows: ProactorEventLoop has no add_signal_handler. Ctrl-C is already
            # handled by the KeyboardInterrupt branch around _read_input below.
            pass

    prior = memory.get_history(session)
    print(f"Lyra  [{backend.name} / {backend.default_model}]")
    if prior:
        print(f"Resumed session {session}  ({len(prior) // 2} prior turns)")
    else:
        print(f"Session {session}")
    print("Type /help for commands or /quit to exit.\n")

    streaming = False
    voice = True
    try:
        while True:
            try:
                raw = await asyncio.to_thread(_read_input)
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
                    await assistant.stop()
                    print("Goodbye.")
                    break
                elif cmd == "/help":
                    print(HELP_TEXT)
                elif cmd == "/history":
                    hist = memory.get_history(session)
                    if not hist:
                        print("(empty)")
                    for turn in hist:
                        snippet = turn["content"][:200].replace("\n", " ")
                        print(f"  {turn['role']:9s}  {snippet}")
                elif cmd == "/clear":
                    memory.clear_session(session)
                    print("Session cleared.")
                elif cmd == "/session":
                    if arg == "list":
                        sessions = memory.list_sessions()
                        if not sessions:
                            print("No saved sessions.")
                        for s in sessions:
                            print(f"  {s}")
                    elif arg == "new":
                        session = str(uuid.uuid4())
                        print(f"New session: {session}")
                    elif arg:
                        session = arg
                        hist = memory.get_history(session)
                        print(f"Session {session}  ({len(hist) // 2} turns)")
                    else:
                        print("Usage: /session list | new | <id>")
                elif cmd == "/backend":
                    print(f"Backend: {backend.name}")
                elif cmd == "/model":
                    print(f"Model: {backend.default_model}")
                elif cmd == "/stream":
                    streaming = not streaming
                    print("Streaming on." if streaming else "Streaming off.")
                elif cmd == "/voice":
                    voice = not voice
                    print("Voice on." if voice else "Voice off.")
                else:
                    print(f"Unknown command. Try /help.")
                continue

            try:
                if streaming:
                    set_state("thinking")
                    print("\nlyra> ", end="", flush=True)
                    chunks: list[str] = []
                    async for chunk in assistant.stream_chat_with_tools(user_input, session, vision_fn=_call_vision):
                        print(chunk, end="", flush=True)
                        chunks.append(chunk)
                    print()
                    full_response = "".join(chunks)
                    if voice:
                        speak_response(full_response)
                    else:
                        set_state("idle")
                else:
                    set_state("thinking")
                    response = await assistant.chat_with_tools(user_input, session, vision_fn=_call_vision)
                    print(f"\nlyra> {response}\n")
                    if voice:
                        speak_response(response)
                    else:
                        set_state("speaking")
                        set_state("idle")
            except Exception as exc:
                set_state("idle")
                print(f"[error] {exc}", file=sys.stderr)
    finally:
        await assistant.stop()


def main() -> None:
    asyncio.run(_main())
