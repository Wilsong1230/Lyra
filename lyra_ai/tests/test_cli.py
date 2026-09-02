"""Tests for lyra.cli — the thin client REPL. No daemon: LyraClient.connect is
patched to hand back a fake, and keyboard input is scripted."""
from __future__ import annotations

import asyncio
import io
import threading
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.assistant import DaemonError, DaemonGone, DaemonUnavailable
from lyra.cli import main


class _FakeClient:
    def __init__(self, reply="Hello world", chat_side_effect=None) -> None:
        self.closed = asyncio.Event()
        self.chat = AsyncMock(return_value=reply, side_effect=chat_side_effect)
        self.close = AsyncMock()


def _run_main(inputs: list[str], client: _FakeClient | None = None, argv: list[str] | None = None):
    client = client if client is not None else _FakeClient()
    input_iter = iter(inputs)

    def fake_read_input() -> str:
        return next(input_iter)

    out, err = io.StringIO(), io.StringIO()
    code = 0
    with patch("lyra.cli.LyraClient.connect", new=AsyncMock(return_value=client)), \
         patch("lyra.cli._read_input", side_effect=fake_read_input), \
         patch("lyra.cli.set_state"), \
         patch("lyra.cli.speak_response"), \
         patch("sys.argv", ["lyra", *(argv or [])]):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                main()
            except SystemExit as exc:
                code = exc.code
    return out.getvalue(), err.getvalue(), code, client


def test_no_daemon_prints_one_line_and_exits_nonzero():
    out, err = io.StringIO(), io.StringIO()
    with patch("lyra.cli.LyraClient.connect",
               new=AsyncMock(side_effect=DaemonUnavailable("no daemon listening on 127.0.0.1:8010 (refused)"))), \
         patch("sys.argv", ["lyra"]):
        with redirect_stdout(out), redirect_stderr(err):
            with pytest.raises(SystemExit) as exc:
                main()
    assert exc.value.code == 1
    assert "no daemon listening on 127.0.0.1:8010" in err.getvalue()
    assert "python -m lyra_core" in err.getvalue()


def test_turn_is_sent_to_the_daemon_and_the_reply_printed():
    out, _, code, client = _run_main(["hello", "/quit"])
    assert code == 0
    assert "Hello world" in out
    client.chat.assert_awaited_once()
    text, session = client.chat.await_args.args
    assert text == "hello" and session


def test_session_flag_is_sent_with_every_turn():
    _, _, _, client = _run_main(["hi", "/quit"], argv=["--session", "abc-123"])
    assert client.chat.await_args.args == ("hi", "abc-123")


def test_session_new_switches_sessions():
    _, _, _, client = _run_main(["one", "/session new", "two", "/quit"])
    (first, s1), (second, s2) = (c.args for c in client.chat.await_args_list)
    assert (first, second) == ("one", "two")
    assert s1 != s2


def test_quit_detaches_without_asking_the_daemon_anything():
    _, _, code, client = _run_main(["/quit"])
    assert code == 0
    client.chat.assert_not_awaited()
    client.close.assert_awaited_once()


def test_help_lists_commands_and_no_streaming():
    out, _, _, _ = _run_main(["/help", "/quit"])
    assert "/session" in out and "/voice" in out
    assert "/stream" not in out


def test_error_frame_is_printed_and_the_loop_continues():
    client = _FakeClient(chat_side_effect=[DaemonError("backend ollama failed: refused"), "second reply"])
    out, err, code, _ = _run_main(["one", "two", "/quit"], client=client)
    assert code == 0
    assert "backend ollama failed" in err
    assert "second reply" in out


def test_daemon_gone_mid_turn_exits_nonzero():
    client = _FakeClient(chat_side_effect=DaemonGone("daemon at 127.0.0.1:8010 closed the connection before replying"))
    _, err, code, _ = _run_main(["hello"], client=client)
    assert code == 1
    assert "closed the connection" in err


def test_daemon_closing_while_idle_at_the_prompt_exits_promptly():
    """The user is sitting at the prompt; the daemon dies. The CLI must not
    wait for a keypress to find out."""
    client = _FakeClient()
    release = threading.Event()

    def blocked_input():
        release.wait()  # nobody types anything
        raise EOFError

    async def _kill_daemon_soon():
        await asyncio.sleep(0.05)
        client.closed.set()

    real_run = asyncio.run

    def run_with_killer(coro):
        async def _both():
            asyncio.create_task(_kill_daemon_soon())
            return await coro
        return real_run(_both())

    err = io.StringIO()
    try:
        with patch("lyra.cli.LyraClient.connect", new=AsyncMock(return_value=client)), \
             patch("lyra.cli._read_input", side_effect=blocked_input), \
             patch("lyra.cli.asyncio.run", side_effect=run_with_killer), \
             patch("sys.argv", ["lyra"]):
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1
        assert "closed the connection" in err.getvalue()
    finally:
        release.set()
