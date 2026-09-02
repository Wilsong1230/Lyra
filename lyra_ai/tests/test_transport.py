"""Tests for lyra_core.transport — frames, the loopback server, the one queue.

Every server test binds port 0 and talks to the real socket. The turn
function is a fake; nothing here touches the core or a store.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from lyra_core.config import PROTOCOL_VERSION
from lyra_core.transport import (
    FrameError,
    TurnRejected,
    TurnServer,
    encode_frame,
    parse_daemon_frame,
    parse_turn,
    reply_frame,
    turn_frame,
)


# ── frames ───────────────────────────────────────────────────────────────────

def test_turn_frame_round_trips_through_parse_turn():
    line = encode_frame(turn_frame("hello", "s1"))
    assert parse_turn(line) == ("hello", "s1")


def test_turn_without_session_parses_with_none():
    assert parse_turn(encode_frame(turn_frame("hello"))) == ("hello", None)


@pytest.mark.parametrize("line, needle", [
    (b"\n", "empty frame"),
    (b"not json\n", "not valid JSON"),
    (b"[1, 2]\n", "must be a JSON object"),
    (b'{"type": "turn", "text": "x"}\n', "no integer 'v'"),
    (b'{"v": "1", "type": "turn", "text": "x"}\n', "no integer 'v'"),
    (b'{"v": true, "type": "turn", "text": "x"}\n', "no integer 'v'"),
    (b'{"v": 2, "type": "turn", "text": "x"}\n', "unsupported protocol version 2"),
    (b'{"v": 1, "text": "x"}\n', "no string 'type'"),
    (b'{"v": 1, "type": "reply", "text": "x"}\n', "unknown frame type 'reply'"),
    (b'{"v": 1, "type": "turn"}\n', "no string 'text'"),
    (b'{"v": 1, "type": "turn", "text": 5}\n', "no string 'text'"),
    (b'{"v": 1, "type": "turn", "text": "   "}\n', "turn text is empty"),
    (b'{"v": 1, "type": "turn", "text": "x", "session": ""}\n', "'session'"),
    (b'{"v": 1, "type": "turn", "text": "x", "session": 3}\n', "'session'"),
    (b"\xff\xfe\n", "not valid UTF-8"),
])
def test_parse_turn_rejects_malformed_frames(line, needle):
    with pytest.raises(FrameError, match=needle):
        parse_turn(line)


def test_parse_daemon_frame_accepts_reply_and_error():
    assert parse_daemon_frame(encode_frame(reply_frame("hi")))["text"] == "hi"
    assert parse_daemon_frame(b'{"v": 1, "type": "error", "msg": "bad"}\n')["msg"] == "bad"


def test_parse_daemon_frame_rejects_turn_and_wrong_version():
    with pytest.raises(FrameError, match="unknown frame type 'turn'"):
        parse_daemon_frame(encode_frame(turn_frame("x")))
    with pytest.raises(FrameError, match="unsupported protocol version"):
        parse_daemon_frame(b'{"v": 99, "type": "reply", "text": "x"}\n')


# ── server harness ───────────────────────────────────────────────────────────

async def _open(server: TurnServer):
    return await asyncio.open_connection("127.0.0.1", server.port)


async def _exchange(reader, writer, payload: bytes) -> dict:
    writer.write(payload)
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), 5.0)
    assert line, "server closed the connection instead of answering"
    return json.loads(line)


async def _echo(text: str, session: str) -> str:
    return f"{session}:{text}"


# ── server behaviour ─────────────────────────────────────────────────────────

def test_server_answers_a_turn_with_a_reply_frame():
    async def _run():
        server = TurnServer(_echo, port=0)
        await server.start()
        try:
            reader, writer = await _open(server)
            frame = await _exchange(reader, writer, encode_frame(turn_frame("hello", "s1")))
            writer.close()
            return frame
        finally:
            await server.stop()

    frame = asyncio.run(_run())
    assert frame == {"v": PROTOCOL_VERSION, "type": "reply", "text": "s1:hello"}


def test_turn_without_session_gets_a_per_connection_session():
    async def _run():
        server = TurnServer(_echo, port=0)
        await server.start()
        try:
            r1, w1 = await _open(server)
            a = await _exchange(r1, w1, encode_frame(turn_frame("one")))
            b = await _exchange(r1, w1, encode_frame(turn_frame("two")))
            r2, w2 = await _open(server)
            c = await _exchange(r2, w2, encode_frame(turn_frame("three")))
            w1.close(); w2.close()
            return a["text"], b["text"], c["text"]
        finally:
            await server.stop()

    a, b, c = asyncio.run(_run())
    sess_a, sess_b, sess_c = (t.split(":")[0] for t in (a, b, c))
    assert sess_a == sess_b, "one connection, one session"
    assert sess_a != sess_c, "another connection, another session"


def test_malformed_json_gets_an_error_frame_and_the_server_stays_up():
    async def _run():
        server = TurnServer(_echo, port=0)
        await server.start()
        try:
            reader, writer = await _open(server)
            err = await _exchange(reader, writer, b"{this is not json\n")
            # Same connection still works afterwards.
            ok = await _exchange(reader, writer, encode_frame(turn_frame("still here", "s")))
            writer.close()
            return err, ok
        finally:
            await server.stop()

    err, ok = asyncio.run(_run())
    assert err["type"] == "error" and err["v"] == PROTOCOL_VERSION
    assert "not valid JSON" in err["msg"]
    assert ok["type"] == "reply" and ok["text"] == "s:still here"


def test_unknown_version_gets_an_error_frame():
    async def _run():
        server = TurnServer(_echo, port=0)
        await server.start()
        try:
            reader, writer = await _open(server)
            err = await _exchange(reader, writer, b'{"v": 7, "type": "turn", "text": "x"}\n')
            writer.close()
            return err
        finally:
            await server.stop()

    err = asyncio.run(_run())
    assert err["type"] == "error"
    assert "unsupported protocol version 7" in err["msg"]


def test_oversized_frame_is_answered_then_disconnected():
    async def _run():
        server = TurnServer(_echo, port=0, max_frame_bytes=256)
        await server.start()
        try:
            reader, writer = await _open(server)
            err = await _exchange(reader, writer, b"x" * 1000 + b"\n")
            eof = await asyncio.wait_for(reader.readline(), 5.0)
            # And the server is still accepting.
            r2, w2 = await _open(server)
            ok = await _exchange(r2, w2, encode_frame(turn_frame("fine", "s")))
            w2.close()
            return err, eof, ok
        finally:
            await server.stop()

    err, eof, ok = asyncio.run(_run())
    assert err["type"] == "error" and "exceeds 256 bytes" in err["msg"]
    assert eof == b"", "the overrun connection must be closed"
    assert ok["text"] == "s:fine"


def test_concurrent_clients_are_serialized_in_arrival_order():
    """Two clients, two turns in flight: the core-facing function never
    overlaps with itself, and turns complete in the order they arrived."""
    events: list[tuple[str, str]] = []

    async def slow_turn(text: str, session: str) -> str:
        events.append(("start", text))
        await asyncio.sleep(0.05)
        events.append(("end", text))
        return f"reply {text}"

    async def _run():
        server = TurnServer(slow_turn, port=0)
        await server.start()
        try:
            ra, wa = await _open(server)
            rb, wb = await _open(server)
            wa.write(encode_frame(turn_frame("A", "a"))); await wa.drain()
            await asyncio.sleep(0.01)  # A is in flight before B is sent
            wb.write(encode_frame(turn_frame("B", "b"))); await wb.drain()
            reply_a = json.loads(await asyncio.wait_for(ra.readline(), 5.0))
            reply_b = json.loads(await asyncio.wait_for(rb.readline(), 5.0))
            wa.close(); wb.close()
            return reply_a, reply_b
        finally:
            await server.stop()

    reply_a, reply_b = asyncio.run(_run())
    assert reply_a["text"] == "reply A" and reply_b["text"] == "reply B"
    assert events == [("start", "A"), ("end", "A"), ("start", "B"), ("end", "B")]


def test_rejected_turn_becomes_an_error_frame_and_the_server_lives():
    async def picky(text: str, session: str) -> str:
        if text == "bad":
            raise TurnRejected("backend said no")
        return "ok"

    async def _run():
        server = TurnServer(picky, port=0)
        await server.start()
        try:
            reader, writer = await _open(server)
            err = await _exchange(reader, writer, encode_frame(turn_frame("bad", "s")))
            ok = await _exchange(reader, writer, encode_frame(turn_frame("good", "s")))
            writer.close()
            return err, ok, server.fatal.done()
        finally:
            await server.stop()

    err, ok, fatal = asyncio.run(_run())
    assert err == {"v": PROTOCOL_VERSION, "type": "error", "msg": "backend said no"}
    assert ok["text"] == "ok"
    assert fatal is False


def test_turn_path_failure_is_fatal_not_swallowed():
    async def broken(text: str, session: str) -> str:
        raise ValueError("no active connection")

    async def _run():
        server = TurnServer(broken, port=0)
        await server.start()
        try:
            reader, writer = await _open(server)
            err = await _exchange(reader, writer, encode_frame(turn_frame("x", "s")))
            fatal_exc = server.fatal.exception() if server.fatal.done() else None
            writer.close()
            return err, fatal_exc
        finally:
            await server.stop()

    err, fatal_exc = asyncio.run(_run())
    assert err["type"] == "error" and "no active connection" in err["msg"]
    assert isinstance(fatal_exc, ValueError)


def test_client_disconnect_mid_turn_does_not_kill_the_server():
    async def slow(text: str, session: str) -> str:
        await asyncio.sleep(0.1)
        return "late"

    async def _run():
        server = TurnServer(slow, port=0)
        await server.start()
        try:
            reader, writer = await _open(server)
            writer.write(encode_frame(turn_frame("x", "s"))); await writer.drain()
            writer.close()  # gone before the reply exists
            await asyncio.sleep(0.2)
            r2, w2 = await _open(server)
            ok = await _exchange(r2, w2, encode_frame(turn_frame("y", "s")))
            w2.close()
            return ok, server.turns_completed, server.fatal.done()
        finally:
            await server.stop()

    ok, completed, fatal = asyncio.run(_run())
    assert ok["text"] == "late"
    assert completed == 2, "the abandoned turn still ran to completion"
    assert fatal is False


def test_stop_fails_queued_turns_honestly():
    async def _run():
        gate = asyncio.Event()

        async def blocked(text: str, session: str) -> str:
            await gate.wait()
            return "never"

        server = TurnServer(blocked, port=0)
        await server.start()
        reader, writer = await _open(server)
        writer.write(encode_frame(turn_frame("x", "s"))); await writer.drain()
        await asyncio.sleep(0.02)
        await server.stop()
        line = await asyncio.wait_for(reader.readline(), 5.0)
        writer.close()
        return json.loads(line) if line else None

    frame = asyncio.run(_run())
    assert frame is not None and frame["type"] == "error"
    assert "shutting down" in frame["msg"]
