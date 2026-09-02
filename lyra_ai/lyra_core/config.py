"""lyra_core.config — constants for the daemon process (CP-A).

Everything here is a knob the daemon reads at startup. Nothing here is
authored identity: the prompt text lives with the turn handler, and the
memory package keeps its own config (lyra_memory.config) for store paths
and retrieval tuning.
"""
from __future__ import annotations

from pathlib import Path

# ── transport ────────────────────────────────────────────────────────────────
# Loopback only. 8000–8005 are the embodiment/voice/listen/vision/ambient/
# wakeword services; 8010 is the first free port above them.
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 8010

# Every frame carries an integer "v". A frame with any other version is
# rejected with an error frame; the daemon never guesses at an old client.
PROTOCOL_VERSION = 1

# One line-delimited JSON frame may not exceed this many bytes. A client that
# sends more gets an error frame and is disconnected — the stream cannot be
# resynchronised once a line has overrun the reader.
MAX_FRAME_BYTES = 1 * 1024 * 1024

# How long the CLI waits for the daemon to accept the TCP connection before
# reporting that no daemon is listening.
CLIENT_CONNECT_TIMEOUT_SECONDS = 3.0

# ── tick clock ───────────────────────────────────────────────────────────────
# dt handed to CognitiveCore.tick() is wall-clock seconds since the previous
# tick, clamped to this ceiling. The daemon ticks only when a turn arrives,
# so after a night with no clients the raw elapsed value is tens of thousands
# of seconds; without a clamp one tick would push boredom pressure and then
# valence far outside [-1, 1].
#
# The value is the conversational dt the affect engine and drives were tuned
# at (CognitiveCore.tick's default). BoredomDrive accumulates idle_rate * dt
# per tick and nothing on the conversational path relieves it, so any larger
# ceiling makes an ordinary conversation drift negative in proportion to dt²
# — measured: at 2.0 the emotion axis reaches -1 within six exchanges, where
# at 0.1 the same six exchanges move it by under 0.01. Raising this ceiling
# is a drive-retuning change, not a config change, and belongs to a later
# checkpoint. Until then the clamp engages on effectively every tick, and the
# log line records the raw elapsed time next to the value actually applied.
MAX_TICK_DT_SECONDS = 0.1

# ── logging ──────────────────────────────────────────────────────────────────
# The daemon logs to stderr and to this file. "grep the log for
# CORE_CONSTRUCTED" needs a log that outlives the terminal it started in.
LOG_PATH = Path.home() / ".lyra" / "lyra_core.log"
