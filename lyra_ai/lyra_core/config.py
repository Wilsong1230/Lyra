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
# of seconds; without a clamp one tick would push affect far outside [-1, 1].
#
# CP-A set this to 0.1 s because BoredomDrive's idle accumulation was
# unbounded and the emotion/mood coupling was a Jacobi split that disagreed
# across tick rates (CP-A.1, docs/AFFECT_CHARACTERIZATION.md) — any larger
# clamp made an ordinary conversational gap drift affect hard. CP-A.2 fixes
# both: BoredomDrive.update now approaches a bounded ceiling
# (drives._BOREDOM_PRESSURE_CEILING = 0.02) instead of growing without
# limit, and AffectEngine.update is the exact solution of the coupled 2x2
# system instead of an operator split, so it is tick-rate invariant.
#
# 600.0 (10 minutes) was chosen, not derived, against two measured
# constraints (docs/AFFECT_CHARACTERIZATION.md "Clamp safety" and "3e.
# Conversation"):
#   - it must cover every gap this checkpoint's done-when names as
#     "ordinary" (5 s, 60 s, 10 min) so none of them clamp;
#   - a single worst-case tick at this dt — pressure already at its
#     ceiling, applied for the whole 600 s — must not saturate either
#     affect axis (measured: valence -0.22, arousal 0.11, both well inside
#     [-1, 1]).
# The binding constraint turned out not to be |value| < 1 but
# expression.prose_hint's much tighter "terse" threshold (blended
# emotion+mood valence <= -0.3): the ceiling that satisfies it
# (_BOREDOM_PRESSURE_CEILING = 0.02) leaves single-tick saturation nowhere
# close even at clamp values well past 600 s, so the clamp itself is sized
# to the "ordinary gaps never clamp" requirement, not to affect safety.
# The remaining behavior this constant controls — retuning the drives
# themselves, or the clamp value again — is a later checkpoint's call, not
# this one's.
MAX_TICK_DT_SECONDS = 600.0

# ── logging ──────────────────────────────────────────────────────────────────
# The daemon logs to stderr and to this file. "grep the log for
# CORE_CONSTRUCTED" needs a log that outlives the terminal it started in.
LOG_PATH = Path.home() / ".lyra" / "lyra_core.log"
