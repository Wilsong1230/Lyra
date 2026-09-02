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

---

# CP-C — the system reports on itself

Register: the memory redesign is built and live (CP-B). This checkpoint adds
no new capability to the daemon — it adds a way to SEE what the daemon
already does, because the last time nobody was looking (three months,
`MemorySystem` disconnected, zero symptom) is exactly the failure mode this
exists to catch before the intent loop closes at CP-D.

## 1. `report.py` cannot read `context_log` for "assemblies performed" — it is always empty

**Finding, not a bug in this checkpoint's code:** change 2c's natural
reading is a historical tally — count real retrieval assemblies that
happened during real turns, split by which path(s) contributed. `Store`
already has a table built for exactly this (`context_log`: `atom_ids`,
`fact_ids`, `dream_ids`, `budget_used`, `misses` — "written from day one,"
per its own docstring). But `context_log` is written by exactly one method,
`Store.ingest_turn()` (via `Store._log_context`), and CP-B's daemon wiring
never calls it: `TurnHandler.system_prompt()` calls the free function
`lyra_memory.store.context.build_context()` directly, and
`CognitiveCore._ingest_sensory` writes atoms one at a time via
`append_atom()`. Neither path touches `_log_context`. Verified live: a
daemon that served real turns through this exact code for the entire
duration of this checkpoint's testing has zero rows in `context_log`.

This is a real gap in the daemon's wiring, discovered by trying to build the
report change 2c asks for — precisely the class of "no symptom" failure
CP-C's WHY names. **Not fixed here**: the fix is a turn-path behavior
change (making `TurnHandler`/`CognitiveCore` log context assembly outcomes
somewhere), and `runtime.py`'s entry in this checkpoint's FILES set is
explicitly annotated `(periodic line emission)` — narrower than "wire
retrieval provenance into the turn path." `interface.py`, where the other
half of that fix would plausibly live, is not in this checkpoint's FILES
set at all. Flagging for CP-D or a dedicated follow-up rather than
expanding scope to fix it here.

**Chosen instead:** `report.py` performs its own live, read-only retrieval
self-test. It takes real atom text already sitting in the report's window
(the most recent `wilson`-speaker atoms, capped at 20) as query strings and
calls the same `build_context()` the daemon calls, through report.py's own
mode=ro connection — genuinely read-only (`build_context()` only ever
`SELECT`s), genuinely exercising the real retrieval code against real
stored content, but a measurement performed AT REPORT TIME against current
content, not a historical tally of turns that happened. `assemblies_total`
counts how many queries this pass ran (bounded by how much real content is
in the window — honestly 0 on an empty store, not fabricated), not how many
times a real user turn triggered retrieval. This is disclosed in
`report.py`'s own module docstring, not just here.

## 2. "Current affect" needed two different answers for two different callers

**Finding:** `CognitiveCore` persists `affect_state` to the `facts` table
in exactly one place — `CognitiveCore.stop()` (CP-B, item 3). A daemon
that has been running since it started and has not yet been cleanly
stopped has **never written that fact at all**. `report.py`'s read-only
connection (by design — a separate process, safe against a store a daemon
is actively writing) has no way to see live in-memory state; it can only
see what's on disk. Verified live: `python -m lyra_core --report` against
an actively-running, never-yet-stopped daemon reported
`emotion_v=unavailable` (and every other current-affect field) for the
entire life of the process — accurate, not a bug, but not useful either.

**Chosen:** the daemon's own periodic emission (`Runtime._emit_self_report`)
overlays the current-affect fields with `self._core.introspect()` —
already the exact port `interface.py`'s own docstring names for this:
"the port the telemetry/dashboard and Lyra's own self-reading use," reading
live affect without mutating anything. Everything else in the daemon's
periodic line (atoms, retrievability, the retrieval self-test, candidates,
outcomes, min/max, clamp stats, file sizes) still comes from
`collect_from_path()` — the identical read-only path `--report` uses.

This is a genuine, disclosed difference between the two callers for this
one field group only, not an inconsistency: the external CLI cannot do
better than "last persisted, possibly never," and the in-process daemon
task can do better because it holds the live core, and change 5's "the
same measurements... the same stable field names" is satisfied by both
still reporting the same fields, meaning the same thing, through whichever
source can actually answer them accurately. Verified live: the daemon's own
`SELF_REPORT` log lines show real, changing `emotion_v`/`emotion_a`/
`mood_v`/`mood_a` values turn over turn; a same-moment `--report` run
against the same never-stopped daemon still (correctly) shows
`unavailable` for those same fields.

## 3. "Candidates created" has no timestamp to window by

`Store`'s `candidates` table has `last_seen` but no creation timestamp —
unlike `atoms` (`ts`) or `trait_history` (`ts`), there's no column that
answers "created within the window." OUT OF SCOPE forbids changing Store's
schema. Reported as `candidates_total` (the current row count, all-time)
instead of a windowed "created" count, labeled as such in both the render
and this note. `candidates_promoted_window` (from `trait_history WHERE
event = 'promoted' AND ts >= window_start`) IS genuinely windowed —
`trait_history` has the `ts` column `candidates` lacks.

## 4. Ten turns write twenty atoms, not ten

**Finding, contradicting the checkpoint's own done-when wording:** "Ten
turns are taken. The report's atom count for today increases by ten."
Measured live, twice (once here, once already in CP-B's own verification):
ten `chat()` calls produce **twenty** atoms — `CognitiveCore._ingest_sensory`
writes one atom per `Observation` tick, and `TurnHandler.handle()` ticks
twice per exchange (`source="conversation"` for the user's message,
`source="lyra"` for her reply — see `runtime.py`'s `TurnHandler.handle`).
This is not new behavior CP-C introduced; it's how the daemon has ticked
since CP-A, and CP-B's own DECISIONS.md already recorded the same 1:2
ratio (`atoms=20` for ten `chat()` calls). Verified again here:
`atoms_today` went from 62 to 82 after ten turns in this checkpoint's live
run — +20, matching CognitiveCore's actual tick granularity rather than the
checkpoint text's assumption. `report.py`'s field is honest either way
(`atoms_today` just counts rows); this note exists so the delta a live
verifier sees isn't mistaken for a bug.

## 5. The retrieval split came out all-both — the reason, per the done-when's own allowance

Live run (ten real atoms in the window, `report.py`'s self-test against
them): `assemblies_total=11, retrieval_vector_only=0, retrieval_fts_only=0,
retrieval_both=11, retrieval_neither=0`. The done-when explicitly allows
this shape "with the reason stated": the self-test's queries are each
atom's own verbatim text (item 1 above), which trivially satisfies BOTH
paths at once — FTS matches on exact token overlap by construction, and
cosine similarity against an identical string is ~1.0 regardless of
embedder, nowhere near `SEMANTIC_SIMILARITY_FLOOR` (0.35). This is an
artifact of the self-test's own query-sampling method (verbatim recent
text), not evidence that the store's retrieval never disagrees across
paths — CP-B's own live verification already demonstrated a genuine
FTS-hit/vector-miss case by deliberately diluting a shared token in
otherwise-unrelated filler text on both sides. Not "fixed" here — change
2c asks for a split and a reason when it degenerates, not a query-sampling
strategy engineered to always disagree.

## 6. `pytest-asyncio`: already installed and configured (change 6)

**Checked, not assumed, before writing anything:** `lyra_ai/pyproject.toml`
already had `pytest-asyncio>=0.23` in `dev` and `asyncio_mode = "auto"` in
`[tool.pytest.ini_options]` — `git blame` dates both to 2026-06-08/09,
months before CP-A. The installed venv already carries `pytest-asyncio`
1.4.0. `tests/test_development.py`'s five `async def test_*` functions
(the only bare `async def test_` functions anywhere under `lyra_ai/tests/`)
were already being collected and already passing before this checkpoint.

**Collected count, before and after (change 6's actual ask):**
- Before this checkpoint's changes: **272** collected, **272** passed.
- After: **294** collected, **294** passed. The entire delta (+22) is
  `tests/test_report.py`, written for this checkpoint's new module (not in
  the FILES set, but the same "cover what you just built" precedent as
  earlier checkpoints' incidental test additions) — not previously-dead
  tests newly surfacing. `lyra_ai/pyproject.toml` and
  `lyra_ai/tests/conftest.py` are both untouched: nothing was required.
- **No async test needed fixing or `xfail`-ing.** The premise that async
  tests exist somewhere in the tree collected-but-broken did not hold for
  `lyra_ai` — checked directly (`pytest --collect-only`, `pytest
  tests/test_development.py -v`) before concluding this, not inferred from
  the pyproject.toml dates alone.

## 7. Eval-set characterization (change 7)

`lyra-memory/eval/retrieval_baseline.json` (not the ad-hoc 5-query set this
session used in CP-B purely to prove `evaluate.py --store` reached a live
store end to end — that one was never committed to the repo and is not
"the" eval set the register's "1.00... NOT trusted" refers to; this one,
already documented in `lyra-memory/eval/BASELINE.md`, is).

Re-ran it live rather than trusting `BASELINE.md`'s numbers unread:

```
LYRA_EMBED_BACKEND=hashed python -m lyra_memory.store.evaluate eval/retrieval_baseline.json
```

- **42-atom corpus, 22 labeled turns.**
- **20 turns expect a hit** (answerable); **2 expect a miss**
  (`expect_miss: true`).
- **coverage 1.00** — every labeled-relevant item surfaced on every
  answerable turn.
- **leak rate 0.00** — no turn injected anything labeled irrelevant.
- **spurious miss rate 0.20** (4 of the 20 answerable turns) — the
  *semantic* path alone reported nothing above the similarity floor on
  those four; BM25 carried the answer instead, so coverage still hit 1.00.
  Expected shape under the hashed offline stand-in, per `BASELINE.md`.
- **spurious injection rate 0.50** (1 of the 2 unanswerable turns) — the
  query "what did we decide about the harm gate in the combat sandbox"
  still returned material: the corpus's "`sandbox_read` is excluded from
  dream input" tokenizes to `sandbox` + `read` under FTS5, a genuine
  lexical hit on `sandbox`. `BASELINE.md` already names this and
  deliberately did not relabel it.
- **Failed by the live store, by the render()'s own marks:** zero turns
  marked `missing` or `leaked`; exactly **one** of 22 (the harm-gate query
  above) marked "injected anyway" (spurious injection). Every other turn
  renders `ok` or `correctly empty`.
- Numbers match `BASELINE.md` exactly — nothing has drifted since it was
  written. The baseline is real, reproducible, and — per the register and
  `BASELINE.md` itself — still measured entirely on the hashed offline
  embedder, so "1.00 unvalidated against real MiniLM" continues to be the
  correct caveat, now with the actual composition behind it on record
  rather than just the headline number.

## Files touched outside the FILES set

- `lyra_ai/tests/test_report.py` — new. Covers `_classify_misses`,
  `format_log_line`/`render`'s field/gap coverage, and
  `collect_measurements`/`collect_from_path` against a real tmp `Store`
  (atom counts, the retrievability split, the retrieval self-test's
  verbatim-match case, outcomes-is-zero, current-affect from a persisted
  fact vs. `unavailable` when none was ever written, per-section failure
  isolation via a monkeypatched section, and the read-only connection
  genuinely rejecting a write). 22 tests, all passing under the
  already-configured `asyncio_mode = "auto"`.

## DONE-WHEN — evidence

All run live against a real `Runtime` (fake LLM backend; everything else —
sqlite, sqlite-vec, aiosqlite, the hashed embedder, the real event loop,
the real default `~/.lyra/` paths so `python -m lyra_core --report` (run as
an actual subprocess) reads the same store the in-process daemon was
serving turns against):

- **`python -m lyra_core --report` against a running, serving daemon:**
  ran as a real subprocess while the daemon was actively up; printed every
  field in change 2. Daemon's log showed no error, exactly one
  `CORE_CONSTRUCTED` (the `--report` process never constructs a core —
  it never imports `Runtime` at all), before and after the subprocess ran.
  Daemon served another turn immediately afterward.
- **Ten turns, atom count for today:** increased by **20**, not 10 — see
  item 4 above for why that's the correct number, not a bug.
- **Retrieval path split, nonzero, total = assemblies:** `11 = 11`
  (`0+0+11+0`), all-both, reason stated (item 5 above).
- **`REPORT_INTERVAL_SECONDS` set low (3s in this run):** daemon log
  showed the startup `SELF_REPORT` line plus at least two more periodic
  ones within 7 seconds, all parseable, all on the exact field names in
  `report.FIELDS`.
- **`store.db` made unreadable mid-run:** `chmod 000` on the live file
  (blocks *new* opens; the daemon's own long-lived write connection, opened
  before the `chmod`, is unaffected by a permission change on an already-open
  fd — this is what makes the two failure domains genuinely separable, see
  item 2's design note in `Runtime._emit_self_report`'s docstring). Next
  periodic tick emitted with every store-derived field `unavailable`; the
  daemon kept serving turns throughout and afterward, confirmed by an
  actual `chat()` call succeeding both during and after the fault.
- **`pytest --collect-only`:** 272 -> 294 (+22, `tests/test_report.py` —
  see item 6).
- **Eval-set characterization recorded as numbers:** see item 7.

## A fix made along the way: `history_db_bytes` was always `unavailable` on the daemon's own line

Found while verifying change 2h live: `Runtime._emit_self_report` was
passing `self._history_path` straight through to `collect_from_path()`,
but that attribute stays `None` whenever the daemon uses
`ConversationMemory`'s own default (`lyra/memory.py`'s `DEFAULT_DB_PATH`)
rather than an explicit override — `report.py`'s `_file_sizes_section`
correctly treats `None` as "nothing to size," so the daemon's own periodic
line reported `history_db_bytes=unavailable` even with a real, sized
`history.db` sitting on disk (the external `--report` CLI didn't have this
problem — `__main__.py`'s `_run_report` always resolves the real default
explicitly). Fixed in `runtime.py`'s `Runtime.start()`: right after
`ConversationMemory(self._history_path)` is constructed, `self._history_path`
is reassigned to `history.db_path` (the object's own resolved path) — a
one-line, in-scope fix squarely within "periodic line emission," not a
change to `ConversationMemory` or the history store itself.

---

# CP-D.0 — the daemon's turn path goes through Store.ingest_turn()

Register: `context_log` was empty because the daemon called `build_context()`
directly and never called `Store.ingest_turn()` (CP-C's finding). This
checkpoint reads `ingest_turn()` end to end before touching anything, wires
the daemon through it, and records every effect the daemon has been
skipping — the deliverable is the list below, not just the wiring.

## Change 1 — `Store.ingest_turn()`'s complete effect list

Read end to end (`lyra-memory/lyra_memory/store/__init__.py`, the entire
329-line file — nothing calls `ingest_turn()` or is called by it outside
that file). Its own module docstring states the boundary directly: "No
enrichment, no dream, no consolidation. Everything structural is derived
later by a cold pass, from atoms that are already permanent." Verified by
reading, not assumed from the docstring alone.

`ingest_turn(user_text, lyra_text, source="cli", environment=None, instance=DEFAULT_INSTANCE, speaker="wilson", injected=None, ts=None, user_vec=None)`:

1. **`append_atom(speaker, source, user_text, ...)`** — INSERT into `atoms`,
   INSERT into `vec_atoms` (embeds `user_text` unless `user_vec` is given),
   one `atoms_fts` row via trigger. **ALREADY DONE** — the daemon already
   wrote this exact atom (CP-B, `_ingest_sensory` -> `append_atom`), but as
   its own separate, un-transacted call, not as part of `ingest_turn()`.
2. **`append_atom(speaker="lyra", source, lyra_text, ...)`** — same three
   writes, embeds `lyra_text` fresh. **ALREADY DONE**, same caveat as #1.
3. **`_log_context(injected or {})`** — INSERT into `context_log` (`ts`,
   `session_id`, `atom_ids`, `fact_ids`, `dream_ids`, `budget_used`,
   `misses`). **SKIPPED** — this is the CP-C finding; fixed by this
   checkpoint (change 2).
4. **Atomicity** — #1-#3 in ONE transaction (`append_atom(..., commit=False)`
   twice, `_log_context(..., commit=False)` once, one `self.db.commit()`).
   **SKIPPED**, and not the same gap as #3: even setting `context_log`
   aside, the daemon's two atom writes were each their own commit. A crash
   between them would have left a real, live exchange half-landed — the
   user's atom on disk, Lyra's never written — which is precisely what
   `ingest_turn()`'s own docstring says the design exists to prevent ("a
   turn that half-lands is worse than a turn that fails, because only the
   second is visible"). CP-C never surfaced this because it only checked
   whether `context_log` had rows; this was found by reading `ingest_turn()`
   itself for this change, not inferred from the register.
5. **`user_vec` reuse** (skip embedding `user_text` twice — once for
   retrieval, once for the atom write). **SKIPPED**, and stays skipped after
   this checkpoint (see change 5 below) — not a choice available within
   scope.
6. **`environment`/`instance` parameters** — defaults (`None` /
   `DEFAULT_INSTANCE`) are exactly what the daemon's own `append_atom` calls
   already used. **ALREADY DONE** — no behavior difference either way.
7. **`run_id`** — not a parameter `ingest_turn()` exposes at all; always
   `NULL` through this call, same as every atom the daemon has ever written.
   Not an effect of `ingest_turn()` to mark done or skipped — noted so it
   reads as checked, not missed.
8. **Segmentation, salience scoring, clustering, entity extraction, fact
   extraction, dream cycles, forgetting, candidate/trait promotion** — NONE
   of these are reachable from `ingest_turn()`, confirmed by the full read
   above, not assumed from the docstring. Not an `ingest_turn()` effect at
   all (cold-pass-only, a completely separate call path in
   `store/passes/`), so there is nothing here to mark ALREADY DONE or
   SKIPPED — recorded so the absence is a checked fact, not an unexamined
   gap.

## Change 2/3 — wiring, and the atom-count regression check

Two new `CognitiveCore` methods (`interface.py`) replace the daemon's direct
`build_context()` call and its two separate `append_atom()` calls:

- **`retrieve_context(query)`** — calls `build_context()` (moved here from
  `runtime.py`; see the grep note below). Read-only, called BEFORE either of
  this exchange's atoms exist, so retrieval never has to see — or exclude —
  its own turn's atoms (a nicety `store/context.py`'s recall dedup already
  handled either way, but this ordering is cleaner and matches how
  `ingest_turn()` is meant to be used: retrieve first, then persist what was
  retrieved for).
- **`ingest_exchange(user_text, lyra_text, context, session_id, source="cli")`**
  — calls `Store.ingest_turn()`, feeding it `context.as_log_row()` plus the
  session id (change 4).

`interface.py`'s `_ingest_sensory` no longer writes an atom for
`obs.source in {"conversation", "lyra"}` — that text is now persisted only
by `ingest_exchange()`. Every other sensory source (vision) is unaffected
and still writes its own atom exactly as before (CP-B). `runtime.py`'s
`TurnHandler.handle()` now: ticks "conversation" (drives/affect only, no
atom) -> `retrieve_context()` once -> composes the prompt from that result
-> runs the vision loop as before -> ticks the final "lyra" text
(drives/affect only) -> `ingest_exchange()` once, pairing the ORIGINAL
message with whatever text it took (a vision-assisted answer or the
fallback) to conclude the exchange.

**Verified live** (ten real exchanges through a real `Runtime`, real
`LyraClient`, real sqlite): `SELECT COUNT(*) FROM atoms` = **20**, not 40 —
the daemon's atom count is unchanged from CP-B/CP-C's own measurement of the
SAME ten-exchange scenario, confirming `ingest_turn()` replaced the two
separate writes rather than adding a third path alongside them (the CP-A
defect change 3 warns against, in a new shape, did not recur).

**A disclosed, deliberate behavior change**, not asked for by name but a
direct consequence of "wire it as built": a turn REJECTED by the backend
(`TurnRejected`) used to still leave the user's message as an orphaned atom
(written before the backend call, with no `lyra` atom ever following it).
Under `ingest_turn()`'s two-texts-required shape, `ingest_exchange()` is
only reachable after a real reply exists, so a rejected turn now writes
**no** atom at all for it — consistent with `ingest_turn()`'s own stated
purpose (no half-landed turns), and arguably the more correct behavior (an
orphaned atom with no reply was already a symptom of the same problem #4
above names), but a real, observable change from before this checkpoint.
`history.db` is unaffected — the user's message is still recorded there
unconditionally, before the backend is ever called; only the `atoms` table
changed. Updated `test_backend_failure_is_rejected_not_fatal` to assert the
new behavior explicitly rather than silently losing coverage of it.

**grep, for the done-when's "no direct build_context() call":**
`grep -n "build_context(" lyra_core/runtime.py lyra_core/interface.py` finds
**zero** matches in `runtime.py` and exactly **one** in `interface.py`
(inside `retrieve_context()`, the one method that owns it). Retrieval still
has to happen somewhere — `ingest_turn()` doesn't retrieve anything, only
writes and logs — so "no direct call" is read as "no longer a bare,
unaccounted-for call sitting in the transport-facing turn handler,
disconnected from what gets persisted," not "retrieval no longer happens
anywhere," which OUT OF SCOPE ("wire the path as built") would make
impossible to achieve regardless.

## Change 4 — the session id `context_log` needed

**Finding:** `context_log.session_id` is declared `INTEGER` in
`store/schema.py` — read as a future cold-pass FK into the `sessions` table
(`sessions.id INTEGER PRIMARY KEY AUTOINCREMENT`, populated by a
segmentation pass that doesn't exist yet; OUT OF SCOPE confirms
"forgetting"/retrieval/etc. stay as built, and no cold pass runs on the hot
path per change 1's own finding #8). The daemon has no such integer at
ingest time — no session row has ever been created for any connection, cold
or otherwise.

**Chosen:** reuse the daemon's own per-connection session string —
`TurnHandler.handle(self, message, session)`'s existing `session` parameter,
already "stable across a connection" (it is the CLI's own session
identifier, already used for `ConversationMemory`) — passed straight into
`injected["session_id"]`. SQLite's column type affinity does not reject a
TEXT value in an INTEGER-affinity column; it stores it as given, with no
error and no change to the column's own declared type — confirmed against
`store/integrity.py`'s `assert_schema()`, which compares declared
`(name, type, notnull, pk)` from `PRAGMA table_info`, not what a column
currently holds, so this does not trip the structural check.

**Not chosen:** minting a new integer (e.g. a per-process connection
counter) purely to satisfy the column's declared type. That would be a
fabricated foreign key pointing at a `sessions` table that has no
corresponding row — worse than an honestly-typed string, which reads
exactly as what it is when someone inspects the column later. Verified live:
ten exchanges over one client connection produced ten `context_log` rows,
every one carrying the same session string the daemon actually used.

## Change 5 — SKIPPED effects, recorded in code, not silently omitted

Both are commented at their exact site, not just here:

- **`user_vec` reuse** (`interface.py`, `ingest_exchange`'s docstring) —
  `build_context()` computes its own query embedding internally and does
  not return it; OUT OF SCOPE forbids changing its signature to expose one.
  `ingest_exchange()` therefore cannot pass `user_vec`, and `user_text` is
  embedded a second time inside `ingest_turn()`'s own `append_atom` call.
  Not a choice — there is no vector available to hand it.
- **Vision's solo atom** (`interface.py`, `_ingest_sensory`) — `ingest_turn()`
  is structurally a pair (exactly one interlocutor atom, one Lyra atom); a
  vision description is a third, unpaired atom with no `lyra_text`
  counterpart at the moment it arrives. Kept on the old `append_atom()`
  path, unchanged from CP-B, because forcing it through `ingest_turn()`
  would mean inventing a fake pairing — not "wiring the path as built."

## Change 6 — `report.py`: two distinct retrieval measurements

`context_log`-derived (`logged_*`, new) and the CP-C live self-test
(`selftest_*`, kept, renamed for the distinction) are both computed and both
labeled in `render()`'s output under one "2c. retrieval" section, in two
clearly headed sub-blocks. `logged_empty_window` counts `context_log` rows
in the window whose `atom_ids` is empty — the done-when's "a retrieval that
returns nothing appears... rather than being absent."

**Verified live** (same ten-exchange run as above): `logged_assemblies_window
= 10`, split `9 both + 0 vector-only + 0 fts-only + 1 neither = 10` (the
`neither`/`empty` one is the very first exchange, retrieved against a store
that had zero atoms yet — nothing to find, correctly recorded as a row, not
skipped). `selftest_assemblies = 10`, split `10 both + 0/0/0` — different
from `logged_*` in both method and result, exactly the distinction change 6
asks for (the self-test's verbatim-text queries against a now-populated
store trivially hit; the `logged_*` numbers reflect what the real,
differently-worded exchange queries actually found at the time, including
the one that legitimately found nothing).

## Change 7 — was `ingest_turn()` usable without an out-of-scope change?

**Yes**, for the daemon's primary (message, reply) pair — the case the
done-when's own test scenario (ten CLI exchanges) exercises. Not blocked;
proceeded per change 2/3. The one place it could NOT represent the daemon's
existing behavior without inventing something is the vision solo atom
(change 5) — handled by leaving that one case outside `ingest_turn()`
explicitly, not by reimplementing part of `ingest_turn()`'s own logic
in the daemon to work around it.

## Files touched outside the FILES set

- `lyra_ai/tests/test_core.py` — `_RecordingMemory` gained `ingest_turn`
  (recording calls) alongside `append_atom`; the two conversation/lyra
  ingest-routing tests were rewritten to assert NO atom is written by
  ticking those sources anymore (renamed to say so); new tests for
  `ingest_exchange` (calls `ingest_turn` with both texts, threads
  `session_id` into `injected`, carries the `ContextResult`'s `as_log_row()`
  through, returns the two atom ids) and `retrieve_context` (calls through
  to `build_context`, via `monkeypatch.setattr` on `interface._build_context`).
- `lyra_ai/tests/test_runtime.py` — `_FakeCore` gained `retrieve_context`
  (returns a fixed `ContextResult`, or raises if configured to, for the
  propagation test) and `ingest_exchange` (records calls). Every
  `_no_context()`/`patch("lyra_core.runtime.build_context", ...)` call site
  removed — there is nothing left to patch in `runtime.py` (the whole
  point of the grep check above) — replaced with `_FakeCore`'s own fixed
  return values. `test_system_prompt_is_layer1_*` renamed and rewritten
  against the new `_compose_system_prompt()` (a pure formatter over an
  already-fetched `ContextResult`, no longer a fetch-and-compose method —
  `system_prompt()` itself was removed as dead code once nothing in
  production called it). `test_backend_failure_is_rejected_not_fatal`
  updated for the disclosed atom-write behavior change above. New tests:
  `test_handle_ingests_the_exchange_once_with_both_texts_and_the_session`
  (the daemon-side half of change 3's atom-count check — exactly one
  `ingest_exchange` call, both texts, right session), and vision-path
  assertions that the ORIGINAL question pairs with the FINAL answer in
  exactly one `ingest_exchange` call regardless of how many vision
  round-trips it took.
- `lyra_ai/tests/test_report.py` — renamed the self-test assertions to the
  `selftest_*` fields; added tests for `logged_*` reading real
  `context_log` rows (zero on a fresh store, a real `ingest_turn()` row
  counted correctly, an empty-`atom_ids` row counted as
  `logged_empty_window` rather than absent).

## DONE-WHEN — evidence

All run live against a real `Runtime` (tmp-path store; fake LLM backend;
everything else real — sqlite, sqlite-vec, aiosqlite, the hashed embedder,
the real event loop, a real `LyraClient` over a real loopback socket):

- **Change-1 effect list, every entry marked:** see "Change 1" above.
- **Ten exchanges; `context_log` rows carry the session id:** 10 rows,
  every one's `session_id` equal to the single session string the client
  used for all ten exchanges.
- **Ten exchanges, twenty atoms, not forty:** `SELECT COUNT(*) FROM atoms`
  = 20.
- **`python -m lyra_core --report` shows the two splits, populated,
  distinct, labeled:** rendered output's "2c. retrieval" section — see
  change 6 above for the actual numbers from this run.
- **An empty retrieval is visible, not absent:** the first exchange's
  `context_log` row has `atom_ids = []` — a real row, counted in
  `logged_empty_window`, not a missing row.
- **grep shows no direct `build_context()` call on the daemon path:** see
  change 2/3 above — zero in `runtime.py`, one in `interface.py`.
- **Both test suites green:** `lyra_ai` 303 passed (was 294 at the end of
  CP-C; +9 net — see "Files touched outside the FILES set" above for what
  changed); `lyra-memory` 283 passed, 4 skipped, unchanged from CP-C (this
  checkpoint touched nothing under `lyra-memory/`).

---

# CP-D — the loop closes (retrieval)

Register: `Store.ingest_turn()` is wired (CP-D.0); `context_log` has real
rows. The fourth and last unmet condition for construct-hood: `tick()`'s
return value has been discarded by every caller since the project began.
This checkpoint makes retrieval the first intent that actually executes.

## Change 1 — what `tick()` returned, before this checkpoint touched anything

Read end to end (`action_selection.py`'s `ActionSelector.select`, the only
producer; `interface.py`'s `CognitiveCore.tick`, the only caller of it) before
adding anything.

**`Intent`'s shape** (`interface.py`, unchanged by this checkpoint):
`kind: IntentKind`, `payload: dict`, `ts: float` (auto-stamped).

**What `ActionSelector.select(drive_pressures, affect, bias=None)` produced,
pre-CP-D** — `frustration = max(0, -affect.valence) * effective_weight`
(`effective_weight = affect_weight`, optionally shifted by `bias.
affect_weight_delta`):
- `drive_pressures["boredom"] > 0` and `> frustration` ->
  `Intent(look, {"reason": "curiosity"})`; additionally, if boredom also
  `>= speak_threshold` (default 1.0) -> `Intent(speak, {"reason": "boredom"})`
  in the same call.
- `drive_pressures["relational"] > 0` and `> frustration` ->
  `Intent(speak, {"reason": "friction"})`.
- Neither wins (both zero, or both flipped by frustration) ->
  `Intent(noop, {})`.
- `IntentKind.research` is declared but has no producer anywhere — reserved,
  never emitted, before or after this checkpoint.

**Consumption, pre-CP-D:** `CognitiveCore.tick()` returned `(intents,
affect_state)`. `TurnHandler.tick()` (`runtime.py`) stored the list on
`self.last_intents` and did nothing else with it — the module's own
docstring said so directly ("tick() still returns intents; they are bound
and left unconsumed, as CP-A requires"). `Harness.run()` (`harness.py`)
records each tick's intents into a `TickRecord` for tests to inspect and
likewise never executes any of them. No path from an `Intent` to an
observable effect existed anywhere in the tree.

## Change 2 — `IntentKind.retrieval`, and where "the core decides"

**Added** `IntentKind.retrieval = "retrieval"` (`interface.py`). Fields:
inherited from `Intent` — no new shape, `payload={"reason": "turn"}`.

**Where the decision lives:** in `ActionSelector.select()`, reusing the
exact frustration-vs-pressure mechanism boredom/relational already use —
not a second way to decide anything. `CognitiveCore.tick()` computes a
third pressure, `drive_pressures["retrieval"]`, set to `_RETRIEVAL_PRESSURE`
(1.0) whenever a `sensory` observation with `source == "conversation"`
arrived this tick (the first tick of an exchange, before `retrieve_context()`
would run), else `0.0`. Deliberately not "any observation this tick" —
the later `"lyra"` tick and any `"vision"` tick within the same exchange
must not each propose a fresh retrieval for it.

**"The core decides," not the daemon:** `TurnHandler.handle()` never
computes whether to retrieve — it reads `self.last_intents` (already
existing state, set by the immediately-preceding `tick("conversation", ...)`
call) for an `IntentKind.retrieval` entry and acts on what it finds.

**Files touched outside the closed FILES set, and why unavoidable:**
- `action_selection.py` — the frustration/pressure comparison lives in
  exactly one place in the codebase; duplicating it in `interface.py`
  to avoid touching this file would mean two formulas that could drift
  out of sync. Added one branch, same shape as the boredom/relational ones
  already there.
- `gate.py` — `HarmGate.check()` is a hard allow-list
  (`ALLOWED_KINDS`); a new `IntentKind` that isn't added there is silently
  dropped by `_gate_intents()`, never executed, regardless of anything
  `ActionSelector` does. There is no way to make change 2/3 work without
  this edit. `gate.py`'s own comment names the exact process this
  checkpoint follows: "Extend this set only after explicit review of the
  new capability" — the review is this checkpoint (retrieval is a read
  against a store the daemon already owns, gated the same way every other
  intent already is; no new external effect). `tests/test_gate.py` has a
  tripwire test asserting `ALLOWED_KINDS`'s exact membership specifically
  to catch an *undeliberate* addition — updated to include `retrieval`,
  by name, as the reviewed fifth kind.

## Change 3 — execution, and a declined turn's visible row

`TurnHandler.handle()` (`runtime.py`): if retrieval was produced, calls
`CognitiveCore.retrieve_context()` (moved here from CP-D.0, unchanged) and
logs `INTENT_EXECUTED`; if not, logs `INTENT_DECLINED` and proceeds with
`context = None`. `_compose_system_prompt()` treats `None` the same as an
empty `ContextResult` — no retrieved text in the prompt, nothing else
different.

**A declined turn's `context_log` row:** `CognitiveCore.ingest_exchange()`
accepts `context=None` and, in that case, builds the `injected` dict itself
— `atom_ids/fact_ids/dream_ids: []`, `budget_used: 0`,
`misses: [interface.DECLINED_MARKER]` (`"retrieval: declined"`) — rather
than skipping the `Store.ingest_turn()` call. The row exists, is queryable,
and is visible in `report.py` as `logged_declined_window`, counted
separately from `logged_vector_only_window`/`fts_only`/`both`/`neither`
(all of which describe retrieval that actually ran) and from
`logged_empty_window` (retrieval ran and found nothing — a different fact
than "retrieval did not run at all").

## Change 4 — the outcome row, packed into columns `Store.record_outcome()` already has

**The weak signal** (change 4's own wording): `atom_count > 0` ->
`valence = 1.0`, else `0.0` — computed once, in
`TurnHandler._retrieval_path()`, shared by the `INTENT_EXECUTED` log line
and the outcome row so they can never disagree.

**No new columns.** `outcomes` has `intent_atom_id, predicted, actual,
valence, ts, environment` — no `atom_count`/`path`/`context_log_id` fields,
and OUT OF SCOPE gives no license to add any (Store's schema is not in this
checkpoint's FILES set either). Packed into what's there, all free-text/
free-float by design:
- `intent_atom_id` = the user's atom id for this exchange (a real FK into
  `atoms`, not a phantom "intent atom" — there is no atom representing an
  intent, so the atom the retrieval was *for* is the closest honest fit).
- `valence` = the weak signal above.
- `actual` = the path label (`"vector"|"fts"|"both"|"neither"`) —
  `Store`'s own free-text convention for an outcome's result, same idea as
  `lyra_core/outcomes.py`'s `ENGAGEMENT`/`SILENCE`.
- `predicted` = the fixed string `"context_available"` — what a retrieval
  intent always wants, mirroring `predicted`/`actual` pairing elsewhere in
  the codebase even though retrieval has no real "prediction" to make.
- `environment` = `"context_log_id={id};atom_count={n}"` — a small,
  parseable string, not a new column. Verified live: recovered by regex
  from a real outcomes row and cross-checked against a real, existing
  `context_log` row (see DONE-WHEN evidence below).

**`context_log_id` doesn't come back from `ingest_turn()`.** OUT OF SCOPE
forbids changing its return signature. `ingest_exchange()` passes an
explicit `ts` into `ingest_turn()` and reads the row back afterward
(`SELECT id FROM context_log WHERE ts = ? ORDER BY id DESC LIMIT 1`) — a
read-after-write against the same connection in the same call, not a
schema or signature change to `Store` itself.

## Change 5 — a minimal, Store-native consolidator; no dedup, no promotion

**Not `development.py`'s `OutcomeConsolidator`/`CandidatePool`.** Those are
built against `MemorySystem`'s old `db.py` schema and semantic-embedding
dedup — neither of which `Store` has, and CP-B already found this whole
path permanently inert against `Store` (`getattr(self._memory,
"candidate_pool", None)` is always `None`). Reusing them would mean giving
`Store` a `candidate_pool` shim just to satisfy an old interface — a bigger,
riskier change than writing five lines of SQL.

**What was built instead:** `CognitiveCore.consolidate_retrieval_outcome
(had_context: bool)` — one `INSERT INTO candidates` per call, `category=
'retrieval'`, `evidence_count=1`, no read-before-write, no merge, no update
to an existing row. `_retrieval_trait_from_outcome()` is the two-branch
closed vocabulary (`"retrieval finds relevant context"` /
`"retrieval finds nothing"`), same shape as `development.py`'s
`trait_name_from_outcome` but not derived from it — that function is keyed
to `predicted == actual` (boredom/relational's "success" concept), which
retrieval has no equivalent of.

**"Must not promote traits":** trivially true by construction — nothing in
this mechanism ever reads or writes `traits` or calls `IdentityEngine`.
Not blocked, not a finding to record under change 5's "if promotion cannot
be separated" clause — there was never a path to separate, because nothing
here reaches promotion in the first place. Verified live: `traits` count
0 before and after twenty exchanges producing twenty candidate rows.

**No dedup is a real, disclosed consequence, not silently absorbed:**
twenty turns produced twenty `candidates` rows (one of two closed values,
repeated), not one row with `evidence_count=20`. OUT OF SCOPE says "trait
dedup" is not this checkpoint's concern — read as covering *this* too, since
the old system's dedup is semantic-embedding-based (the exact thing named
out of scope) and there is no non-embedding dedup already built to fall
back to. A future checkpoint doing dedup or promotion for these candidates
has real data to work from either way.

## Change 6 — six markers, one shared id

`INTENT_PRODUCED`, `INTENT_EXECUTED`, `INTENT_DECLINED`,
`OUTCOME_RECORDED`, `CONSOLIDATOR_FIRED`, `CANDIDATE_CREATED` — all on
logger `lyra_core.runtime`, all carrying `turn=<id>`.

**The shared id is `TurnHandler._turn_seq`**, a plain per-process counter
incremented once at the top of `handle()` — not a database id.
`Store.ingest_turn()` doesn't return a context_log id (change 4), and even
if it did, `INTENT_PRODUCED`/`INTENT_DECLINED` are logged *before*
`ingest_exchange()` ever runs, so no store-derived id could exist yet at
that point regardless. A counter that exists from the first line of
`handle()` is available to every log line the whole turn produces, in
order, with no dependency on how far the turn gets. Safe under concurrent
turns on the same `TurnHandler` (confirmed the daemon serves connections
concurrently — `test_two_clients_at_once_land_in_one_history_in_arrival_
order`): `self._turn_seq += 1` contains no `await`, so no other coroutine
can interleave inside it.

`INTENT_PRODUCED` and `INTENT_EXECUTED` are always emitted together in this
design (nothing currently separates "the core proposed it" from "the
daemon executed it" — there is no failure mode between them yet), so their
window counts are always equal — verified live (20 = 20). Logged as two
markers anyway, per change 6's explicit list, not collapsed into one —
future checkpoints that could introduce a gap between proposing and
executing (a retrieval that's produced but then fails, say) inherit a
distinction that already exists rather than needing to invent one.

## Change 7 — `report.py`: the loop, and outcomes as a real number

New fields (all in `FIELDS`, all in the daemon's periodic line and
`--report` alike): `intents_produced_window`, `intents_executed_window`,
`intents_declined_window`, `consolidator_fired_window`,
`candidates_created_window_log` — all parsed from the log (`_loop_log_
section`, the same technique `_clamp_section`/`_log_affect_ranges` already
use for `DT_CLAMP_ENGAGED`/tick lines — there is no table that records "an
intent was produced," only the log line that says so).
`candidates_created_window` — a second, DB-derived count
(`candidates WHERE last_seen >= window_start`), a cross-check against the
log count rather than a replacement for it (candidates still has no
`created_ts` — CP-C's finding stands — but this checkpoint's own writer
never updates an existing row, so `last_seen` doubles as a creation time
for *these* rows specifically). `logged_declined_window` — read from
`context_log` directly (change 3).

**The "expected: 0" line:** it was never in the hardcoded `KNOWN_GAPS` list
(CP-C put it in `render()`'s section header instead, `"2e. outcomes
(expected: 0 until CP-D)"`) — checked before editing rather than assumed.
Removed; `outcomes_total` now renders under a plain `"2e. outcomes"`
heading, the number speaking for itself. No new `KNOWN_GAPS` line was
needed for anything change 5 blocks, because nothing was blocked (see
change 5 above) — checked, not skipped by omission.

## Change 8 — did the daemon need something `tick()` didn't return?

**No.** Everything `TurnHandler.handle()` needed was already reachable:
whether retrieval was produced, from the existing `self.last_intents`
(unchanged field, unchanged `tick()` signature); the retrieved content,
from the already-existing `retrieve_context()` (CP-D.0); `context_log_id`,
recovered via a read-after-write against `Store` directly (change 4), not
by widening `tick()`'s contract. `tick()`'s signature and return shape —
`(observations, dt) -> (intents, affect)` — are exactly what they were
before this checkpoint.

## `_RETRIEVAL_PRESSURE = 1.0`, and the "no decline in twenty" finding

Chosen as "yes, unless frustration is high enough to say no," in the same
units `ActionSelector` already compares boredom/relational pressure
against — not tuned, not derived, matching the checkpoint's own framing
that whether decline is reachable is part of what this checkpoint measures.

**Measured live, twenty real exchanges through a real `Runtime`** (a fake
backend that always replies, never fails — no `action_outcome`
observations, so `RelationalDrive` stays at zero pressure throughout; only
`BoredomDrive`'s own small negative valence push, bounded to
`_BOREDOM_PRESSURE_CEILING = 0.02`, ever moves valence away from zero):
`intents_produced_window = 20`, `intents_declined_window = 0`. Every turn
retrieved; none declined. This is the sanctioned outcome the done-when
itself names ("If no turn declines in twenty, report the drive conditions
that make declining unreachable and record it as a finding") — recorded
here as that finding, not treated as a failure to fix.

**Decline is reachable, demonstrated separately, not inferred:**
`test_action_selection.py::test_strong_negative_affect_flips_retrieval_to_
declined` drives `ActionSelector.select({"retrieval": 0.3}, valence=-0.8)`
directly and confirms no retrieval intent is proposed — the same mechanism,
exercised at the frustration level a twenty-turn friendly conversation
with this backend never reaches on its own. Nothing here was tuned to make
the live run decline; the live run's honest zero and the unit test's
honest "yes, it can" are two different, both-true facts about the same
mechanism.

## Files touched outside the FILES set

- `lyra_ai/lyra_core/action_selection.py`, `lyra_ai/lyra_core/gate.py` — see
  change 2 above; both necessary, both explicitly reasoned through, neither
  a "wire it as built" violation (nothing about how retrieval, forgetting,
  context assembly, `ingest_turn()`, affect, or drives *work* changed).
- `lyra_ai/tests/test_gate.py` — the `ALLOWED_KINDS` tripwire test updated
  to name `retrieval` as the fifth reviewed kind (renamed
  `test_allow_list_is_exactly_the_four_safe_kinds` ->
  `..._five_reviewed_kinds`).
- `lyra_ai/tests/test_action_selection.py` — new tests for the retrieval
  pressure branch (selects when pressure>0, doesn't when 0, frustration can
  flip it, coexists with boredom in one call, passes the real `HarmGate`);
  `test_extreme_affect_only_produces_allowed_intent_kinds`'s pressure
  scenarios extended to include `{"retrieval": 1.0}`.
- `lyra_ai/tests/test_core.py` — new tests for `tick()` producing/not
  producing the retrieval intent by observation source; new tests for
  `CognitiveCore.record_retrieval_outcome`/`consolidate_retrieval_outcome`
  (both the graceful-absence path via `_RecordingMemory` and real-`Store`
  integration tests: outcome row content, valence by branch, candidate row
  content, zero writes to `traits`); `ingest_exchange`'s return signature
  test updated for the 3-tuple; a new test for the `context=None` (declined)
  path building the right `injected` dict.
- `lyra_ai/tests/test_runtime.py` — `_FakeCore` gained
  `produces_retrieval` (defaults to matching the real selector's behavior
  on a "conversation" tick), `record_retrieval_outcome`, and
  `consolidate_retrieval_outcome`; new tests for the full log sequence and
  shared turn id, the declined path (log lines, the `context=None` exchange,
  no outcome/consolidator calls), the executed path (outcome fields,
  consolidator `had_context` argument by branch), `CANDIDATE_CREATED`
  logging, and two turns getting two different turn ids.
- `lyra_ai/tests/test_report.py` — `path_label` tests; a `logged_declined_
  window` test against a real declined `ingest_turn` row; `_loop_log_
  section`/`collect_from_path` tests parsing a synthetic log for the five
  markers, including the unavailable-without-a-log case; a
  `candidates_created_window` DB cross-check test.

## DONE-WHEN — evidence

All run live against a real `Runtime` (tmp-path store; fake LLM backend;
everything else real — sqlite, sqlite-vec, aiosqlite, the hashed embedder,
the real event loop, a real `LyraClient` over a real loopback socket, real
file-backed logging):

- **One turn; log shows the sequence, shared id:** `INTENT_PRODUCED ->
  INTENT_EXECUTED -> OUTCOME_RECORDED -> CONSOLIDATOR_FIRED`, in order,
  all `turn=1`.
- **`outcomes` row, live:** `(intent_atom_id=1, valence=0.0, actual='neither',
  predicted='context_available', environment='context_log_id=1;
  atom_count=0')` — the first turn, against an empty store, correctly found
  nothing; `context_log_id=1` resolved to a real, existing `context_log`
  row.
- **Twenty exchanges, decline reachability:** `produced=20, executed=20,
  declined=0` — see the finding above.
- **`python -m lyra_core --report`:** `outcomes_total=20`,
  `intents_produced_window=20 == intents_executed_window=20`,
  `+ intents_declined_window=0 == 20`. `outcomes_total ==
  intents_executed_window` (every executed retrieval produced exactly one
  outcome row).
- **Candidates traceable to an outcome:** the first turn's outcome
  (`outcome_id=1`) is immediately followed, same turn, by
  `CONSOLIDATOR_FIRED turn=1 outcome_id=1` and a real `candidates` row
  (`category='retrieval'`); twenty turns produced twenty such rows.
- **No trait promotes:** `traits` count 0 before, 0 after twenty exchanges.
- **grep, no direct `build_context()` in the daemon path:** `grep -n
  "build_context(" lyra_core/runtime.py` — zero matches (re-confirmed after
  every edit in this checkpoint); the one call site remains
  `interface.py`'s `retrieve_context()`.
- **Both test suites green:** `lyra_ai` 339 passed (was 303 at the end of
  CP-D.0); `lyra-memory` 283 passed, 4 skipped, unchanged (this checkpoint
  touched nothing under `lyra-memory/`).
