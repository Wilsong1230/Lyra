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

---

# CP-A.1 — affect/drive characterization

Measurement checkpoint: characterize the affect and drive integrators
against wall-clock time and tick granularity, and publish the numbers.
Nothing here corrects an integrator, retunes a drive, or changes
`MAX_TICK_DT_SECONDS`. `docs/AFFECT_CHARACTERIZATION.md` is the
deliverable; this section covers the ambiguities in producing it.

## Reading drive pressure in `TurnHandler.tick()` without touching `interface.py`

**Ambiguous:** the per-tick log line change asks for "each drive's
pressure," but `CognitiveCore.tick()` returns only `(intents, affect)` —
pressure never leaves the core. `lyra_core/interface.py` (where
`CognitiveCore` and its `_boredom`/`_relational` attributes live) is not
in this checkpoint's FILES set, so adding a public pressures accessor
there is out of scope.

**Options:** (a) add `CognitiveCore.pressures` (touches `interface.py`);
(b) drop the pressure fields from the log line; (c) read
`self._core._boredom.pressure` / `self._core._relational.pressure`
directly from `runtime.py`, reaching past the leading underscore.

**Chosen:** (c). `getattr(self._core, "_boredom", None)` /
`getattr(..., "_relational", None)`, each defaulting to `float("nan")` if
absent, so a fake core in a test that carries neither attribute logs
`nan` instead of raising. It is a private-attribute reach-through, which
would not be the right call if `interface.py` were open — a real
accessor belongs there — but given the closed set it is the smaller,
reversible option: nothing in `interface.py` changes, and the reversal
(once `interface.py` reopens) is deleting these two `getattr` lines in
favor of a proper `CognitiveCore.pressures` property.

## The per-tick log line: one format, not two

**Ambiguous:** the pre-CP-A.1 code logged two different message shapes —
`DT_CLAMP_ENGAGED elapsed=... dt=... | tick source=... valence=... arousal=...`
when clamped, `tick source=... dt=... valence=... arousal=...` otherwise.
Change 1 asks for "one line, parseable, stable field names," which a
branch on message shape does not give.

**Chosen:** one `log.info` call, one field order, used on every tick.
The only thing that varies is the leading token — `DT_CLAMP_ENGAGED` when
`clamped` is true, `tick` otherwise — so `grep DT_CLAMP_ENGAGED` keeps
working exactly as before, while every line also carries `clamped=True`/
`clamped=False` explicitly (so a parser does not have to infer clamp
state from which token happened to lead the line). Fields, in order:
`source`, `elapsed`, `dt`, `clamped`, `boredom_pressure`,
`relational_pressure`, `emotion_v`, `emotion_a`, `mood_v`, `mood_a`,
`temperament_v`, `temperament_a`, `temperament_c`. `%.6f` throughout
(the prior code used `%.1f`/`%.3f`); the extra precision costs nothing
and the probe's own numbers (docs/AFFECT_CHARACTERIZATION.md) need it at
the small end (dt=0.1 conversational ticks move valence by ~1e-4).

**Mood and temperament can be `None`:** `AffectState.mood` and
`.temperament` are typed `AffectVector | None` (interface.py's own
docstring still calls them "RESERVED, None until Phase ?", though the
real `AffectEngine.state` has populated both unconditionally since
before this checkpoint). A fake core's default `AffectState()` still
gets `None` for both. `TurnHandler.tick()` substitutes a neutral
`AffectVector()` when either is `None` so the log line never raises on
a fake affect state that only sets `emotion`.

## No new log-verbosity constant in `config.py`

**Ambiguous:** change 1 says "log verbosity constant if needed."

**Chosen:** not needed, so `config.py` is untouched. The daemon already
logged exactly one INFO line per tick before this checkpoint (one of two
possible shapes); the change here makes it one line of one shape with
more fields, not more lines or a higher default level. Nothing about tick
volume changed, so there is nothing for a verbosity knob to gate.

## `tools/affect_probe.py` lives at the repo root, not under `lyra_ai/`

**Chosen:** `docs/` is already at the repo root (predates CP-A), so
`tools/` sits next to it rather than under `lyra_ai/`, which has its own
`pyproject.toml`/`tests/` and is one of five sibling service directories.
The probe needs no install: it inserts `lyra_ai/` onto `sys.path` and
imports `lyra_core.affect`, `lyra_core.drives`, `lyra_core.config`
directly. Those three modules (and `lyra_core.interface`, pulled in
transitively for `AffectState`/`AffectVector`) have zero third-party
dependencies, so `python tools/affect_probe.py` runs with the system
interpreter alone — no venv activation, no `lyra_memory`, no daemon.

## What "saturation" means for the probe

**Ambiguous:** change 3c asks for "the wall time at which each axis first
reaches saturation." Nothing in `affect.py` clamps `_emotion_v`/
`_emotion_a` to any range — there is no engine-enforced saturation point.

**Chosen:** `|value| >= 1.0`, stated as a measurement convention in the
document, not a property the code guarantees — matching the `[-1, 1]`
range `prose_hint` and the affect tests already treat valence/arousal as
living in.

## Test file touched outside the FILES set

**`lyra_ai/tests/test_runtime.py`:** `test_tick_logs_the_clamp_with_the_raw_gap`
asserted the old two-shape format's exact substrings
(`"elapsed=28800.0s"`, `"valence="`) — those exact strings no longer
appear under the new unified format's field names and precision. Updated
in place to assert the new format, plus one new test
(`test_tick_logs_one_stable_line_per_tick_whether_or_not_clamped`)
covering the unclamped branch and the full new field set, following the
CP-A precedent of updating tests outside the closed set as the direct,
minimal consequence of an in-set behavior change — no other test in the
file changed.

---

# CP-A.2 — exact coupling, bounded pressure, a measured clamp

Correction checkpoint, built directly on CP-A.1's measurements. Register
correction carried in: temperament is static under `tick()` — two dynamic
timescales (emotion, mood) and one constant, not three.

## The exact 2x2 solve (item 1)

**Ambiguous:** "replace the Jacobi update... with the exact solution of the
2x2 system" specifies the destination, not the derivation.

**Derivation, recorded here because it is not obvious from the code:** for
`e' = forcing + k_e(m-e)`, `m' = k_m(e-m)`, decompose into `S = k_m*e + k_e*m`
and `D = e - m`. `S` has no restoring term (`k_m*e' + k_e*m' = k_m*forcing`
identically, for any `e`, `m`), so it is exactly conserved when
`forcing = 0` and integrates a constant forcing exactly over any interval:
`S(dt) = S0 + k_m*forcing*dt`. `D` decouples into a single ordinary
relaxation, `D' = forcing - r*D` with `r = k_e + k_m` (the register's
`1/tau_e + 1/tau_m`), whose exact solution is `D(dt) = D0*(1-relax) +
(forcing/r)*relax` with `relax = 1 - e^(-r*dt)` — the same
never-overshoots-at-any-dt form CP-A already used for a single variable,
now applied to the pair's own difference channel. `e` and `m` are read back
off `(S, D)` algebraically. No substep; `_step()` is one closed-form
evaluation regardless of how large `dt` is. Implemented in `affect.py`
(`AffectEngine._step`), full derivation in that method's docstring.

**Verified exact:** `docs/AFFECT_CHARACTERIZATION.md` 3a — the same span at
dt=0.1, 2.0, 60.0, and one single tick agree to ~1e-15 (float noise), down
from CP-A.1's measured 0.4-0.5 spread with the Jacobi split.

## Reading pressure as forcing, not as a second Euler input (item 3)

**Ambiguous:** "pressure enters affect as a rate, integrated once over the
step, not accumulated by dt and then integrated by dt again" describes a
symptom (dt appearing to scale a quantity twice), not a specific code
change distinct from item 1.

**Finding:** there is no separate literal bug to remove beyond item 1 —
`BoredomDrive.update` advancing pressure by `dt` and `AffectEngine.update`
advancing emotion by `dt` are two different state variables each correctly
integrated once; that is ordinary cascaded-ODE structure, not a double
integration of the same quantity. What made it *look* and *behave* like a
dt² blowup was the combination CP-A.1 measured: pressure unbounded (so the
forcing kept growing across the whole trajectory) feeding the *broken*
Jacobi coupling (whose own error compounds badly at large dt). Item 1 (this
checkpoint) makes the affect side exact; item 2 (below) bounds the forcing.
Together, the drive-to-affect path now treats `push.valence_delta` as
`forcing`, held constant across one step and integrated exactly by `_step`
(`S(dt) = S0 + k_m*forcing*dt`, not `push*dt` bolted onto a Jacobi update) —
which is the literal reading of "pressure enters affect as a rate,
integrated once." No further code beyond item 1's `_step` was needed to
satisfy item 3; there is no line left in `affect.py` or `drives.py` that
scales a state variable by `dt` twice for the same conceptual quantity (see
the "dt consumption sites" table in the regenerated document).

**Residual, disclosed rather than hidden:** the *coupled* measurements (3c)
do not agree across tick rates to floating-point precision the way the
uncoupled ones (3a, 3b) do — a small, constant, non-growing residual
(~5.6e-05 valence at `_BOREDOM_PRESSURE_CEILING = 0.02`) remains, because
pressure ramps from 0 to its ceiling over `ceiling/idle_rate` seconds and a
coarse dt resolves that one ramp less precisely than a fine dt does; since
`S` never decays, that resolution difference is never erased. This is
qualitatively different from CP-A.1's finding (bounded and flat vs.
unbounded and accelerating) and is exactly what item 5's agreement check
measures and reports rather than asserting away.

## Bounding pressure: the ceiling is sized to `prose_hint`, not to `|value| < 1` (item 2)

**Ambiguous:** "bound drive pressure... approach a ceiling... record the
ceiling as a named constant" gives no value. The done-whens name two
different bars: "affect values in range, not saturated" (8h-idle scenario)
and "does not turn her terse" (six-exchange scenario) — these are not the
same threshold.

**First attempt, and why it was wrong:** `_BOREDOM_PRESSURE_CEILING = 0.5`
(exact exponential approach, `dPressure/dt = idle_rate*(1-pressure/ceiling)`,
so `idle_rate` is preserved as the slope at pressure=0) keeps a single
worst-case tick at the new clamp comfortably under `|value| < 1`. But the
six-exchange conversation done-when isn't measured against `|value| < 1` —
it's measured against whether the daemon actually goes terse, which is
`lyra_core.expression.prose_hint`'s `_VALENCE_THRESHOLD = 0.3` on the
*blended* `emotion.valence + mood.valence`, a threshold three times
tighter, on a summed (not single-axis) quantity. At ceiling=0.5 the probe's
six-exchange scenario produced blended valence -4.46 — deep in
`prose_hint`'s terse region, failing the done-when outright despite passing
the naive saturation check.

**Chosen:** `_BOREDOM_PRESSURE_CEILING = 0.02`, found by probing candidate
ceilings against the *actual* `prose_hint` output on the six-exchange
scenario (not against a hand-derived proxy threshold) until the hint
stopped firing with margin: blended valence -0.18 at ceiling=0.02, vs. the
-0.3 trigger. `docs/AFFECT_CHARACTERIZATION.md` 3e now calls `prose_hint`
directly on the final state and reports the actual hint string, so this is
checked against the real function, not reimplemented in the probe.

**Reversal:** the constant is named and isolated (`drives.py`); changing it
is a one-line edit plus a probe re-run, no integrator change required.

## `MAX_TICK_DT_SECONDS = 600.0` (item 4)

**Chosen:** 600 s (10 minutes), against two measured constraints in
`docs/AFFECT_CHARACTERIZATION.md` ("Clamp safety"):
1. It must cover every gap this checkpoint's done-when calls "ordinary"
   (5 s, 60 s, 10 min) without clamping — verified against a live daemon,
   not just the probe (three ticks, `clamped=false` on all three).
2. A single worst-case tick at this dt — pressure already at its ceiling
   from prior idling, applied for the whole 600 s — must not saturate
   (`|value| >= 1`) either affect axis. Measured: valence -0.22, arousal
   0.11, both well inside range.

With `_BOREDOM_PRESSURE_CEILING` sized to the tighter `prose_hint`
constraint (above), the `|value| < 1` constraint on a single clamped tick
turned out not to bind until much larger clamp values (the "Clamp safety"
table shows candidates well past 600 s before `saturated?` flips to
`True`), so 600 s was chosen directly against "ordinary gaps never clamp"
rather than pushed up against a saturation ceiling. Verified against a live
daemon: 8h+ idle then one turn — `elapsed=28800.000000 dt=600.000000
clamped=True`, `emotion_v=-0.461763 emotion_a=0.230882`, both in range.

**Not derived analytically:** the coupled forcing's `S` channel has no
restoring term (see item 1's derivation) — under *sustained* nonzero
forcing there is no dt or ceiling that keeps it bounded forever, only
values that keep it small over the specific, finite scenarios this
checkpoint's done-when actually names. 600 s is evidence-based for those
scenarios, not a proof for arbitrary future ones; a future checkpoint
retuning the drives or extending the clamp further should re-run the probe
against whatever new scenarios it needs to cover, the same way this one
did.

## What CP-A.2 did not touch

`RelationalDrive` needed no change: CP-A.1 already found it bounded to
`[0, 1]` by construction (a function of elapsed time since last recurrence,
not an accumulator) and tick-rate invariant. `BoredomDrive`'s relief branch
(`pressure -= relief_rate*dt`, floor-clamped at 0) is unchanged — item 2
named only idle accumulation, and the floor plus the new ceiling already
bound it on both sides. `encourage_remaining`'s countdown is unchanged — a
state-independent linear decrement, exact at any dt on its own, not part of
the emotion/mood coupling or the drive-to-affect path either item 1 or item
3 touches. Temperament is untouched per OUT OF SCOPE.

## Reading "(add the agreement check only)" against a regenerated document

**Ambiguous:** the FILES set annotates `tools/affect_probe.py` with "(add
the agreement check only)", but change 4 requires "a value the probe
shows is safe" and `docs/AFFECT_CHARACTERIZATION.md` is separately listed
as "(regenerated)". The existing narrative text (e.g. 3a's "the four do
NOT agree", 3b's "Bounded: no", the DT_SITES table's line numbers and
classifications) describes the pre-CP-A.2 code; left as-is, a regenerated
document produced by re-running the same script would state things about
the *current* code that are now false.

**Chosen:** added `agreement_checks()`/`measure_clamp_safety()` (the two
new pieces of evidence items 4 and 5 require) and updated the surrounding
prose and the `DT_SITES` table to describe what the current code actually
does — not a restructuring of the measurement methodology (same spans,
same rates, same scenario functions throughout). The alternative — leaving
stale text in place — would make the regenerated document self-
contradicting (numbers from the new code next to prose describing the old
code's bugs as current). "Add the agreement check only" is read as scoping
*new measurement machinery*, not as license to publish a document that
misdescribes the code it just measured.

## Files touched outside the FILES set

- `lyra_ai/tests/test_affect.py` — one test
  (`test_encouragement_expires_after_duration_then_full_rate_resumes`) hand-
  derived its expected value from the old per-variable Jacobi formula; it
  now derives the same expectation from the exact 2x2 solve (same
  discrimination — full rate vs. half rate — different closed form). Two
  new tests added: `test_relaxation_agrees_across_tick_granularities` (3a as
  a unit test) and `test_coarse_dt_converges_toward_mood_not_starting_value`
  (the swap-case fix, directly). All other tests passed unmodified — the
  new integrator is qualitatively closer to the old one's intent (monotonic
  convergence, no oscillation), not a behavior change most tests could see.
- `lyra_ai/tests/test_drives.py` — no changes. Every existing test asserts
  qualitative properties (monotonic increase, bounded comparisons) that the
  bounded exponential-approach still satisfies; none hard-coded a specific
  pressure value that the ceiling would change.

---

# CP-B — the memory swap

Register: `lyra_memory.store.Store` (`store.db` + `runs.db`, hybrid FTS5 +
vector retrieval, retrievability-based forgetting) was already built and
already tested against itself (`store/evaluate.py`, `lyra-memory/tests/`),
but nothing in the daemon process ever constructed one. The daemon ran on
`lyra_memory.MemorySystem` (`memory.db`, vector-only retrieval, no
forgetting) the whole time. This checkpoint's only job is the wiring: make
`Store` the daemon's one memory, leave `MemorySystem` alone in the tree.

## 1. Removing `interface.py`'s `MemorySystem` default

**Ambiguous:** `CognitiveCore.__init__`'s `memory=None` fallback constructed
`MemorySystem()` — cheap and synchronous. `Store.open()` is async and does
real I/O (schema assertion, WAL pragmas, `runs.db`), so it cannot replace
that fallback in kind: `CognitiveCore.__init__` is a plain `__init__`, not a
coroutine.

**Options:** (a) make `memory` a required constructor argument; (b) default
to `None` and make every place that touches `self._memory` tolerate it being
absent; (c) keep a *different* free default (e.g. a tiny in-process stub
built just for this).

**Chosen:** (b). `_ingest_sensory`, `_ingest_outcome` (via the consolidator's
existing `getattr(self._memory, "candidate_pool", None)` guard), and
`_get_promoted_traits` (`getattr(..., "identity_engine", None)`) were
already either the only places that touch memory or already duck-typed
against its absence. Checked every current bare `CognitiveCore()` /
`Harness()` call site in `tests/test_core.py` before making this change:
every one of them either never ticks, or only ticks empty observation
lists — none exercises `_ingest_sensory` on the no-memory default. So (b)
costs nothing on the existing suite and is honest about what changed: a
core built with nothing injected has no memory, full stop, rather than
silently reaching for a store the daemon process never opens. (a) would
have broken `Harness()`'s and several tests' no-arg construction for no
functional gain (they never rely on there being a *working* memory, only
on there being *something* that doesn't crash on `introspect()`/empty
`tick()`); (c) invents a class this checkpoint doesn't need.

`tests/test_core.py`'s `_RecordingMemory`/`_FakeMemory` and the
`test_harness_constructs_own_core_when_none_given` docstring were updated to
match (see "Files touched outside the FILES set" below) — not in the closed
FILES set, but the fakes exist to speak whatever protocol `interface.py`
actually calls, and the docstring said something no longer true.

## 2. `obs.source` → Store's `speaker`/`source` (change 2)

**Ambiguous:** the checkpoint says "one atom per turn," which the existing
tick structure already delivers (`TurnHandler.tick` calls `core.tick()` once
per user turn, once per Lyra turn, once per vision result — one
`Observation` per call, so one atom per call maps directly onto
`Store.append_atom`). What it doesn't say is how `obs.source` (`"conversation"`
| `"lyra"` | `"vision"`, a "who produced this" label local to `lyra_core`)
maps onto Store's two *separate* enforced vocabularies: `speaker` (`wilson`
| `lyra` | `system`) and `source` (`cli` | `wakeword` | `ambient` | `vision`
| `sandbox_read`, a channel).

**Chosen:**
| obs.source      | speaker  | source |
|-----------------|----------|--------|
| `"conversation"`| `wilson` | `cli`  |
| `"lyra"`        | `lyra`   | `cli`  |
| `"vision"`      | `system` | `vision` |
| anything else   | `system` | `cli`  |

`"cli"` because the daemon's one transport today is the `lyra` CLI talking
over `lyra_core.transport` — there is no other channel to name. A vision
result is attributed to `system`, not `wilson`: it's Lyra's own tool call
seeing the world, not something Wilson said, and it wasn't `wilson` in the
old `MemorySystem` path either (`add_observation`, not `add_turn`, kept it
out of the user/lyra turn pair). The catch-all row exists because
`validate_atom` raises loudly on an out-of-vocabulary `source`
(`SOURCES`), not because any current caller sends anything else — `TurnHandler`
only ever emits `"conversation"`, `"lyra"`, `"vision"`.

## 3. `affect_state` has no table of its own

**Not named by the checkpoint text, but forced by "no calls into `structured_state`."**
The old `CognitiveCore.start()`/`stop()` persisted `affect_state` through
`MemorySystem.structured_state` (`StructuredState.get_fact`/`set_fact`,
backed by `db.py`'s `facts(key, value, updated_at)` — a genuine key-value
table). `Store` has no `structured_state` and no key-value table:
`store/schema.py`'s `facts` table is `subject + free text`, a table of
semantic facts about the world, and `schema_meta` is explicitly the store's
own bookkeeping (schema version, embedder id) — OUT OF SCOPE forbids adding
a table, and repurposing `schema_meta` for application state would be
misusing a table documented as "what the schema looks like," not "what she
last felt."

**Chosen:** persist `affect_state` as a row in Store's own `facts` table,
under `subject = "_lyra_internal_affect_state"` — a subject no real query
will ever type (`_facts_block`'s subject match requires the literal token
in the user's message), `source_kind = "inferred"` (the closest fit among
the four allowed values: it's Lyra's own internal state, not something
anyone told her), and the same "supersede by `valid_until`, current row has
`valid_until IS NULL`" pattern the `facts` table already uses for everything
else. `CognitiveCore.start()`/`stop()` reach this via
`getattr(self._memory, "db", None)` — the same duck-typing style already
used for `structured_state` — so a `structured_state`-bearing fake still
takes that path unchanged, and only a `Store` (or anything exposing `.db`
with the same schema) takes the new one. Verified live: three chat turns,
`stop()`, restart against the same `store.db`, affect matched to float
precision (`test_affect_persists_across_daemon_restarts`, unmodified from
before this checkpoint).

## 4. Trait promotion and outcome consolidation go inert under Store (not a regression to fix here)

`_get_promoted_traits` reads `identity_engine`; the consolidator's
`record_outcome` reads `candidate_pool`. Both are `getattr(..., None)`
already — `Store` has neither attribute, so both silently stop doing
anything the moment `MemorySystem` stops being the daemon's memory. This is
exactly what OUT OF SCOPE means by "trait dedup, salience scoring,
consolidator behavior" and "intent execution" being out of scope: nothing
in `Store`'s own tree (`store/`) builds an equivalent of `IdentityEngine` or
`CandidatePool` against it, so there is nothing this checkpoint could wire
even if it wanted to. `Store`'s schema still has `traits` and `candidates`
tables (REQUIRED_TABLES enforces their presence) — they just stay empty
until a later checkpoint gives something a reason to write to them.

## 5. REQUIRED_TABLES: distinguishing names, not exhaustive validation (change 3)

**Chosen:** `{atoms, atoms_fts, vec_atoms, facts, commitments, outcomes,
traits, candidates}` — Store's tables the daemon's own code paths touch,
named specifically so a store shaped like the *old* `memory.db` schema can
never satisfy the check by accident: the old schema has no `atoms_fts`,
`commitments`, or `outcomes` table at all, so any of the three alone is
enough to catch it. This is a coarse, name-only gate (matching CP-A's
existing `store_tables()`/`sqlite_master` mechanism) — the *real*,
structural validation is `Store.open()`'s own `assert_schema()`
(`store/integrity.py`), which diffs columns, triggers, indexes, schema
version, and embedder id, and which this checkpoint does not touch (OUT OF
SCOPE: "Store is the spec; wire it as built"). `prepare_store()`'s job is
only to decide *whether to archive-and-recreate* before that stricter check
ever runs — it does not need to duplicate it.

## 6. Archiving a leftover `memory.db` on cold start (change 4)

**A real hazard caught before it shipped:** the daemon's *checked* path is
now `STORE_PATH` (`store.db`), a different file from the legacy `DB_PATH`
(`memory.db`) entirely — `prepare_store()` never looks at `memory.db` as
part of validating the store it's about to open. But the done-when is
explicit that a machine with *only* `memory.db` present must have it
archived on cold start, specifically so it can't be mistaken for the live
store by a human poking around `~/.lyra/`. That needs its own step,
`archive_legacy_memory_db()`, called at the top of `prepare_store()`,
independent of and before the `STORE_PATH` check.

**A second hazard, caught in review before running any test:** a first
draft read the legacy path from the module-level `DB_PATH` constant
directly (`Path.home() / ".lyra" / "memory.db"`) with no way to override it.
Every test that exercises `prepare_store()` or constructs a `Runtime` would
then have reached into the *real* `~/.lyra/memory.db` on whatever machine
runs the suite — on a real dev machine or this container alike, silently
renaming actual daemon data as a side effect of running `pytest`. Fixed by
making the legacy path a parameter (`prepare_store(..., legacy_path=DB_PATH)`,
`Runtime(..., legacy_db_path=DB_PATH)`) that defaults to the real constant
for production use but that every test points at a `tmp_path` file instead.
Confirmed live in this container: an actual `/root/.lyra/memory.db` left
over from an earlier bootstrap/test run *did* exist, and running
`python -m lyra_core` for real (no test, no override) archived it correctly
to `/root/.lyra/memory.db.2026-09-02.archive` — nothing lost, nothing
deleted, exactly the intended behavior when it's really the production path
being exercised.

## 7. `runs.db` (change 5)

No code needed: `Store.open()` already opens/creates `runs.db` alongside
`store.db` as part of its own `_connect()`. Nothing here writes to it (no
telemetry added, per OUT OF SCOPE) — its existence is a side effect of
opening the store, not a step this checkpoint performs. `Runtime.start()`
logs its path explicitly (`"runs store open at %s"`) so the done-when's
"all three paths appear in the log" has something to point at beyond
`Store.open()`'s own silence.

## 8. `trait_history`: the checkpoint's premise was wrong (change 6)

**Finding:** `db.py` has `trait_history`; so does `store/schema.py`'s
`COLD_SQL` (same intent — ts, trait_id, trait_label, event,
conf_before/after, tier_before/after, evidence_count, dream_id — referencing
`traits(id)` and `dreams(id)` instead of a bare integer). Nothing is
missing. The register's "db.py has the table, Store does not" is incorrect
as written; corrected here per the checkpoint's own instruction to record
the finding either way. The table sits empty under `Store` for the same
reason `traits`/`candidates` do (see item 4 above): nothing writes to it
without an `IdentityEngine` wired against `Store`, which is out of scope.

## 9. `DB_PATH` stays (change 9)

**Checked, not assumed:** grepped every importer of
`lyra_memory.config.DB_PATH` before deciding. It is not "`memory_bridge.py`
is the only remaining importer" — `MemorySystem` (`lyra_memory/__init__.py`),
`retrieval.py` (both `search_episodes`'s default path and `get_fact`), and
`inspect_state.py` all import it directly, on top of `memory_bridge.py` and
`lyra-memory`'s own test suite. All of those are explicitly staying in the
tree unchanged (OUT OF SCOPE: "Deleting MemorySystem, retrieval.py, or
db.py from lyra_memory... they stay in the tree"), so change 9's stated
condition for deletion never holds. Left `DB_PATH` in place with a comment
explaining why (`lyra-memory/lyra_memory/config.py`); no code deleted.

## 10. `evaluate.py`'s live-store mode (change 7)

Added `--store PATH` to the CLI (`store/evaluate.py`'s `main()`), which
skips seeding a throwaway corpus and scores `labeled["turns"]` against
whatever the given `store.db` already has — exactly what `evaluate()`
already did whenever it was called with a `store=` argument (`owned =
store is None` gates the seeding step; that branch was already there,
unused by the CLI). No change to `TurnResult`, `Report`, or any scoring
logic — "harness wiring only," per the FILES annotation.

**Baseline recorded, evidence not aspiration:** ran it for real against a
`Store` populated by ten real chat turns through a live `Runtime` (the same
one exercised for the done-when's atom-count and forgetting checks — see
below), with a five-query labeled set covering four real topics from those
turns plus one deliberately unanswerable query (`expect_miss: true`):

```
coverage                 1.00
leak rate                0.00
spurious miss rate       0.00
spurious injection rate  0.00
5 turns
```

This is a wiring proof, not a tuned baseline: five hand-written queries
against a ten-turn corpus, under the hashed offline embedder (this
container has no path to huggingface.co — see item 11). The number to
compare future retrieval changes against should be re-measured against a
larger, real-conversation corpus with the real MiniLM embedder before it's
trusted as a regression gate; what's proven here is that `--store` reaches
the daemon's actual schema and actual accumulated turns end to end, per
change 7's ask.

## 11. `conftest.py` opt-in only, and the daemon's own fatal warmup (change 8)

**Chosen:** `tests/conftest.py`'s cache-miss probe (`_real_model_is_cached()`,
auto-setting `LYRA_EMBED_BACKEND=hashed` whenever MiniLM wasn't cached) is
removed outright rather than replaced with a different auto-detection —
`lyra_memory.embeddings._backend()` already reads `LYRA_EMBED_BACKEND` from
the environment at call time with no help from `conftest.py`, so "opt-in by
explicit env var" needs nothing beyond *not auto-setting it*. The file is
now doc-comment only. Every test invocation in this checkpoint (and every
one going forward, in this container) sets `LYRA_EMBED_BACKEND=hashed`
explicitly on the command line, which is the opt-in the checkpoint asks for.

The daemon side needed an actual code change, not just a deletion:
`MemorySystem.start()` used to call `embed("warmup")` itself, which is how
a missing/uncached MiniLM used to become a startup failure. `Store.open()`
has no equivalent warmup — embedding only happens lazily, on the first real
`append_atom`. Added an explicit `await embed("warmup")` to
`Runtime.start()` right after `Store.open()` succeeds and before
`construct_core()`, wrapped to close the just-opened store and re-raise a
`StartupError` naming `EMBED_MODEL` explicitly (rather than trusting
whatever exception text `sentence_transformers`/`huggingface_hub` happens to
raise to "name the cause") — `__main__.py`'s existing `except Exception as
exc: log.critical("FATAL: %s", exc)` then makes that the final log line, as
the done-when requires.

**Verified live, in this container** (no test mock — a real attempted
network call, actually blocked by the proxy): `Runtime.start()` with
`LYRA_EMBED_BACKEND` unset and a fresh store raised
`StartupError: embedding model unavailable (all-MiniLM-L6-v2): ...`
before `construct_core()` ever ran (`CORE_CONSTRUCTED` does not appear in
the log for that run) — exits nonzero, names the model, no fallback.

## What CP-B did not touch

`Store`'s own files (`store/schema.py`, `store/context.py`,
`store/__init__.py`, `store/passes/*`, `store/integrity.py`) — zero edits,
per OUT OF SCOPE. `MemorySystem`, `lyra_memory/retrieval.py`, `lyra_memory/db.py`,
`lyra_memory/atoms.py` — zero edits; still imported by
`lyra_ai/lyra/memory_bridge.py` (CLI-only path, untouched) and by
`lyra-memory`'s own test suite, both of which pass unmodified (283 passed,
4 skipped — pre-existing skips, unrelated to this checkpoint). Intent
execution, the perception loop, `OutcomeConsolidator` behavior, trait dedup,
salience scoring — all still exactly as inert/unwired as they were before
(see item 4).

## DONE-WHEN — evidence

All of the following were run live against real `Store`/`Runtime` instances
(a fake LLM backend; everything else — sqlite, sqlite-vec, aiosqlite, the
hashed embedder, the real async event loop — real), not asserted from
reading the code:

- **Cold start, only `memory.db` present:** a pre-atoms-era `memory.db` was
  placed at a fresh `Runtime`'s `legacy_db_path`, nothing at its `db_path`.
  `Runtime.start()` archived the legacy file to
  `memory.db.<date>.archive`, created `store.db` and `runs.db`, and
  started; all three paths appeared in the log
  (`test_runtime_cold_start_archives_legacy_memory_db_and_logs_all_three_paths`,
  plus the same sequence run standalone outside pytest).
- **Ten turns through the CLI:** a real `LyraClient` sent ten `chat()` calls
  through a real `Runtime` over a real loopback socket. Live count:
  `atoms=20 atoms_fts=20 vec_atoms=20` (ten user + ten Lyra atoms; every
  count matches, as the done-when requires).
- **Lexical hit, vector miss:** an atom burying the token `atoms_fts` in
  otherwise-unrelated text, queried with a differently-worded question
  sharing only that token. Live: `_lexical_hits` found it (BM25 match on
  the exact token); `_semantic_hits` did not (cosine similarity fell under
  `SEMANTIC_SIMILARITY_FLOOR = 0.35` — the hashed embedder still rewards
  exact token overlap enough that a short, mostly-shared query finds it
  either way; this pair needed enough unrelated filler on both sides to
  dilute that overlap below the floor).
- **Forgetting pass, retrievability strictly decreases:** `ForgettingPass`
  run twice against the same live store, 60 days apart (`now` advanced, no
  re-access in between). Live: `retrievability` on the same atom went
  `1.000000 -> 0.680712`.
- **`evaluate.py` against the live store reports coverage:** see item 10
  above — `coverage 1.00` against the ten-turn store, recorded as the
  starting baseline.
- **Grep clean:** `grep` for actual `import` statements (not comments —
  several docstrings *mention* `MemorySystem` by name, which is not the
  same thing) matching `MemorySystem` or `lyra_memory.retrieval` under
  `lyra_ai/`, excluding `tests/` and `memory_bridge.py`: zero results.
- **MiniLM uncached, offline var unset:** see item 11 above — exits
  nonzero, final log line names `all-MiniLM-L6-v2`, no fallback.

## Files touched outside the FILES set

- `lyra_ai/tests/test_core.py` — `_RecordingMemory`'s `add_turn`/
  `add_observation` replaced with `append_atom` (recording
  `(speaker, source, text)`), matching `interface.py`'s new call; the three
  ingest-routing tests updated to assert the new speaker/source mapping
  (item 2 above), plus a fourth added for the `"vision"` case that didn't
  have its own test before. `test_harness_constructs_own_core_when_none_given`'s
  docstring corrected — it described a "self-constructed core owns an
  unstarted MemorySystem" that no longer exists (item 1 above); the test's
  behavior (one empty tick, no memory touched) is unchanged.
- `lyra_ai/tests/test_runtime.py` — extensive rewrite: every atom/fact
  assertion now reads Store's schema (`atoms.speaker`/`.text`, not
  `.role`/`.content`; `facts.subject`/`.text`/`valid_until`, not
  `.key`/`.value`) instead of the old `memory.db` shape;
  `_no_context()`/`build_context` patches retarget
  `lyra_core.runtime.build_context` (now `store.context.build_context`,
  returning a `ContextResult` with `.text`, not a bare string) instead of
  `lyra_core.runtime.retrieval.build_context`; `construct_core`'s log test
  reads `memory.path` (Store's attribute) instead of `memory._db_path`
  (MemorySystem's); every `prepare_store()`/`Runtime(...)` call site passes
  a `tmp_path`-scoped `legacy_path`/`legacy_db_path` (item 6 above — this
  is not cosmetic, it is what stops the suite from touching a real
  `~/.lyra/memory.db`). Six new tests added for legacy-archiving behavior
  that didn't exist before this checkpoint
  (`archive_legacy_memory_db` directly, `prepare_store`'s integration of
  it, and the full cold-start-through-`Runtime` path). Every other test's
  *assertions* are unchanged from before CP-B — only the schema they read
  against moved.
