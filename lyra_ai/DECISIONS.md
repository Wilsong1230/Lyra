# DECISIONS.md — ambiguities resolved during the construct checkpoints

Format per entry: what was ambiguous, the options considered, the one
chosen, and why. Conservative by default: the smaller change, the
reversible one. `lyra-memory/DECISIONS.md` holds the memory build's own
record; this file starts at CP-A.

---

# CP-A — single locus

## Where `config.py` lives

**Ambiguous:** the FILES set names `config.py` with no path. There was no
`config.py` under `lyra_ai/`; the only one in the tree is
`lyra-memory/lyra_memory/config.py`, a different package.

**Options:** (a) put the daemon constants in `lyra_memory/config.py`;
(b) a top-level `lyra_ai/config.py`; (c) a new `lyra_ai/lyra_core/config.py`.

**Chosen:** (c). The port, protocol version, frame limit, dt clamp and log
path are all properties of the daemon, which is `lyra_core`. (a) would put
transport constants in the memory package and touch a file outside the
set; (b) would not be importable from an installed package (pyproject only
ships `main` and `lyra*`).

**Reversal:** move the constants; every importer is in the FILES set.

## Where `DECISIONS.md` lives

**Chosen:** `lyra_ai/DECISIONS.md`, because every other path in the FILES
set is relative to `lyra_ai/`. The memory package keeps its own.

## The daemon does not run the perception loop

**Ambiguous:** the pre-CP-A `Runtime` ran a `PerceptionLoop` (wakeword
poller, 2 s tick) whose `CoreSink` bound `tick()`'s intents and executed
`speak` through `SpeechActuator`, with `OutcomeTracker` watching for
engagement. The spec says the daemon "owns the single CognitiveCore" and
says nothing about perception; OUT OF SCOPE says "tick() return values
remain unconsumed" and names wakeword/TTS services; DEFERRED names "intent
executor and OutcomeConsolidator on the daemon path".

**Options:**
1. Keep the perception loop, actuator and outcome tracker; add the listener.
2. Keep the perception loop but disconnect the actuator.
3. The daemon ticks the core only when a turn arrives; no perception loop,
   no actuator, no outcome tracker in the daemon.

**Chosen:** 3. Two reasons, one decisive. The decisive one is the dt
done-when: "the daemon idles 8+ hours, then takes a turn: … affect values
are in range rather than saturated." `BoredomDrive` accumulates
`idle_rate * dt` per tick with nothing on the daemon path to relieve it, and
`AffectEngine` applies `input * dt` per tick. A loop ticking every 2 s
through an 8-hour night reaches boredom pressure ≈ 2,880 and drives valence
to about −1,150 per tick — saturated, regardless of any clamp on a single
tick's dt. Retuning the drives is out of scope, so a continuously ticking
daemon cannot meet the criterion. The second reason is that options 1 and 2
leave an intent executor on the daemon path, which DEFERRED says must not
exist yet; the register's statement that both callers discard `tick()`'s
return value is what the spec was written against.

With turn-driven ticks the affect engine's exact integrator still models
the idle gap correctly up to the clamp — relaxation toward mood over dt is
closed-form — so nothing about the affect dynamics is lost, only the
unbounded boredom accumulation, which is the thing that was wrong.

`CoreSink` is deleted with the loop; `perception.py`, `senses.py`,
`actuators.py` and `outcomes.py` are untouched and keep their own tests.

**Reversal:** re-instantiate `PerceptionLoop` in `Runtime.start()` with a
sink that calls `TurnHandler.tick`-style clamped ticks. Belongs with the
drive retuning, not before it.

## The dt clamp is 0.1 s

**Ambiguous:** "Clamp to a named constant in config.py" — no value given.

**Options:** 2.0 (the perception loop's verified dt), something "natural"
like 60 s, or 0.1 (the conversational dt `tick()` defaulted to).

**Chosen:** `MAX_TICK_DT_SECONDS = 0.1`. Every conversational tick has a
real gap larger than any of these (typing time, model latency), so in
practice the clamp value *is* the per-tick dt, and the per-tick affect push
from unrelieved boredom grows as dt². Measured against the current drives:
at 2.0 the emotion axis reaches −1 within six exchanges (the prose hint
would turn her terse for the rest of the conversation); at 0.1 the same six
exchanges move it under 0.01, which is exactly what the CLI path did before
CP-A. 0.1 preserves today's conversational behaviour to the decimal; any
larger value is a drive-retuning change wearing a config constant's
clothes. The cost is that the clamp engages on essentially every tick. The
log line records the raw elapsed time next to the applied dt, so an 8-hour
gap and a 3-second gap are still distinguishable in the log.

**Reversal:** change the constant, after the drives are retuned against
wall-clock time.

## dt uses wall-clock, not monotonic time

**Chosen:** `time.time()`. A laptop asleep overnight does not advance the
monotonic clock on every platform, and the gap that matters is the one the
machine slept through. A backwards step is logged and treated as dt = 0.

## A missing store is fatal; `--init-store` is the one way to create one

**Ambiguous:** change 5 says a startup failure exits nonzero; change 6 says
a failed schema check archives and recreates; the done-when says a renamed
store must make the daemon refuse to start. Read together: missing store →
fatal; present-but-wrong-schema → archive by rename, create fresh, continue.
That leaves no path for the very first run on a machine with no store.

**Chosen:** `python -m lyra_core --init-store` creates an empty store when
none exists, then continues startup. Without the flag a missing store is
always fatal, with the path in the final log line. Creation is deliberate
and visible; nothing appears on disk as a side effect of a typo in a path.

**Alternative:** create silently on first run. Rejected: it is the
fail-open the checkpoint exists to remove, and it makes "store missing"
and "fresh machine" indistinguishable.

## Which tables the schema check requires

**Chosen:** `atoms`, `vec_atoms`, `facts`, `traits` — what the hot path
(`add_turn` → atoms + vec_atoms; affect restore/persist → facts) and the
current retrieval (`get_top_traits` → traits; `search_episodes` → vec_atoms
join atoms) reference. The check runs on a read-only `sqlite_master` read
BEFORE `init_db`, because `init_db` would otherwise migrate a pre-atoms
store in place (decomposing the 29 essays into atoms), which is exactly the
migration the spec says is not worth doing. The old June store lacks
`atoms`/`vec_atoms`, so it is archived as `memory.db.2026-06-10.archive`
and a fresh store takes its place.

## The archive carries its WAL sidecars

**Chosen:** `-wal`/`-shm`/`-journal` files are renamed to the names SQLite
derives from the archive's filename, so opening the archive later applies
its WAL. Renaming only the main file would silently drop any committed
pages still in the WAL.

## Optional `session` field on the turn frame

**Ambiguous:** the frame shapes in the spec have no session field, but the
CLI has always had `--session` to resume a conversation and history.db is
keyed by session.

**Chosen:** `{"v":1,"type":"turn","text":...,"session":...}` with `session`
optional. A client that omits it gets one session per connection, generated
by the daemon. The CLI always sends one. A present-but-invalid session is
rejected like any other malformed field.

## Backend failure is not fatal; memory failure is

**Chosen:** on the turn path, an exception from `backend.chat` (the LLM
service) becomes `TurnRejected`, which the transport answers with an error
frame; the daemon stays up. Any other exception in the turn path (history
write, atom write, retrieval, affect) resolves `TurnServer.fatal`, the
client gets an error frame saying the daemon is stopping, teardown is
attempted, and the process exits 1. The user's turn is already in history
and the atom store by the time the model is asked; that is the record of
what happened.

## The vision tool loop runs in the daemon

**Ambiguous:** the tool loop lived in `Assistant` (CLI process) and the
CLI supplied `vision_fn` (HTTP to lyra-vision). With turns answered in the
daemon, the daemon must resolve `[TOOL:see:*]` before it can reply.

**Chosen:** `call_vision` moves to `runtime.py`; the daemon calls
lyra-vision. Vision unavailability stays what it was — reported as the
observation text — because "she asked to see and could not" is what
happened. The avatar, TTS and push-to-talk calls stay in the CLI: they are
the body in the room, not cognition.

## `LAYER1_FACTS` stays in `lyra/assistant.py`

**Chosen:** the orphaned `memory_bridge.py` (deletion deferred) imports
`DEFAULT_SYSTEM` from `lyra.assistant`; moving the text would force an
edit outside the FILES set. It is a string constant; the client module
constructs nothing. The daemon's `TurnHandler` imports it from there.

## Backend selection and `--list-backends`/`--list-models` move to the daemon

**Chosen:** the CLI no longer imports `lyra.backends`; `python -m lyra_core
--backend/--model/--list-backends` replaces `lyra --backend/...`. The
daemon loads `.env` the same way `main.py` does (twelve lines duplicated
rather than importing `main`, whose import runs the CLI's own setup).

## CLI commands dropped: /stream, /history, /clear, /session list, /backend, /model

**Chosen:** the protocol carries one reply per turn, so there is no
streaming. `/history`, `/clear` and `/session list` read or delete
history.db from the client; change 10 puts history writes in the daemon
and `/clear` was a deletion path. `/backend` and `/model` describe a
choice the daemon now makes. `/session`, `/session new`, `/session <id>`,
`/voice`, `/help`, `/quit` and push-to-talk remain.

## The CLI reads stdin as lines when it is not a terminal

**Chosen:** `readchar` needs a tty; piped input previously crashed the CLI.
Line reads (no push-to-talk) when `stdin.isatty()` is false. Needed to
drive the done-whens from scripts.

## Exiting promptly when the daemon dies at the prompt

**Chosen:** keyboard input runs on a daemon thread; the main loop waits on
either the input future or the client's `closed` event. On `closed` the CLI
restores the terminal's line discipline (readchar leaves it raw during a
keypress) and exits 1. A pool thread from `asyncio.to_thread` would have
been joined at interpreter exit and hung until a key was pressed.

## Files touched outside the FILES set

- `lyra_core/interface.py` — change 5 names `_ingest_sensory` by name. The
  `try/except RuntimeError: pass` around the memory write is removed; no
  other edit.
- `tests/test_core.py` — nine tests fed sensory observations into an
  unstarted default `MemorySystem` and relied on the swallow. They now use
  the file's own `_RecordingMemory` fake; the "constructs its own core"
  test proves construction with an empty tick.
- `tests/test_runtime.py`, `tests/test_assistant.py`,
  `tests/test_cli_stream.py` → `tests/test_cli.py` — their subjects were
  rewritten; the tests are rewritten against the new behaviour. Tool-loop
  tests moved from the assistant to the daemon's `TurnHandler` unchanged
  in what they assert.
- `tests/test_outcomes.py`, `tests/test_actuators.py` — the `CoreSink`
  tests are removed with `CoreSink`; nothing else in those files changed.
- `tests/conftest.py` (new) — selects the offline embedder when MiniLM is
  not cached, exactly as `lyra-memory/tests/conftest.py` does; the daemon
  writes an atom per turn and an atom write embeds.
- `tests/test_transport.py` (new) — the transport's own tests.

## Daemon log file

**Chosen:** the daemon logs to stderr and to `~/.lyra/lyra_core.log`
(`--log-file` overrides). "Grep the log for CORE_CONSTRUCTED" needs a log
that outlives the terminal.

## Two launch surfaces outside the FILES set

`CLAUDE.md` (commands block) and `start-lyra.ps1` (`-Chat` branch) both
invoked `lyra --backend ... --model ...`, which the client no longer
accepts; the house machine's launcher would have failed on its first
`-Chat` after this checkpoint. Minimum edit in each: the backend and model
flags move to the `python -m lyra_core` invocation, `lyra` is run bare.
Nothing else in either file changed. `docs/PICKUP.md` is a dated snapshot
and is left as written.
