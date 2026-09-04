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

# CP-E — candidate deduplication

## Change 1 — the present dedup path, read end to end before touching anything

**The module exists and is already wired into the live Store — CP-D's own
docstring on `consolidate_retrieval_outcome()` was wrong about this, not
merely stale.** That docstring said: "This is deliberately NOT
development.py's OutcomeConsolidator/CandidatePool: those are built against
MemorySystem's old db.py schema and semantic-embedding dedup, neither of
which Store has an equivalent of." Verified false by reading
`lyra_memory/candidate_pool.py` and `lyra-memory/lyra_memory/store/passes/
dream.py`: `CandidatePool.__init__` takes a bare `aiosqlite.Connection`, not
a `MemorySystem`; `DreamPass._extract_observations`/`consolidate` already
construct `CandidatePool(self.store.db)` against the live Store connection;
and `store/schema.py`'s `VEC_SQL` defines `vec_candidates` identically to
what `CandidatePool` expects. Store has had an equivalent since CP-B —
CP-D's consolidator just never used it, and its own comment asserted the
opposite of what the code two directories over does. Corrected in
`interface.py`'s new docstring (see change 2 below); the same claim was not
present anywhere else in the FILES set.

**Two dedup modes coexist in `CandidatePool`, by design, not by accident:**

- **Open vocabulary** (`closed_vocabulary=False`, the default) — the
  dream-generated (open, model-invented) vocabulary. `_embedding_text
  (trait_value, evidence)` embeds the **description** (`trait_value`) plus
  **evidence** when supplied (`f"{trait_value}\n{evidence}"`), **never**
  `trait_name` (the label) — the module's own docstring names exactly why:
  "'analytical_orientation' and 'analytical_approach' name two different
  behaviours while their descriptions sit 1.22 apart. Matching on them is
  why 70 candidates produced 60 singletons and one promoted trait." Compared
  by a `vec_candidates` KNN query (`k=1`) against every existing open-
  vocabulary candidate's stored embedding; threshold is `_L2_THRESHOLD =
  sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)`, `CANDIDATE_DEDUP_THRESHOLD = 0.37`
  (cosine, `lyra_memory/config.py`). On a match (`nearest.distance <
  _L2_THRESHOLD`): `evidence_count += 1`, `trait_value`/`last_seen` replaced
  with the new observation's, `evidence_text` — **before this checkpoint** —
  overwritten only if it had been NULL (`COALESCE(?, evidence_text)`, i.e.
  every evidence string after the first was silently discarded); the stored
  vector is replaced with `_merge_centroid` — the running mean of every
  member vector merged so far, renormalized — not the first member's vector,
  so cluster membership does not depend on insertion order. On no match: a
  fresh `candidates` row plus a fresh `vec_candidates` row.
- **Closed vocabulary** (`closed_vocabulary=True`) — used today by
  `development.py`'s `OutcomeConsolidator` for its four frustration-outcome
  trait names (`trait_name_from_outcome`). Bypasses embedding and
  `vec_candidates` entirely; matches by **exact `trait_name` SQL equality**
  (`_add_exact`). The module's own docstring gives the reason, and it is a
  measured one, not a guess: "'persists under frustration' and 'abandons
  under frustration' sit at L2 0.746, inside any threshold loose enough to
  merge genuine duplicates." A fixed, template-generated vocabulary produces
  near-identical surface text for *opposite* outcomes, and no single
  semantic threshold can both merge real duplicates and keep those apart —
  so this vocabulary does not use a threshold at all. Same UPDATE-on-match
  shape as the open path (`evidence_count += 1`, `trait_value`/`last_seen`
  replaced), same pre-existing `evidence_text` loss on every merge past the
  first (now fixed for both paths — see change 3).

**CP-D's `consolidate_retrieval_outcome()`, before this checkpoint, used
neither mode** — a bare `INSERT INTO candidates`, one row per call, no
comparison against anything already in the table. Every one of the twenty
turns in CP-D's own live run wrote a distinct row (register: "Candidate
creation is one row per turn, no dedup. Singletons by construction.");
nothing compared a new observation against what already existed, by label,
by exact name, or by embedding.

## Change 2 — description vs label, and which one retrieval candidates actually use

`_retrieval_trait_from_outcome` (interface.py) is, by the comment already
sitting above it, deliberately shaped like `development.py`'s
`trait_name_from_outcome`: a fixed, two-branch, template-generated
vocabulary ("retrieval finds relevant context" / "retrieval finds
nothing"), not the open, model-invented kind. That is exactly the shape
`CandidatePool`'s `closed_vocabulary=True` path exists for, and exactly the
shape its docstring warns will falsely merge under semantic
description-embedding.

**Measured, not assumed**, before deciding: embedded both retrieval
descriptions under the offline hashed backend (`LYRA_EMBED_BACKEND=hashed`,
what this container actually runs with; see note below on the real model),
and compared to the same `_L2_THRESHOLD` the pool uses:

```
a = "an assembled context contained at least one atom above the retrievability floor"
b = "an assembled context contained no atoms above the retrievability floor"
L2 distance:     0.5240675273905536
L2 threshold:    0.8602325267042626   (sqrt(2 * 0.37))
would merge under description-embedding: True
```

The two retrieval descriptions differ by one clause ("at least one atom" vs
"no atoms") and sit well inside the merge threshold — embedding them would
collapse "finds context" into "finds nothing" (or vice versa), silently
counting a hit as evidence of a miss. This was re-confirmed end to end by
`tools/dedup_probe.py` against the real live store after the twenty-turn
run (change 6 below): description-embedding groups the two real candidate
rows into **1** group; label-embedding (and exact-name) into **2**. Same
finding, from two different measurements.

**Decision: retrieval candidates route through `CandidatePool.add_observation
(..., closed_vocabulary=True)`** — the same mechanism `OutcomeConsolidator`
already uses for its own closed vocabulary, not a new exception invented for
this checkpoint. Consequence, answered directly per change 2's own question:
**label text (`trait_name`) is the only thing that contributes to matching**
for retrieval candidates; `trait_value` (the description) is stored and
displayed but never embedded, never compared. This does not contradict the
open-vocabulary behavior above — it is the same module's existing second
mode, applied to the vocabulary shape it was built for. See interface.py's
`consolidate_retrieval_outcome()` docstring for the same reasoning inline
with the code.

*(Caveat on the measurement above: this container has no network access to
fetch `all-MiniLM-L6-v2` on first use, so every number in this checkpoint —
here and in change 6 — was measured under `LYRA_EMBED_BACKEND=hashed`, the
deterministic offline stand-in, not the real model. The stand-in is
"surface-level only" by its own docstring; the qualitative finding — two
sentences differing by one clause sit far inside a threshold tuned to catch
paraphrase-level duplicates — is exactly the surface-level effect the
stand-in is suited to measuring, and it independently agrees with
`CandidatePool`'s own already-committed docstring finding for the
structurally identical frustration vocabulary ("persists"/"abandons" at L2
0.746). Re-run `tools/dedup_probe.py` on a machine with the real model
cached to confirm against MiniLM specifically; not done here — no such
machine was available.)*

## Change 3 — what "strengthen" means in the schema as built

No column added, per the checkpoint's own instruction. "Strengthen" is
exactly what `CandidatePool._add_exact` (closed vocabulary) already does on
a `trait_name` match, unchanged by this checkpoint except for evidence
handling (below):

- `evidence_count = evidence_count + 1` (read, incremented, written back;
  not a SQL `+1` update because the read is needed to compute
  `evidence_text` too).
- `last_seen = now` — moves on every strengthen, not only on creation.
- `trait_value` replaced with the incoming observation's `trait_value` —
  the same string every call for a given branch (`_retrieval_trait_from_
  outcome` is deterministic), so this is a no-op in practice for retrieval
  candidates specifically, but it is the pool's existing, shared behavior
  and not something this checkpoint changed.
- `evidence_text` — **changed by this checkpoint**, see change 4.

No new row. `consolidate_retrieval_outcome()` returns `(trait_name,
trait_value)` on every call now, same as before — the caller (runtime.py's
`CANDIDATE_CREATED` log line) cannot tell strengthen from creation from that
return value alone; left alone rather than touched, see "Files touched
outside the FILES set" below for why.

## Change 4 — provenance: evidence_text now accumulates instead of discarding

**Found, before fixing it:** `_add_exact` and the open-vocabulary merge
branch both wrote `evidence_text = COALESCE(?, evidence_text)` — the
FIRST evidence string a candidate ever received, forever, with every
later merge's evidence silently dropped. `test_closed_vocabulary_ignores_
evidence_for_matching` (already in `test_candidate_dedup.py`) drives exactly
this case — two calls, two evidence strings — and only ever asserted
`evidence_count == 2`, never checked what happened to the text, so this had
no test coverage either way. A candidate strengthened five times before
this checkpoint had one contributing observation recoverable and four
gone.

**Fixed in `candidate_pool.py`** (in the FILES set): a new `_append_evidence
(existing, new)` helper — `None` if both empty, the single non-empty side if
only one is, else `f"{existing}\n{new}"` — replaces `COALESCE(?,
evidence_text)` in both merge branches (`_add_exact` and the open-vocabulary
KNN-match branch; both had the identical bug, both are the same shared
module, fixing one and not the other would leave the module internally
inconsistent for no reason). `evidence_text` is the existing column, not a
new one — a newline-joined list of everything that has ever contributed,
oldest first.

**What `consolidate_retrieval_outcome()` now passes as evidence:**
`f"outcome_id={outcome_id}"` when the caller has one (threaded through from
`record_retrieval_outcome()`'s return — see "Files touched outside the
FILES set" for the one-line companion change this required in runtime.py),
else `None`. So a strengthened retrieval candidate's `evidence_text` is
literally a newline-separated list of the `outcomes.id` values that
contributed to it — provenance is a `str.splitlines()` plus a `WHERE id IN
(...)` away, no new column, no schema change:

```sql
-- given a candidates.evidence_text like "outcome_id=2\noutcome_id=3\n...":
SELECT * FROM outcomes WHERE id IN (2, 3, ...)
```

**Measured live** (change 6/DONE-WHEN below): after twenty turns, candidate
id=2 (`"retrieval finds relevant context"`) has `evidence_count=19` and
`evidence_text` containing exactly 19 `outcome_id=N` lines; every one of
those 19 ids resolved to a real, distinct row in `outcomes` with
`intent_atom_id`/`valence`/`actual` intact. The schema as built expresses
change 4's requirement; nothing blocks it, so there is nothing to stop and
record a blocker for.

## Change 5 — `tools/dedup_probe.py`

New file, in the shape of `tools/affect_probe.py`: offline, no daemon, no
Store, no CognitiveCore, stdlib plus `lyra_memory.config`/`embeddings` only
(does not import `candidate_pool.py` — it re-derives grouping from
first principles rather than reusing the pool's own online, order-dependent
merge logic, see the module docstring for why that is a deliberate,
different question). Takes a path to any sqlite file with a `candidates`
table (works unmodified against both the old db.py-shaped `memory.db` and
the new store/schema.py-shaped `store.db` — identical columns), embeds every
row's `trait_name` and `trait_value` separately, and reports for each: the
pairwise L2 distance distribution, the number of distinct groups at
`CANDIDATE_DEDUP_THRESHOLD` (connected components of the
pairwise-below-threshold graph — an order-independent notion of "how many
groups does this data support," not a replay of the pool's own
insertion-order-sensitive centroid merging), and the largest group size.
Also reports one bonus measurement past the two required by change 5: exact
`trait_name` groups — what the live closed vocabulary actually uses — so a
reader sees, side by side, why retrieval candidates use neither embedding
method.

Requires a venv with `lyra_memory` installed (`lyra_ai/venv` or
`lyra-memory/venv`) — `lyra_memory/__init__.py` imports `aiosqlite`
unconditionally even though this script's own imports do not need it; not
worked around, that import shape is outside this checkpoint's FILES set.

## Change 6 — the probe run against both required inputs

**Input 1: the archived `memory.db`, opened read-only.** The register
claimed "~70 historical singleton candidates live in the archived
memory.db." **Measured, and false for the file actually present in this
container:**

```
$ lyra_ai/venv/bin/python3 tools/dedup_probe.py /root/.lyra/memory.db.2026-09-02.archive
=== dedup_probe: /root/.lyra/memory.db.2026-09-02.archive ===
candidates: 0
(no candidates in this store — nothing to group)
```

The archive's actual contents: 10 rows in `atoms`, 3 in `facts`, 0 in
`candidates`/`traits`/`trait_history`/`episodes`. CP-B's own DECISIONS.md
entry (see "Files touched outside the FILES set" there) already recorded
this file as "left over from an earlier bootstrap/test run," not three
months of production use — it never claimed 70 candidates itself. The
"~70 historical candidates, self_reflection/self_awareness at L2 0.858"
figures live in `lyra_memory/config.py`'s `CANDIDATE_DEDUP_THRESHOLD`
docstring and in `docs/PICKUP.md`, both apparently describing a different
prior environment/session's real accumulated pool, not a file present on
this container's disk — no second archive file exists anywhere on this
filesystem (`find / -iname "*memory.db*"`/`*.archive*"` outside `/tmp`
pytest artifacts turned up exactly the one file above). The probe was run
exactly as instructed, against the real file that exists; the result is
zero candidates, honestly reported rather than substituted with the
register's number or with synthetic data dressed up as "the archive."

**Input 2: the live store's CP-E candidates**, after the twenty-turn run in
DONE-WHEN below:

```
$ lyra_ai/venv/bin/python3 tools/dedup_probe.py <tmp-store>/store.db
candidates: 2
CANDIDATE_DEDUP_THRESHOLD (cosine) = 0.37  ->  L2 threshold = 0.860233

-- embedding: label (trait_name) --
  pairwise L2 distance: n=1 min=0.9975 max=0.9975 mean=0.9975 median=0.9975
  distinct groups at threshold: 2
  largest group size: 1

-- embedding: description (trait_value) --
  pairwise L2 distance: n=1 min=0.5241 max=0.5241 mean=0.5241 median=0.5241
  distinct groups at threshold: 1
  largest group size: 2

-- bonus: exact trait_name match (what the live closed vocabulary actually uses) --
  distinct groups: 2
  largest group size: 1
```

**The two numbers differ** (2 groups by label, 1 by description) — DONE-
WHEN's explicit allowance ("The two numbers differ, or they do not and the
reason is stated") is satisfied by the first branch, and the reason is
change 2's finding: the live set is exactly the two-branch closed
vocabulary CP-E targeted, so label/exact-name matching (2 groups — the
correct answer, matching what the live pool actually produced) and
description-embedding (1 group — the wrong answer, the two branches would
wrongly merge) diverge exactly as predicted. This is also the legitimate
one-group-is-fine case the checkpoint itself names ("the live set is one
intent kind and may legitimately collapse to one group") — legitimate for
label/exact-name matching if there had been only one branch exercised; here
both branches were exercised (twenty turns produced both "finds nothing"
once and "finds relevant context" nineteen times — see DONE-WHEN), so 2 is
the right group count for label/exact matching, and 1 is specifically
description-embedding's failure mode, not an artifact of a narrow sample.

## Change 7 — `report.py`: candidates by group

`_candidates_traits_section` (report.py) now selects `COUNT(*),
SUM(evidence_count), MAX(evidence_count), SUM(evidence_count = 1)` from
`candidates` in one query and returns four fields instead of one bare
total: `candidates_total` (distinct, post-dedup groups — this field's
*meaning* changed even though its name didn't: dedup now happens at write
time, so a row already is a group, not a raw per-turn count),
`candidates_total_evidence`, `candidates_largest_evidence`,
`candidates_singleton_count`. All four added to `FIELDS` (shared by
`format_log_line` and the daemon's periodic `SELF_REPORT` line) and to the
`candidates_traits` section's field list and `render()`'s "2d. candidates /
traits" block. `candidates_created_window`'s existing comment claimed
`last_seen` "doubles as a creation time" because nothing ever updated an
existing row — false as of this checkpoint (`last_seen` now also moves on
strengthen) — corrected in place to "created or strengthened in window,"
cross-referenced against the log-derived `candidates_created_window_log`
count, which is imprecise in the same new way (see "Files touched outside
the FILES set").

## Files touched outside the FILES set

- `lyra_ai/lyra_core/runtime.py` — one line, structurally unavoidable:
  `consolidate_retrieval_outcome()` gained a second parameter (`outcome_id`,
  default `None`) so it can pass evidence provenance through to
  `CandidatePool` (change 4); its one call site
  (`TurnHandler._finish_exchange`) already has `outcome_id` in scope from
  the preceding `record_retrieval_outcome()` call and now passes it
  positionally. No other line in this file changed — nothing about how
  retrieval, forgetting, context assembly, affect, or drives *works*
  changed; `CANDIDATE_CREATED`'s log line and its name were deliberately
  left alone even though "created" is now imprecise (it fires on strengthen
  too, same as `consolidate_retrieval_outcome()` returning non-`None` on
  every call, unchanged from CP-D) — renaming it would mean touching more
  of runtime.py than one call site requires, for a cosmetic fix DONE-WHEN
  does not ask for; the imprecision is recorded here and in report.py's
  comment instead of silently accepted.
- `lyra_ai/tests/test_runtime.py` — `_FakeCore.consolidate_retrieval_outcome`
  gained the `outcome_id` parameter (recorded as `(had_context, outcome_id)`
  tuples instead of bare bools) to match the real signature; the two
  assertions that inspected `core.consolidations` updated to the new tuple
  shape (`test_handle_executed_retrieval_fires_the_consolidator_with_
  had_context`, `test_handle_executed_retrieval_with_no_atoms_fires_
  consolidator_with_false`).

Both are one-parameter-addition companion edits forced by change 4's
provenance requirement, not scope creep — no behavior in either file's
domain (transport, logging shape, turn sequencing) changed.

## DONE-WHEN — evidence

All measured live: a real `Runtime` (tmp-path store, `init_store=True`),
`LYRA_EMBED_BACKEND=hashed`, a fake backend that always replies (no
`action_outcome` observations, matching CP-D's own setup), twenty turns
driven through `TurnHandler.handle()` directly (the same code path a real
socket client reaches — this checkpoint's scope is `CandidatePool`/
`consolidate_retrieval_outcome`, not the transport layer, so a real
`TurnServer`/socket round-trip was not additionally exercised).

- **`DECISIONS.md` contains the change-1 present-behavior record and both
  change-6 probe tables as numbers:** above.
- **`python tools/dedup_probe.py` runs standalone against the archived
  store:** yes — `candidates: 0`, honestly reported (see change 6 for why
  the register's "~70" claim did not hold for the file actually present).
- **Twenty new turns; candidate table growth:** grew by **2** rows, not
  twenty. `outcomes_total = 20` (one outcome row per executed retrieval,
  unchanged from CP-D); `candidates` table has exactly 2 rows afterward —
  id=1 `"retrieval finds nothing"` (evidence_count=1, the first turn,
  against an empty store), id=2 `"retrieval finds relevant context"`
  (evidence_count=19, every turn from the second turn on). Both branches
  were genuinely exercised, not just one.
- **A candidate with evidence_count > 1, provenance identifiable:**
  candidate id=2, `evidence_count=19`. `evidence_text` is 19 newline-joined
  `outcome_id=N` lines; every one resolved via `SELECT * FROM outcomes
  WHERE id IN (...)` to a real, distinct `outcomes` row with intact
  `intent_atom_id`/`valence`/`actual` — e.g. outcome_id=2 -> `(id=2,
  intent_atom_id=3, valence=1.0, actual='both')`; all 19 resolved, none
  missing.
- **`python -m lyra_core --report` shows the grouped fields, singleton
  count visible:** measured via `collect_from_path()` (the same function
  `--report` calls) against the tmp store above — `candidates_total=2`,
  `candidates_total_evidence=20`, `candidates_largest_evidence=19`,
  `candidates_singleton_count=1`. Rendered section:
  ```
  2d. candidates / traits (grouped — a row is a distinct, post-dedup candidate)
    distinct candidates 2
    total evidence      20
    largest evidence    19
    singletons          1
    created/strengthened (window)  2
    promoted (window)   0
    trait count         0
  ```
- **Trait count before/after; promotion or the gap to it:** 0 before, 0
  after. No trait promoted. Highest evidence count reached: 19 (candidate
  id=2). `TRAIT_THRESHOLDS` (`lyra_memory/config.py`) are `{surface: 5,
  character: 15, core: 50}` — 19 is past the surface and character raw
  numbers and 31 short of core — but comparing evidence_count to those
  numbers directly is not why nothing promoted: promotion is
  `IdentityEngine.consolidate()`, called only from `DreamPass.consolidate()`
  (`store/passes/dream.py`), and this checkpoint's `consolidate_retrieval_
  outcome()` never calls it, unchanged from CP-D's own stated design
  ("nothing here ever touches `traits`"). SCOPE is candidate dedup, not
  wiring the retrieval vocabulary into promotion, and OUT OF SCOPE forbids
  tuning thresholds either way — so this is recorded as a structural gap
  (no path from these candidates to `traits` exists yet at all, not "close
  but under the threshold") rather than acted on.
- **Both test suites green:** `lyra_ai` 339 passed (unchanged count — no
  tests added or removed, three updated in place: `_FakeCore`'s signature
  and the two assertions that depended on it); `lyra-memory` 283 passed, 4
  skipped (unchanged count — `candidate_pool.py`'s existing evidence-text
  tests did not assert on the text this checkpoint changed the handling of,
  so all pass unmodified against the new append behavior).

# CP-F — promotion is wired

Register corrections carried in verbatim by CP-F: the "~70 historical
candidates" figure is struck (unverified — CP-E measured 0 in the actual
archive file in this container); dedup for closed vocabularies uses exact
label match, not description embedding (CP-E, measured L2 0.524 vs
threshold 0.860); `evidence_text` accumulates instead of discarding after
the first merge (CP-E fix, third silent-discard path found); the live
store's 20-turn run produced 2 candidates, max evidence_count 19, fully
traceable; promotion was an unwired mechanism, not an unreached threshold —
trait count has always been 0.

## Change 1 — the promotion path, read end to end, before touching anything

**`IdentityEngine`** (`lyra-memory/lyra_memory/identity_engine.py`) is the
only code in this repository that ever writes to `traits` or
`trait_history`. `consolidate(dream_id=None)`:

1. Reads every row from `candidates` with `evidence_count >= 1`
   (`CandidatePool.get_candidates`).
2. For each, `_stability_for(evidence_count)` maps it against
   `TRAIT_THRESHOLDS = {"surface": 5, "character": 15, "core": 50}`
   (`lyra_memory/config.py`) — `None` (skip) below 5, else the highest tier
   crossed.
3. `confidence = min(evidence_count / TRAIT_THRESHOLDS["core"], 1.0)` —
   i.e. evidence measured as a fraction of the CORE threshold specifically,
   not the tier just reached. A brand-new surface trait (evidence_count=5)
   therefore starts at confidence 0.10, not some tier-relative 1.0.
4. `_upsert_trait` writes or updates the `traits` row and, in the same
   aiosqlite transaction (asserted by `assert_trait_history_integrity`),
   an accompanying `trait_history` row — INSERT+INSERT on first sight of a
   name (`event="promoted"`), UPDATE+INSERT on a later call whose
   evidence_count/confidence/stability changed (`event="confidence_change"`
   or `"tier_change"`), or nothing at all when the trait is core-tier and
   `confidence >= CORE_CONFIDENCE_LOCK` (0.8, write-protected) or the
   computed state is identical to what is already stored (a true no-op).

This is fully correct and was already covered by
`lyra-memory/tests/test_trait_history.py` before this checkpoint touched
anything — the promotion arithmetic was never the problem.

**What caller was supposed to invoke it, and why it doesn't, for the live
daemon:** `IdentityEngine.consolidate()` has exactly two production
callers in the tree, and neither one is reachable from the process that
actually runs `python -m lyra_core`:

- `store/passes/dream.py`'s `DreamPass.consolidate()` — itself only called
  from `DreamPass.execute()`, a `ColdPass`. `grep -rn "DreamPass(" --
  include="*.py"` across the whole repository finds exactly two
  constructors: `DreamPass`'s own module and
  `lyra-memory/tests/test_dream.py`. There is no scheduler, cron, or
  daemon-side call that ever constructs a `DreamPass` (or any `ColdPass`
  subclass) against a live store — cold passes in this build are library
  code, exercised only by their own tests. (`review_facts.py` runs
  `FactPass`, a different cold pass, as a manual review tool — not evidence
  of a general scheduler; there is no analogous tool for `DreamPass`.)
- `lyra_memory/__init__.py`'s legacy `MemorySystem.start()` constructs a
  real `IdentityEngine` and a `DreamingLoop` that DOES poll itself on a
  timer — but `MemorySystem` opens `DB_PATH` (`~/.lyra/memory.db`), and the
  live daemon's `CognitiveCore` is always constructed with a
  `lyra_memory.store.Store` opened against `STORE_PATH`
  (`~/.lyra/store.db`) instead (CP-B). `CognitiveCore._get_promoted_traits`
  already does `getattr(self._memory, "identity_engine", None)` — `Store`
  has no such attribute, so this has always returned `None` and therefore
  `[]` for the live daemon, exactly as `getattr(self._memory,
  "candidate_pool", None)` did before CP-E fixed the analogous gap for
  candidates.

So "unwired" means precisely this: the live daemon's Store connection and
`IdentityEngine` had never been introduced to each other by any code path,
in either direction. Not a threshold question, not a bug in the promotion
arithmetic — a missing wire, exactly as the register states.

**A second, blocking finding, only surfaced by actually wiring it (not
visible from reading the code alone):** `CandidatePool.get_candidates()`
validates every `candidates` row into a `lyra_memory.models.Candidate`
pydantic model, whose `category` field was `Literal["behavioral",
"emotional", "relational", "cognitive"]`. CP-D/CP-E's
`consolidate_retrieval_outcome()` (`interface.py`) has written
`category="retrieval"` into `candidates` since CP-D. Nothing had ever
called `get_candidates()` against a store containing a `category=
"retrieval"` row before this checkpoint's live verification, so the
mismatch was invisible — the first attempt raised
`pydantic_core.ValidationError` inside `IdentityEngine.consolidate()`,
which would have made ALL candidates unreadable (dream-derived ones
included, not just retrieval's), not merely the retrieval branch. Fixed in
`lyra_memory/models.py` by adding `"retrieval"` to the `Literal` — one
value, no new abstraction. `models.py` is not literally named in CP-F's
FILES, but the `Candidate.category` vocabulary is the candidate pool
module's own data contract, and promotion cannot be wired at all without
this fix (every `IdentityEngine.consolidate()` call against a store
containing a retrieval candidate would raise) — a structurally-unavoidable
companion touch under the same standard CP-E used for `runtime.py`.

## Change 2 — wiring, and why exposure doesn't go through a new introspect() method

`CognitiveCore.promote_traits()` (`interface.py`) is the new method:
constructs `CandidatePool(db)` / `IdentityEngine(db, pool)` fresh against
`self._memory.db` — the same Store connection `consolidate_retrieval_
outcome()` already uses, not `self._memory.identity_engine` (which stays
`None` for a Store, per Change 1) — and returns `IdentityEngine.
consolidate()`'s own result: a list of dicts, one per candidate that
crossed into `traits` for the first time this call.

Called from `runtime.py`'s `_finish_exchange`, once per turn, right after
`consolidate_retrieval_outcome` — "after consolidation" per CHANGES item 2
— and only on turns where a retrieval intent actually executed (the same
guard `consolidate_retrieval_outcome` is already behind; a declined turn
produces no new evidence, so there is nothing new to promote, though
`promote_traits` would also be a no less correct no-op if called there —
not changed, to keep this turn identical in shape to CP-D/E's).

**Read-only exposure to Lyra deliberately does NOT touch `introspect()` or
add a new method for it.** OUT OF SCOPE forbids "changing retrieval," and
`lyra_memory/store/context.py` already has a `traits` context block
(`_traits_block`, budget `CONTEXT_BUDGETS["traits"] = 100`) that SELECTs
`traits WHERE confidence >= TRAIT_CONFIDENCE_FLOOR ORDER BY confidence
DESC` and folds the result into `context.text` under a `## Traits` heading
— alongside facts/commitments/recall, in the same pinned block order the
module's own docstring has documented since before this checkpoint. This
is already wired into the live daemon path, unmodified, via
`CognitiveCore.retrieve_context()` -> `TurnHandler._compose_system_prompt`
(`context.text` is appended to the system prompt whenever retrieval
executed). A promoted trait therefore becomes visible to her on the next
turn that retrieves — through the mechanism this codebase already built
for exactly this purpose — without CP-F touching retrieval at all. This
was verified live, not assumed (see DONE-WHEN evidence below): after 20
turns, `CognitiveCore.retrieve_context()` returned a `context.text`
containing:
```
## Traits
- retrieval finds relevant context: an assembled context contained at least one atom above the retrievability floor
```
`introspect()` itself is untouched — still `AffectState` only, still sync,
still non-mutating. Overloading its signature to also return traits would
mean either bolting non-affect data onto the Phase-0-frozen `AffectState`
dataclass (interface.py's own docstring warns this "requires touching
every peripheral") or making `introspect()` async (a second real signature
change, disruptive to `_compose_system_prompt`/`_emit_self_report`,
neither of which needed touching for this checkpoint). Both are larger,
riskier changes than CP-F's SCOPE calls for, and neither is necessary: the
`_traits_block` path already satisfies "her access stays read-only" (it is
a `SELECT`, reachable only from her own context assembly, and writes
nothing) and already satisfies "expose any promoted trait" (verified
above) without them.

**A measured consequence of the confidence formula, worth recording
because it changes what "wired" means in practice:** `confidence =
evidence_count / 50` (Change 1, point 3) means a surface-tier trait
(evidence_count 5-14) has confidence 0.10-0.28 — always below
`TRAIT_CONFIDENCE_FLOOR` (0.3) — so it exists in `traits` (a real
promotion, `trait_history` says so) without yet being visible to her via
`_traits_block`. Visibility starts at evidence_count=15 (character tier,
confidence exactly 0.30). Live-verified: the run below promoted at
evidence_count=5 (turn 6) but the trait did not appear in `context.text`
until evidence_count reached 15-19 (character tier, confidence 0.30-0.38)
by turn 20. This is not a bug CP-F introduces or a threshold CP-F is
tuning (OUT OF SCOPE forbids that either way) — it is an existing
interaction between two independently-designed, pre-existing constants
(`TRAIT_THRESHOLDS["core"]` in the confidence denominator, and
`TRAIT_CONFIDENCE_FLOOR` in the visibility gate) that nothing had ever
observed together before, because nothing had ever promoted a trait
against the live Store before this checkpoint.

## Change 3 — trait_history explicability, without a schema change

`lyra_memory/store/schema.py` states its own constraint plainly: **"There
is no migration path and there will not be one: the version below is
asserted at boot... a mismatch crashes on start."** `SCHEMA_VERSION` is
compared exactly (`store/integrity.py`'s `assert_schema`, structural diff
included) against every store this code opens — a live column addition
would crash every existing `store.db` in the field on next boot, with no
repair path by design. `schema.py` is also not in CP-F's FILES. Both
things point the same way: do not touch it unless truly unavoidable, and
it is not.

`trait_history`'s existing columns already carry what change 3 asks for,
read together with `candidates`:

- **"the candidate"**: `trait_history.trait_label` is the trait's `name` —
  the same string as the promoted candidate's `candidates.trait_name`.
  `candidates.trait_name` has no UNIQUE constraint in the schema (unlike
  `traits.name`, which does), so this join is not schema-enforced — but
  `IdentityEngine._upsert_trait` itself already treats `traits.name` as
  the sole identity key for a trait (`SELECT ... WHERE name = ?`), and for
  the closed vocabularies actually promoted so far (frustration's four
  names, retrieval's two), `_add_exact` keeps to exactly one row per exact
  name by construction. An open-vocabulary (dream-generated) name is
  free text and could in principle collide with another open-vocabulary
  candidate's name without colliding semantically — a preexisting
  ambiguity in this schema's design (the same one CP-E's `dedup_probe.py`
  measured for description-vs-label grouping), not one CP-F introduces or
  can resolve within a no-migration constraint.
- **"its evidence count at promotion"**: `trait_history.evidence_count`,
  already written on every mutation including the promoting one.
- **"the threshold in force"**: not a stored column, but reconstructible
  from `trait_history.tier_after` (already written) plus
  `TRAIT_THRESHOLDS[tier_after]` — the fixed dict CP-F's own change 5
  forbids tuning this checkpoint, and confirmed unchanged below. A future
  checkpoint that DOES tune `TRAIT_THRESHOLDS` would make this
  reconstruction wrong for historical rows promoted under the old values —
  a real, known limitation, explicitly punted by DEFERRED ("Promotion
  threshold tuning") and OUT OF SCOPE ("do not tune them... this
  checkpoint") to whichever future checkpoint actually changes the
  constant, not solved here.
- **"the outcome rows that constituted the evidence"**: `trait_label` ->
  `candidates.trait_name` -> `candidates.evidence_text` (CP-E's
  newline-joined `outcome_id=N` list, already built for exactly this) ->
  `outcomes` rows by id. No new column — this reuses CP-E's provenance
  mechanism as-is; CP-F does not modify `candidate_pool.py`'s evidence
  handling at all.

No schema change was made anywhere in this checkpoint's diff (verified:
`git diff` over `lyra-memory/lyra_memory/store/schema.py` for this
checkpoint is empty).

## Change 4 — TRAIT_PROMOTED, never silent

Two log lines, one per layer, both live-verified below:

- `identity_engine.py`'s `_upsert_trait`, on the INSERT (first-crossing)
  branch only — NOT on `confidence_change`/`tier_change` for an
  already-promoted trait, which is a real mutation but not "a candidate...
  becomes a trait" (SCOPE's own wording): `print(f"...[IdentityEngine]
  TRAIT_PROMOTED name={name!r} evidence_count={evidence_count}
  threshold={threshold} stability={stability!r}")`, where `threshold =
  TRAIT_THRESHOLDS[stability]`. This fires regardless of caller (DreamPass,
  a test, or the live daemon), matching lyra_memory's existing print-based
  convention (`CandidatePool`, `DreamPass`, `DreamingLoop` all log this
  way, not via `logging`).
- `runtime.py`, matching its own established convention (`CORE_CONSTRUCTED`
  ... `CANDIDATE_CREATED`, a module-level tag + `log.info("%s ...", TAG,
  ...)`): a new `TRAIT_PROMOTED = "TRAIT_PROMOTED"` constant, logged once
  per promotion dict `promote_traits()` returns, in `_finish_exchange`,
  with `turn=`, `trait_name=`, `evidence_count=`, `threshold=`.

`_upsert_trait`'s return value changed from `None` to `bool` (True only on
the first-crossing branch) and `IdentityEngine.consolidate()`'s from `None`
to `list[dict]` (one entry per candidate that crossed) so `promote_traits`
— and therefore `runtime.py` — can know what happened without re-querying
`traits` before and after. No existing caller (`DreamPass.consolidate`,
every `test_trait_history.py`/`test_memory.py` call site) inspects the
return value, so this is additive, not breaking; confirmed by both test
suites passing unmodified at those call sites.

## Change 5 — the threshold constant

`TRAIT_THRESHOLDS: dict[str, int] = {"surface": 5, "character": 15,
"core": 50}` and `CORE_CONFIDENCE_LOCK = 0.8` (`lyra_memory/config.py`)
were already named constants before this checkpoint — nothing inlined
needed naming. Recorded here, unchanged: `git diff` over `config.py` for
this checkpoint is empty. Every threshold and confidence value referenced
anywhere in this section (5 / 15 / 50 / 0.8 / 0.3-for-`TRAIT_CONFIDENCE_
FLOOR`) is the value already in the tree before CP-F, read, not edited.

## Change 6 — reversible by hand

Two variants, both executed against a scratch copy of the live
verification store (`cpf_verify/store.db`, never the original) via a plain
`sqlite3` connection — SQLite's per-connection default is
`PRAGMA foreign_keys=OFF` (confirmed: `PRAGMA foreign_keys` on a bare
`sqlite3.connect()` reports `0`), unlike `Store.open()`, which explicitly
turns it `ON` for its own aiosqlite connection. `trait_history.trait_id
REFERENCES traits(id)` with no `ON DELETE` clause would raise under FK
enforcement; run by hand via plain `sqlite3` (as change 6 specifies — "by
hand", not through `Store`), it does not.

**Minimal — removes the trait, trait_history stays exactly as it was:**
```sql
DELETE FROM traits WHERE name = 'retrieval finds relevant context';
```
Executed and verified: `traits` went from 1 row to 0; `trait_history`'s 15
rows were byte-for-byte unchanged (same count, same `trait_id=1` on every
row, now orphaned rather than deleted) — the trajectory the schema.py
comment calls "the primary artifact" survives exactly as it was, including
the record of the promotion that is being undone.

**Caveat, found by testing rather than assumed: this alone does not
stick.** The candidate's `evidence_count` is untouched by the DELETE, so
the very next `IdentityEngine.consolidate()` call (which runs every turn
via `promote_traits()`) re-promotes it immediately — verified: calling
`engine.consolidate()` against the rolled-back scratch copy re-inserted
the trait at `evidence_count=19, stability='character'` (its current,
unrolled-back evidence), logging a second `TRAIT_PROMOTED` line. A
first-promotion rollback that must survive the next turn needs a second
statement:
```sql
DELETE FROM traits WHERE name = 'retrieval finds relevant context';
UPDATE candidates SET evidence_count = 4
  WHERE trait_name = 'retrieval finds relevant context';  -- below TRAIT_THRESHOLDS['surface']
```
Executed and verified on a second scratch copy: `traits` stayed empty
across a subsequent `consolidate()` call (`promotions == []`),
`trait_history`'s 15 rows again untouched. This second statement is
presented as the operator's explicit choice, not a system feature — DEFERRED
excludes "demotion, decay of traits, or trait revision" as a built-in
mechanism; lowering a candidate's evidence_count by hand, once, to make a
by-hand rollback durable, is a human undoing their own action via raw SQL,
not a new automated demotion path, and nothing in this checkpoint's code
performs it.

## Change 7 — report.py: gap to the next threshold, per candidate

Added to `_candidates_traits_section` (no new flat `FIELDS` entry — a list,
like the existing `_traits`/`_atoms_by_day`, not a scalar): for every
`candidates` row, `_gap(evidence_count)` returns the distance to the
smallest `TRAIT_THRESHOLDS` value still above it, or `0` once
evidence_count is at or above the highest tier (core, 50) — nothing left
to cross. `render()` prints one line per candidate under the existing "2d.
candidates / traits" section: `"{name}: evidence={n}  gap={g}"`, or "(at or
above the highest tier)" at gap 0. The other two things change 7 asks for
— "traits promoted in the window" and "current trait count with names and
confidences" — were already present from CP-E's own report.py work
(`candidates_promoted_window`, `trait_count` + the `_traits` render loop)
and needed no change.

## Files touched outside the FILES set

- **`lyra_ai/lyra_core/runtime.py`** — `TRAIT_PROMOTED` constant + the
  `promote_traits()` call site in `_finish_exchange`. Structurally
  unavoidable: CHANGES item 2 asks for promotion to run "on the daemon
  path", and `runtime.py` is the only file that drives a real turn — the
  same standard CP-E applied to this same file for the same reason.
- **`lyra_ai/tests/test_runtime.py`** — `_FakeCore.promote_traits` (a
  bare method the fake needed once `_finish_exchange` started calling it
  unconditionally on every retrieval-executed turn) plus three new tests
  for the call and its logging. Without this addition every existing test
  that drives `handler.handle()` against a `_FakeCore` would raise
  `AttributeError`.
- **`lyra-memory/lyra_memory/models.py`** — `Candidate.category`'s
  `Literal` gained `"retrieval"` (Change 1's second finding). Not
  structurally optional the way the two touches above are stylistic
  consistency — without it, `IdentityEngine.consolidate()` raises on any
  store containing a retrieval candidate, i.e. promotion cannot run at all
  against this checkpoint's own live store.
- **`lyra_ai/tests/test_core.py`, `lyra_ai/tests/test_report.py`,
  `lyra-memory/tests/test_trait_history.py`** — new tests for
  `promote_traits()`, the report.py gap section, and `consolidate()`'s new
  return value, respectively. No existing test in any of the three files
  was changed in a way that altered its assertions (test_trait_history.py's
  two edits only add a new assertion on the now-meaningful return value
  alongside the pre-existing ones).

## DONE-WHEN — evidence

Twenty turns through a real `Runtime` (tmp-path store,
`LYRA_EMBED_BACKEND=hashed`, the same fake backend/message set as CP-E's
verification, driven via `rt._handler.handle()`), then inspected directly
and via `collect_from_path()`:

- **Trait count before/after:** 0 -> 1.
- **A trait promoted.** Candidate: `"retrieval finds relevant context"`.
  Evidence count at promotion: 5. Threshold crossed: `TRAIT_THRESHOLDS
  ["surface"] = 5`. Turn 6 of 20 (the fifth turn whose retrieval intent
  executed and found context — turns interleave "context found"/"nothing
  found" replies).
  ```
  2026-09-02T23:47:02.435172 [IdentityEngine] TRAIT_PROMOTED name='retrieval finds relevant context' evidence_count=5 threshold=5 stability='surface'
  2026-09-02 23:47:02,435 INFO lyra_core.runtime: TRAIT_PROMOTED turn=6 trait_name='retrieval finds relevant context' evidence_count=5 threshold=5
  ```
  By turn 20: `evidence_count=19`, `stability='character'`,
  `confidence=0.38`. `trait_history` has 15 rows for this one trait: 1
  `promoted`, 9 `confidence_change`, 1 `tier_change` (surface->character at
  evidence_count=15), 4 more `confidence_change`.
- **sqlite shows the trait row, its trait_history row(s), and the outcome
  rows named as its evidence:**
  ```
  traits:   (1, 'retrieval finds relevant context', '...at least one atom...', 0.38, 'character', 19)
  trait_history: 15 rows, trait_id=1 throughout, event history exactly as above
  candidates: (2, 'retrieval finds relevant context', 19)  -- evidence_text: 19 lines of outcome_id=N (CP-E's mechanism, unmodified)
  ```
- **The trait's name and description, verbatim** (the first emergent
  trait — nobody authored this string, it was assembled by `_retrieval_
  trait_from_outcome` in CP-D and never written to `traits` until this
  checkpoint):
  - name: `retrieval finds relevant context`
  - description: `an assembled context contained at least one atom above the retrievability floor`
- **`python -m lyra_core --report`-equivalent output** (via
  `collect_from_path`/`render` against the verification store):
  ```
  2d. candidates / traits (grouped — a row is a distinct, post-dedup candidate)
    distinct candidates 2
    total evidence      20
    largest evidence    19
    singletons          1
    created/strengthened (window)  2
    promoted (window)   1
    trait count         1
      retrieval finds relevant context: an assembled context contained at least one atom above the retrievability floor (confidence=0.38)
    candidates — evidence vs. next threshold not yet crossed (CP-F):
      retrieval finds relevant context: evidence=19  gap=31
      retrieval finds nothing: evidence=1  gap=4
  ```
- **The rollback statements executed against a scratch copy**, both
  variants, evidence above under Change 6.
- **Traits visible to Lyra, read-only, verified by calling
  `retrieve_context()` after the 20th turn:**
  ```
  context.text contains '## Traits': True
  ## Traits
  - retrieval finds relevant context: an assembled context contained at least one atom above the retrievability floor
  ```
  **No write path reachable from her side** — `grep -rn "INSERT INTO
  traits\|UPDATE traits\|INSERT INTO trait_history" --include="*.py" .`
  (excluding `venv`/`__pycache__`) finds writers only in
  `identity_engine.py` (production) and test fixtures that seed state
  directly (`test_context.py`, `test_inspect_state.py`,
  `test_trait_history.py`); `promote_traits()`'s only production caller is
  `runtime.py`'s `_finish_exchange`, itself never conditioned on anything
  in her response — the only inspection of her output anywhere in
  `runtime.py` is `_TOOL_TOKENS`, a fixed two-entry dict
  (`[TOOL:see:screen]`, `[TOOL:see:webcam]`) with no trait-shaped or
  candidate-shaped entry.
- **Both test suites green:** `lyra_ai` 351 passed (339 + 12 new: 3 in
  `test_runtime.py`, 5 in `test_core.py`, 4 in `test_report.py`);
  `lyra-memory` 283 passed, 4 skipped (unchanged count — `test_trait_
  history.py`'s two edits added assertions to existing tests rather than
  new tests).

# CP-G — repo history, indexed and citation-checked

Register corrections carried in verbatim: the first emergent trait
("retrieval finds relevant context") promoted turn 6 at evidence_count 5,
character tier by turn 20 — produced by mechanism, not authorship;
`IdentityEngine.consolidate()`'s two dead callers (unscheduled DreamPass,
wrong-database MemorySystem/DreamingLoop) are fixed — it runs per-turn from
runtime.py now; traits reach Lyra through `_traits_block` above
`TRAIT_CONFIDENCE_FLOOR` 0.3, `introspect()` needed no change;
`Candidate.category` now includes `"retrieval"`; every outcome signal to
date is internal, and the first trait is near-trivially true because of it.

## Change 1 — where a commit lives, and why

Considered atoms, facts, and entities (CHANGES' own three candidates).

**Atoms ruled out, measured, not assumed.** Read `store/context.py`'s three
recall paths before deciding anything: `_semantic_hits` joins `vec_atoms`
to `atoms` unconditionally; `_lexical_hits` matches `atoms_fts` (populated
by an insert trigger — schema.py's `FTS_SQL`) with no source filter;
`_temporal_hits` is `SELECT ... FROM atoms ... ORDER BY ts DESC` with no
source filter either. None of the three recall paths know about `source`
at all — a commit written as an atom, under ANY source string, is
findable by all three the moment it exists, deterministically for
`_temporal_hits` alone. OUT OF SCOPE forbids touching `store/context.py`
("changing retrieval"), so there is no way to exclude a new atom source
from recall without violating that — atoms was excluded by this constraint
before any design work started, not chosen against on taste.

**Facts chosen.** The row a `repo_index.py` commit becomes:

| column | value |
|---|---|
| `subject` | the commit's full 40-hex-char hash |
| `text` | `"[<short-hash>] <date> <author>: <subject-line>"` |
| `source_kind` | `"repo_commit"` |
| `confidence` | `1.0` |
| `source_atom_id` | `NULL` |
| `valid_from` | the commit's own author timestamp |
| `valid_until` | `NULL` |
| `ts` | indexing time (`time.time()` at the moment `repo_index.py` ran) |

`source_kind` has no `CHECK` constraint in `schema.py` — the same reason
`source`/`environment` are enforced in Python (`SOURCES`/`ENVIRONMENTS`
frozensets), per schema.py's own comment: "a CHECK constraint on a growing
vocabulary is exactly the migration this schema rules out." Adding
`"repo_commit"` as a fifth `source_kind` value (alongside
stated/observed/document/inferred) needed no schema change and none was
made — verified: `git diff` over `schema.py` for this checkpoint is empty.

**Distinguishable in the store, structurally, not by convention.** A
`facts` row is never `atoms`-table content. `store/passes/dream.py`'s
`DreamPass._input_atoms()` — the only path from anything to
candidate/promotion — reads `atoms` exclusively (`SELECT ... FROM atoms a
LEFT JOIN dream_atoms ...`). A `facts` row with `source_kind="repo_commit"`
is therefore not merely excluded from dream input the way
`DREAM_EXCLUDED_SOURCES` excludes `sandbox_read` atoms (a WHERE clause
filtering something that COULD otherwise be read) — it is not the kind of
row that pipeline reads AT ALL. "A commit is not an experience she had; it
must be distinguishable from things that are" (CHANGES item 1's own
words) is true here in the strongest available sense: nothing downstream
of `atoms` can mistake it for one, because it was never one.

**Excluded from ordinary conversational retrieval, also structurally, not
by a new filter.** `_facts_block` (`store/context.py`, unmodified — OUT OF
SCOPE) injects a fact only when a query word exactly matches its
`subject`, lowercased. A commit's subject is a 40-hex-char hash; no
ordinary conversational sentence contains one. Repo rows reach a prompt
through an entirely separate path — `CognitiveCore.retrieve_repo_context()`
(interface.py), wired to the new `repo_query` intent kind, never through
`build_context`/`_facts_block`/`_recall_block`. Live-verified below (turn
5): a repo-unrelated conversational turn's `context_log` row has
`fact_ids: []` even though 159 repo-commit facts exist in the same store,
and turns 2-4 (repo-related, which DO inject a repo block) still show
`fact_ids: []` too — the two channels never touch, confirmed on both
repo and non-repo turns, not assumed from the design alone.

## Change 2 — repo_index.py

Reads `git log --format=%H\x1f%h\x1f%an\x1f%aI\x1f%s\x1e` via `subprocess`
(control-character field/record separators — a commit subject line
legally containing `|` would corrupt a pipe-delimited parse; tested
directly, see test_repo_index.py). Writes one `facts` row per commit not
already indexed (idempotency: `SELECT subject FROM facts WHERE
source_kind='repo_commit'` first, skip full hashes already present — no
UNIQUE constraint exists or was added on `facts.subject`, so this is
enforced in Python, the same discipline `source_kind`'s own growing
vocabulary already requires per Change 1).

Opens the store via `lyra_memory.store.Store.open()` (full schema
assertion + WAL + foreign_keys, the correct way to open a WRITABLE
connection against this schema) rather than report.py's `mode=ro`
`_ReadOnlyStore` pattern (that pattern exists specifically so a read-only
tool cannot write; this tool's whole job is to write). Does not import
`lyra_core.runtime.Runtime`/`TurnHandler`/`lyra_core.transport` (CHANGES
item 2's own words, mirroring report.py's identical discipline for the
identical reason) and does not import or call `lyra_memory.embeddings.
embed()` at all — commit facts are never vectorized (`retrieve_repo_
context` is a plain SQL scan, not KNN), so this tool needs no
`LYRA_EMBED_BACKEND` and no network to run.

One environment finding, not a design decision: this container's cached
`all-MiniLM-L6-v2` was NOT reachable earlier in this session (CP-B/CP-E
both recorded `LYRA_EMBED_BACKEND=hashed` as forced) but IS reachable now
— a bare `Store.open()` on a NEW store stamps `schema_meta.embedder` with
whatever `backend_id()` returns at that moment, real or offline stand-in,
regardless of whether the opener ever calls `embed()`. Running
`repo_index.py` and the verification `Runtime` under different embedder
choices produced a real `SchemaMismatch` on the second process
(store/integrity.py's own designed behavior — a mismatch crashes on open,
by design, per Change 1's schema-change discussion above). Not a bug;
recorded here because it is exactly the kind of drift `assert_schema`
exists to catch, and because it means anyone re-running this checkpoint's
verification must set `LYRA_EMBED_BACKEND=hashed` (or leave it unset
consistently) for every process that opens the SAME store — `repo_index.py`
included, even though it never embeds anything itself.

## Change 3 — the repo_query intent kind

`IntentKind.repo_query = "repo_query"` (interface.py). Payload:
`{"reason": "repo"}` — matching the minimal `{"reason": ...}` shape every
other kind already uses (`retrieval`/"turn", `look`/"curiosity",
`speak`/"boredom"/"friction"); the query text itself is not carried in the
payload, the same way `retrieval`'s isn't — `runtime.py` already has
`message` directly and passes it straight to `retrieve_repo_context()`,
matching `retrieve_context()`'s own precedent.

**Deliberately NOT routed through `ActionSelector`/drives.**
`action_selection.py` is explicitly OUT OF SCOPE ("changing... drives").
Retrieval's own trigger (`_RETRIEVAL_PRESSURE` compared against frustration
inside `ActionSelector.select()`) lives there; extending it for repo_query
would mean editing that file, which CP-G forbids. Instead, `tick()`
(interface.py, in FILES) appends `Intent(kind=IntentKind.repo_query, ...)`
directly — after `self._selector.select(...)` runs, before `_gate_intents`
— when `_looks_like_repo_query(observations)` matches a fixed keyword set
(`commit(s)`/`committed`/`checkpoint(s)`/`repo`/`repository`/`codebase`)
against the tick's own "conversation" observation, restricted to
`awaiting_reply` exactly as retrieval already is (never on her own "lyra"
turn — tested directly). This is simpler than retrieval's design on
purpose: nothing in CHANGES asks for frustration-gating here, and building
it without touching action_selection.py is not straightforward (the
frustration-vs-pressure comparison is that file's one mechanism); the
simpler, deterministic, in-scope design was chosen over replicating
retrieval's shape for its own sake. There is consequently no
`repo_query`-declined state — no equivalent of `INTENT_DECLINED` — see
Change 8/runtime.py's own comment on `REPO_QUERY_PRODUCED`.

Registered in `gate.ALLOWED_KINDS` (`gate.py`, not in the closed FILES set
— CHANGES item 3 explicitly instructs this, the same kind of mandated
companion touch CP-E/CP-F's precedent already established; see "Files
touched outside the FILES set" below). `test_gate.py`'s tripwire test
(`test_allow_list_is_exactly_the_five_reviewed_kinds`, renamed to `_six_`)
caught the omission immediately on the first test run after adding the
`IntentKind` member but before touching `gate.py` — exactly the mechanism
that test exists to be.

## Change 4 — the repo block, and the citation instruction

`retrieve_repo_context(query)` (interface.py): two passes over
`facts WHERE source_kind='repo_commit'`, no new index (a `facts_fts`
virtual table would be a schema change, forbidden). Pass 1: any hex-looking
token in the query (7-40 hex chars) tried as a `subject LIKE token||'%'`
prefix — treats "what does commit abc1234 do" as the strong, well-defined
request it is. Pass 2, filling up to `_REPO_QUERY_ROW_LIMIT` (10) rows: a
plain `OR`-of-`LIKE` scan over `text` using the query's own words (length
>2, no stopword filtering — the corpus is small enough and short lines
enough that this is adequate; a fixed row-count cap, not
`CONTEXT_BUDGETS`-style token budgeting, since commit lines are already
short and uniform by construction).

The block:
```
## Repository history (git log metadata for this repository — not
something you experienced, and not conversation. Cite the bracketed hash,
e.g. [abc1234], for any commit you rely on. Do not cite a hash you have
not seen here.)
- [5c8163c] 2026-09-02 Claude: CP-D: close the intent loop for retrieval
- [2d7a91f] 2026-09-02 Claude: CP-E: candidate deduplication
...
```
appended in `TurnHandler._compose_system_prompt` (runtime.py) after
conversational context and before the affect hint — its own heading is
what makes it "distinguishable from conversational context" (change 4's
own words), independent of position.

## Change 5 — citation existence, the outcome bit

`check_repo_citations(reply_text)` (interface.py) parses every `[hash]`
(`_CITATION_RE`, 7-40 lowercase hex) out of the reply and checks EACH
against the FULL index — `facts WHERE source_kind='repo_commit' AND
subject LIKE cited||'%'` — not just the rows this turn happened to
retrieve: a correct citation to a commit surfaced on an earlier turn, or
recalled from her own prior knowledge of this repo, is still a real hit,
and restricting validation to "this turn's retrieved set" would have
manufactured false misses for a genuinely correct answer. Counted PER
OCCURRENCE, not deduplicated — citing the same hash twice is two things to
check, not one (tested directly: `[aaaaaaa]` twice + one fabricated hash =
`(2, 1)`).

`record_repo_query_outcome` writes to the existing `outcomes` table (no
new table): `valence = 0.0` the moment `miss_count > 0` — change 5's own
wording, "a reply citing a hash that is not in the index is a FALSE
outcome," read literally: one miss makes the whole outcome false, not a
ratio. `environment` packs `kind=repo_query;context_log_id=...;hits=N;
misses=N` into the same free-text `key=value;...` column
`record_retrieval_outcome`'s own `environment` already uses (OUT OF SCOPE
forbids a schema change to give repo-query outcomes real columns); the
`kind=repo_query` prefix is what lets report.py select only these rows out
of the one shared table.

Nothing here suppresses, retries, or repairs a miss — `check_repo_
citations` and `record_repo_query_outcome` are pure read/write with no
branch that could do any of the three; `runtime.py`'s `_finish_exchange`
calls them once per repo-query-executed turn and moves on.

## Change 6 — zero is a recorded row, not an absent one

`_finish_exchange` calls `record_repo_query_outcome` whenever
`repo_query_intended` is true, unconditionally — not gated on `hit_count +
miss_count > 0` the way retrieval's own `outcome_id is None` check gates
its downstream steps. Live-verified (turn 4 below): a repo-related turn
that retrieved 10 commit rows but cited none still produced outcome row
`(7, ..., hits=0, misses=0)`, logged via `REPO_OUTCOME_RECORDED`. Tested
directly for the "nothing retrieved" half too
(`test_record_repo_query_outcome_records_zero_zero_turn`).

## Change 7 — no candidate vocabulary was added

`candidate_pool.py` was NOT touched. CHANGES item 7 is conditional ("if
the candidate vocabulary needs a repo-query label") and it did not: SCOPE
is "index... and can answer over it, with an outcome signal that can come
back false" — the `outcomes` row IS that signal, and neither DONE-WHEN nor
any other CHANGES item asks repo-query outcomes to feed
`CandidatePool.add_observation`/consolidation/promotion. Doing so anyway
would mean deciding a trait vocabulary for "cites accurately"/"cites
falsely" and touching the consolidation path — real design work outside
what this checkpoint asked for, and adjacent to "changing... promotion"
(OUT OF SCOPE). Left for a future checkpoint to decide deliberately, the
same way CP-D left promotion itself unwired for CP-F to pick up.

## Change 8 — report.py

Two new sections, kept separate from CP-D's own fields on purpose (see
runtime.py's `REPO_QUERY_PRODUCED` comment): `commits_indexed` (all-time —
indexing is a one-off explicit act like `atoms_total`, not a window flow),
`repo_query_produced_window`/`executed_window` (new markers, own regex,
`_repo_loop_log_section`), and `repo_citations_checked_window`/
`hit_window`/`miss_window`/`repo_citation_false_rate_window` (read from
`outcomes.environment`, `_repo_section`) — the false rate rendered to 4
decimal places, always a number (0.0 on a fresh store, never `UNAVAILABLE`
unless the DB query itself fails).

## Files touched outside the FILES set

- **`lyra_ai/lyra_core/gate.py`** — `ALLOWED_KINDS` gains
  `IntentKind.repo_query`, per CHANGES item 3's explicit instruction ("Register
  it in gate.ALLOWED_KINDS"). Not a judgment call — a directly mandated edit
  to a file outside the closed set, the same standing this checkpoint's own
  text gives it.
- **`lyra_ai/tests/test_gate.py`** — the ALLOWED_KINDS tripwire test updated
  to expect six kinds instead of five (renamed
  `test_allow_list_is_exactly_the_five_reviewed_kinds` ->
  `..._six_reviewed_kinds`). This test existing and failing immediately is
  the mechanism working as designed, not a problem to route around.
- **`lyra_ai/tests/test_core.py`, `test_runtime.py`, `test_report.py`** —
  new tests for `retrieve_repo_context`/`check_repo_citations`/
  `record_repo_query_outcome`, the daemon-path wiring (`_FakeCore` gained
  `retrieve_repo_context`/`check_repo_citations`/`record_repo_query_
  outcome` and a `produces_repo_query` flag), and the two new report.py
  sections, respectively — the same standing CP-E/CP-F's own test-file
  companions had.
- **`lyra_ai/tests/test_repo_index.py`** (new) — builds real git
  repositories with `subprocess` (`git init`/`commit`, global identity
  already configured in this environment) rather than mocking `git log`.

## DONE-WHEN — evidence

`repo_index.py` run twice against the real Lyra repository
(`/home/user/Lyra`, 159 commits at HEAD):
```
repo_index: /home/user/Lyra — 159 commits found, 159 indexed, 0 already present (skipped).
repo_index: /home/user/Lyra — 159 commits found, 0 indexed, 159 already present (skipped).
```
sqlite: 159 rows, all `source_kind='repo_commit', confidence=1.0,
source_atom_id=NULL, valid_until=NULL`, subject a 40-hex-char hash, text
the bracketed-citation form — e.g. `('aac3b4459f0bea...', '[aac3b44]
2026-09-02 Claude: CP-F: wire candidate-to-trait promotion into the live
daemon', 'repo_commit', 1.0, None, None)`.

Live verification: a real `Runtime` (tmp-path store indexed as above,
`LYRA_EMBED_BACKEND=hashed`) driven through five turns via
`rt._handler.handle()`. **No LLM API key is configured in this
environment** — verified directly: no `.env` file anywhere in the repo,
`env | grep -iE "anthropic|openrouter|cerebras|google_api"` returns
nothing, so `lyra.backends.auto_select_backend()` has no real backend to
select. Every prior checkpoint's live daemon verification in this session
used a scripted fake backend for the same reason; this one does too, with
replies written by hand to demonstrate the mechanism against the real
indexed commit set — not generated by any model. This bears directly on
the Wilson-gradable check below.

- **Turn 2** — "what commit closed the intent loop for retrieval":
  ```
  REPO_QUERY_PRODUCED turn=2 kind=repo_query
  REPO_QUERY_EXECUTED turn=2 kind=repo_query commit_count=10
  reply: "The intent loop for retrieval was closed in commit [5c8163c], per the repo history."
  REPO_OUTCOME_RECORDED turn=2 outcome_id=3 hits=1 misses=0
  outcomes row: (3, 3, 1.0, 'citations_valid', 'citations_valid', 'kind=repo_query;context_log_id=2;hits=1;misses=0')
  ```
  `5c8163c` is the real short hash of `5c8163c32d8bfb8a61763c4529eaf9be060beed8`
  ("CP-D: close the intent loop for retrieval") — verified against `git
  log` directly, not assumed.
- **Turn 3** — "what commit added candidate deduplication", reply
  scripted to cite one real hash and one fabricated one (to demonstrate
  the miss path, not to claim a real model produced it — disclosed, not
  hidden):
  ```
  reply: "Candidate deduplication was added in commit [2d7a91f]. I'll also mention [deadbeef] for context, though I'm not fully sure about that one."
  REPO_OUTCOME_RECORDED turn=3 outcome_id=5 hits=1 misses=1
  outcomes row: (5, 5, 0.0, 'citations_invalid', 'citations_valid', 'kind=repo_query;context_log_id=3;hits=1;misses=1')
  ```
  `2d7a91f` is real (CP-E: candidate deduplication, verified against `git
  log`); `deadbeef` is not an indexed commit — `valence=0.0`, confirming a
  false outcome IS recorded, not suppressed, retried, or repaired. This is
  the DONE-WHEN citation-miss requirement — produced by a deliberately
  scripted reply, recorded as such rather than presented as a discovered
  hallucination, per the same "do not manufacture" ethic DONE-WHEN itself
  states for this exact case.
- **Turn 4** — "did we ever add a commit about time travel to this repo":
  repo_query fired (10 commit rows retrieved, none about time travel — a
  correct, honest reply citing nothing):
  ```
  REPO_OUTCOME_RECORDED turn=4 outcome_id=7 hits=0 misses=0
  ```
  Change 6's requirement, observed directly: rows retrieved, nothing
  cited, still a recorded outcome row, not an absent one.
- **Turn 5** — "what did we decide about the threshold last time" (no
  repo keyword): no `REPO_QUERY_PRODUCED`/`EXECUTED`/`REPO_OUTCOME_
  RECORDED` line at all; `context_log` row 5:
  `atom_ids=[1..8], fact_ids=[]` — conversational atoms only, zero commit
  facts, even though 159 exist in the same store. **Conversational
  retrieval is not polluted.** The same holds on the repo-answering turns
  themselves (rows 2-4 also show `fact_ids: []`) — the repo channel and
  the conversational-facts channel never share a row, on any turn observed.

`python -m lyra_core --report`-equivalent output:
```
2j. repo index (CP-G)
  commits indexed     159
  repo_query produced 3  (window)
  repo_query executed 3  (window)
  citations checked   3  (window)
  citations existed   2  (window)
  citations did not   1  (window)
  citation false rate 0.3333  (window)
```
The false rate is a visible number, per change 8's own requirement.

**Wilson-gradable check — recorded as a gap, not manufactured.** DONE-WHEN
asks for three questions with known answers, replies recorded verbatim,
graded by human judgment. The three Q&A pairs above (turns 2-4) ARE
recorded verbatim, and their citations were independently checked against
real `git log` output (not merely trusted) — but the replies themselves
were written by hand for this verification, not generated by a model, for
the concrete reason stated above (no LLM API key configured in this
environment). Grading a hand-scripted reply against the ground truth I
used to write it is circular and would not be a meaningful signal — the
whole point of change 5/8's false rate is that it can come back true
against a process that MIGHT be wrong, and a script I wrote is not that
process. What this session DOES establish, mechanically and verifiably: the
citation-parse/validate/record/report pipeline is correct end to end
against real indexed data, including a genuine detected miss. What it does
NOT establish, and cannot in this environment: whether a real model,
asked these same three questions against this same index, would answer
correctly. That check needs a configured backend and a human to grade the
result — recorded here as the honest state, per this checkpoint's own
"do not manufacture one" instruction applied to the same problem one level
up.

**Both test suites green:** `lyra_ai` 397 passed (351 before this
checkpoint + 46 net new — `test_gate.py`'s tripwire test was renamed, not
added to; `test_repo_index.py` is a new file with 12 tests; the remainder
are new tests added to `test_core.py`, `test_runtime.py`, and
`test_report.py`); `lyra-memory` 283 passed, 4 skipped (unchanged — CP-G's
FILES set includes `candidate_pool.py` only conditionally, and Change 7
found the condition unmet, so lyra-memory's own tree and tests are
untouched this checkpoint).

# CP-H — the citation outcome consolidates

Register corrections carried in verbatim: `facts` now holds three
populations (world, machine affect_state, repo commits) in one table;
`context.py`'s recall paths do not filter by source, so atom purity is
maintained by never writing non-experience into `atoms`, not by exclusion
at read time; `IntentKind.repo_query` is registered but not declinable and
not routed through `ActionSelector` — a content trigger, not a decision;
citation existence is the first outcome signal that can come back false,
and as of CP-G it was recorded and nothing consolidated it; 159 commits
indexed as of CP-G (160 now — this checkpoint's own commits landed on the
branch in between); the three-question semantic check against a real
backend is still outstanding.

## Change 1 — the closed citation candidate vocabulary

Four labels, exact match (`closed_vocabulary=True`), no embedding — the
same reasoning CP-D's frustration vocabulary and CP-G's own retrieval
vocabulary already established for a fixed, template-generated set: these
four descriptions share nearly every word with their neighbors and differ
only in the one fact that decides them, exactly the shape a semantic
threshold loose enough to merge genuine duplicates would also merge apart.
Not re-measured against the offline embedder a third time — the shape is
identical to the two prior closed vocabularies in this codebase (one
template, one or two booleans flipped), not merely similar, and CP-G
already measured that shape unsafe to dedup semantically.

| label | description |
|---|---|
| `repo citations verified` | an executed repo-query turn's reply cited one or more hashes, and every cited hash was found in the indexed commits |
| `repo citations include a false hash` | an executed repo-query turn's reply cited one or more hashes, and at least one cited hash was not found in the indexed commits |
| `repo context given but nothing cited` | an executed repo-query turn retrieved commit rows from the index, but the reply cited none of them |
| `no repo context to cite` | an executed repo-query turn retrieved no commit rows from the index, so the reply had nothing to cite |

The two "nothing cited" branches are kept apart on purpose, per CHANGES'
own instruction: the third label is about her declining to use context she
was given; the fourth is about retrieval finding nothing for her to use.
Collapsing them into one "nothing cited" bucket would erase exactly the
distinction that makes one of them about her behavior and the other about
the index's coverage.

`_repo_citation_trait_from_outcome(had_repo_context, hit_count, miss_count)`
(interface.py) selects the branch: `hit_count + miss_count == 0` splits on
`had_repo_context` for labels 3/4; otherwise `miss_count > 0` selects label
2, else label 1. Every branch is reachable from real inputs
`check_repo_citations`/`retrieve_repo_context` already produce — no branch
is dead code, confirmed live below (all four fired in the same run).

## Change 2 — reuse, not a second consolidation path

`CognitiveCore.consolidate_repo_citation_outcome()` (interface.py) is
structurally identical to `consolidate_retrieval_outcome()` (CP-D/E): fresh
`CandidatePool(db)` against `self._memory.db`, `add_observation(trait_name,
trait_value, "repo_citation", evidence=..., closed_vocabulary=True)`. No
new code was written in `candidate_pool.py` — `add_observation`'s
`closed_vocabulary=True` path already does exactly what a fourth closed
vocabulary needs; CHANGES' own FILES note ("vocabulary only") anticipated
this, and in the end no vocabulary-shaped code change was needed there
either, since the vocabulary lives in interface.py's
`_repo_citation_trait_from_outcome`, not in `candidate_pool.py` itself
(which has never contained any of the three prior closed vocabularies'
label strings — it is generic over whatever `trait_name` a caller passes).
`candidate_pool.py`'s diff for this checkpoint is empty.

**A recurring finding, not a new bug class:** wiring this immediately hit
the same blocker CP-F found for `category="retrieval"` —
`Candidate.category`'s pydantic `Literal` (`lyra_memory/models.py`) did not
include `"repo_citation"`, so `CandidatePool.get_candidates()` (called by
`IdentityEngine.consolidate()`, called by `promote_traits()`) would raise
`ValidationError` on the first `promote_traits()` call after a citation
candidate existed — not just failing to read repo_citation candidates, but
failing to read ANY candidate, the identical failure mode CP-F documented.
Fixed the same way: `"repo_citation"` added to the Literal.
`lyra_memory/models.py` is not in CP-H's FILES set; this is the same
structurally-unavoidable companion touch CP-F's own precedent established
for this exact recurring situation — see "Files touched outside the FILES
set" below.

## Change 3 — provenance, unmodified

`evidence=f"outcome_id={outcome_id}"` is passed through exactly as
`consolidate_retrieval_outcome` already does; `candidate_pool.py`'s
`_append_evidence` (CP-E) accumulates it into `evidence_text` with no
changes. Live-verified below: the false-hash candidate's `evidence_text`
resolves to two real `outcomes` rows, each carrying its own `hits=`/
`misses=` count in its own `environment` field (CP-G's packing, also
unmodified) — a citation candidate's contributing turns, and what each one
actually found, are both recoverable from the store alone.

## Change 4 — promotion, exactly as wired at CP-F

No new call site, no new condition, no check on category anywhere near
promotion. `runtime.py`'s existing `promote_traits()` call (inside the
`if retrieval_intended:` branch, unchanged from CP-F/CP-G) calls
`IdentityEngine.consolidate()`, which reads `CandidatePool.get_candidates
(min_evidence=1)` — every row, every category, exactly as it always has.
A `repo_citation` candidate that reaches `evidence_count >= TRAIT_
THRESHOLDS["surface"]` (5) promotes through this unchanged path the moment
`promote_traits()` next runs, with no special case written for it anywhere
in this checkpoint's diff. Live-verified below: `repo citations verified`
promoted at evidence_count=5 on the very next retrieval-executed turn
after crossing the threshold — the same one-tick lag CP-F's own retrieval
trait showed, for the identical reason (promotion is tied to `retrieval_
intended`, not to which candidate changed).

One structural note, not a gap this checkpoint needed to close:
`promote_traits()` is called from inside `if retrieval_intended:`, not
`if repo_query_intended:`. Since `repo_query` is never declined (CP-G) but
`retrieval` can be (frustration), a citation candidate could in principle
have to wait for a later retrieval-executed turn to be picked up by
promotion, on the rare turn where retrieval declines but repo_query still
fires. CHANGES change 4 says "do not gate it, do not special-case it" —
read as an instruction not to add a NEW condition around promotion for
citation candidates specifically, which this checkpoint honors by leaving
`promote_traits()`'s own trigger untouched rather than duplicating the
call under `repo_query_intended` too (which would be two calls to the same
idempotent operation on turns where both fire, and a second, redundant
condition to reason about). Not exercised in the live run below (retrieval
was never declined across ten turns), and not a case this checkpoint was
asked to handle.

## Change 5 — CITATION_OUTCOME

`runtime.py` gains `CITATION_OUTCOME = "CITATION_OUTCOME"`, logged once per
turn immediately after `REPO_OUTCOME_RECORDED`, under the same `turn=` id
— "joining the existing per-turn marker sequence" read literally: this
line sits beside the outcome-row log line the way `CONSOLIDATOR_FIRED`
already sits beside `OUTCOME_RECORDED` for retrieval, not replacing
anything. Carries `label=` and the same `hits=`/`misses=` counts
`REPO_OUTCOME_RECORDED` logged one line above, so `grep CITATION_OUTCOME`
alone tells a reader which branch fired and why, without cross-referencing
the outcome line.

## Change 6 — report.py, per label

`_repo_citation_candidates_section` (new): `SELECT trait_name,
evidence_count FROM candidates WHERE category = 'repo_citation'`, gap to
the next `TRAIT_THRESHOLDS` value via `_gap_to_threshold` — the identical
formula `_candidates_traits_section`'s own gap list already used, factored
out into one shared function rather than duplicated a third time (CP-F
first wrote it, CP-H's is the second copy that made extracting it worth
doing). Rendered as its own "2k. repo citation candidates, per label"
section, distinct from the all-categories "2d." breakdown that already
includes these rows undifferentiated — DONE-WHEN asks to SEE the labels
broken out, not merely confirm they exist somewhere in the generic list.

## Files touched outside the FILES set

- **`lyra-memory/lyra_memory/models.py`** — `Candidate.category`'s
  `Literal` gains `"repo_citation"` (Change 2's finding). Structurally
  unavoidable, not stylistic: without it `IdentityEngine.consolidate()`
  raises on any store containing a repo_citation candidate, i.e.
  `promote_traits()` — already running unconditionally per Change 4 — would
  crash on its very next call once this checkpoint's own consolidator ever
  fired once. The identical justification CP-F recorded for `"retrieval"`,
  recurring for the reason CP-F's own comment already names: a closed
  vocabulary category is data this Literal has to know about before
  anything using it can be read back, not merely written.
- **`lyra_ai/tests/test_core.py`, `test_runtime.py`, `test_report.py`** —
  new tests for `consolidate_repo_citation_outcome` (label selection,
  dedup-by-exact-match, provenance, promotion via the unmodified
  `promote_traits()`), the daemon-path wiring (`_FakeCore` gained
  `consolidate_repo_citation_outcome` and a `repo_citation_consolidations`
  recorder), and the new report.py section — the same standing every prior
  checkpoint's test-file companions had.

## DONE-WHEN — evidence

Ten turns through a real `Runtime` (tmp-path store indexed against the
real Lyra repository — 160 commits at the time of this run —
`LYRA_EMBED_BACKEND=hashed`, the same scripted-backend disclosure as CP-G:
no LLM API key is configured in this environment, verified directly, so
these replies are hand-written to exercise all four branches, not
generated by a model).

- **Candidates under all four labels**, sqlite:
  ```
  (3, 'repo citations verified',            'repo_citation', 5, 'outcome_id=3\noutcome_id=5\noutcome_id=7\noutcome_id=9\noutcome_id=11')
  (4, 'repo citations include a false hash', 'repo_citation', 2, 'outcome_id=13\noutcome_id=15')
  (5, 'repo context given but nothing cited','repo_citation', 1, 'outcome_id=17')
  (6, 'no repo context to cite',             'repo_citation', 1, 'outcome_id=19')
  ```
- **A candidate carrying evidence from a FALSE citation, provenance
  resolved**: `repo citations include a false hash` (id=4) resolves via
  `evidence_text` to outcome ids 13 and 15:
  ```
  outcome row: (13, 0.0, 'citations_invalid', 'kind=repo_query;context_log_id=7;hits=0;misses=1')
  outcome row: (15, 0.0, 'citations_invalid', 'kind=repo_query;context_log_id=8;hits=0;misses=1')
  ```
  Both carry `valence=0.0` and their own `hits=`/`misses=` counts, exactly
  DONE-WHEN's requirement — "identifiable and contain the miss counts."
  The turn that produced them: asked twice "what commit added candidate
  deduplication", scripted to answer `[deadbeef]` — a hash not in the
  index (the real answer is `2d7a91f`; the wrong hash was written
  deliberately to exercise this label, disclosed here as in CP-G, not
  discovered from a live model).
- **Context-given-but-nothing-cited vs. no-context-to-cite, both rows
  shown, distinct**:
  ```
  [('no repo context to cite', 1), ('repo context given but nothing cited', 1)]
  ```
  Produced by two different turns: "did we ever add a commit about time
  travel to this repo" (10 real commit rows retrieved, reply correctly
  cited none of them — label 3) and "committed anything about xylophone
  hovercraft zeppelin" (repo_query triggered on the word "committed", but
  the keyword-LIKE search over indexed commit text matched zero rows,
  verified directly against the store before relying on it in this run —
  label 4). One measured pitfall worth recording: an earlier version of
  this same "no context" query included the word "repo", which — because
  `retrieve_repo_context`'s keyword pass is a substring `LIKE`, unchanged
  from CP-G — matched commit messages containing "repo" as a SUBSTRING
  ("CP-C: the daemon **repo**rts on itself"), producing label 3 instead of
  4. Not a bug in this checkpoint's code (CP-G's retrieval, out of scope
  to touch, behaved exactly as documented); a bug in the first draft of
  this verification's own query, caught by checking `retrieve_repo_
  context()`'s actual return value directly before trusting it in the full
  run, per this session's standing "measure, don't assume" discipline.
- **`python -m lyra_core --report`-equivalent output**:
  ```
  2k. repo citation candidates, per label (CP-H)
    repo citations verified: evidence=5  gap=10
    repo citations include a false hash: evidence=2  gap=3
    repo context given but nothing cited: evidence=1  gap=4
    no repo context to cite: evidence=1  gap=4
  ```
- **Trait count before/after: 0 -> 2.** A citation trait DID promote:
  ```
  name: repo citations verified
  description: an executed repo-query turn's reply cited one or more hashes, and every cited hash was found in the indexed commits
  evidence_count: 5
  threshold: 5 (TRAIT_THRESHOLDS["surface"])
  stability: surface
  confidence: 0.10
  ```
  logged:
  ```
  TRAIT_PROMOTED turn=7 trait_name='repo citations verified' evidence_count=5 threshold=5
  ```
  This is the second trait this system has ever promoted, and the first
  whose underlying signal (change 5 was checkable and could have come back
  the other way) — CP-F's "retrieval finds relevant context" describes
  machinery that is nearly always true by construction; "repo citations
  verified" describes an answer about the world that was checked against
  ground truth and happened, this run, to hold. The false-hash label
  (evidence=2) did not promote — gap to `TRAIT_THRESHOLDS["surface"]` is 3.
  `TRAIT_THRESHOLDS` is unchanged (`git diff` over `lyra_memory/config.py`
  for this checkpoint is empty, confirmed alongside `schema.py`,
  `store/context.py`, `action_selection.py`, `candidate_pool.py`, and
  `gate.py` — none touched).
- **Both test suites green:** `lyra_ai` 414 passed (397 before this
  checkpoint + 17 new: 7 in `test_core.py`, 5 in `test_runtime.py`, 5 in
  `test_report.py`); `lyra-memory` 283 passed, 4 skipped (unchanged — this
  checkpoint's `candidate_pool.py` diff is empty, so lyra-memory's own tree
  and tests are untouched).

# CP-I — repo-read becomes a scored candidate

**Labeling note.** This checkpoint arrived tagged "=== ACTIVE CHECKPOINT:
CP-H ===" a second time, with a SCOPE unrelated to and inconsistent with
the CP-H already completed and pushed (citation consolidation) — its own
register claimed "CP-G produces no trait... the false-citation signal
lands in an outcome row and stops," which was already false at the time
this message arrived (a citation trait had already promoted). Flagged to
the user directly rather than silently acted on or silently discarded;
confirmed to proceed as CP-I, building on the completed CP-H rather than
redoing or reverting it. Recorded here so the register stays legible: CP-H
is citation consolidation (previous section); CP-I is this section, repo-
read as a scored candidate.

Register corrections carried in verbatim (the SCOPE this message actually
specified): repo intent bypassed drives via a keyword check in `tick()`,
no `ActionSelector` — the second intent kind, and the first one that
wasn't a decision; CP-G/H's citation work produces a candidate/trait, not
"nothing" (the message's own register was wrong about this, as noted
above); `facts` holds three populations, no recall path in `context.py`
filters by source (unchanged, still true, still out of scope to fix); the
three-question gradable check is still outstanding, still Wilson's to run.

## File-name note

FILES named `runtime.py (tick keyword check)` and `action_selector.py`.
The actual locations, verified before editing rather than assumed: the
keyword check (`_looks_like_repo_query`) and the pressure-setting logic in
`tick()` both live in `lyra_ai/lyra_core/interface.py` (`CognitiveCore`),
not `runtime.py` — `runtime.py`'s own `tick()` (`TurnHandler.tick()`) only
calls `self._core.tick(...)` and reads its result. The scoring mechanism
lives in `lyra_ai/lyra_core/action_selection.py`, not `action_selector.py`
(no file by that name exists in this repository). Treated as referring to
these actual files, the same way "the promotion / development module" and
similar FILES entries in earlier checkpoints were resolved to the real
module doing the described thing rather than a literal filename match —
`interface.py` ended up needing more of this checkpoint's diff than
`runtime.py` as a direct consequence.

## Change 1/2 — a candidate, scored, not a direct append

CP-G's `tick()` (interface.py) did:
```python
intents = self._selector.select(pressures, self._affect.state, bias=bias)
if awaiting_reply and _looks_like_repo_query(observations):
    intents.append(Intent(kind=IntentKind.repo_query, payload={"reason": "repo"}))
intents = self._gate_intents(intents)
```
— the keyword match appended the intent directly to the selector's OUTPUT,
after scoring had already happened. Structurally undeclinable: there was
no comparison for it to lose, and CP-G's own DECISIONS.md said so
explicitly ("repo_query is NOT one of ActionSelector's pressures").

CP-I moves the keyword match INTO the pressures dict `tick()` already
builds for boredom/relational/retrieval, at a new `_REPO_QUERY_PRESSURE =
1.0` (same value and same reasoning as `_RETRIEVAL_PRESSURE`: full
strength, no separate lever for "decline repo reads but not conversational
recall" — CHANGES never asked for one, and a second, different pressure
constant would start to resemble the new drive OUT OF SCOPE forbids):
```python
self._last_repo_query_pressure = (
    _REPO_QUERY_PRESSURE if (awaiting_reply and _looks_like_repo_query(observations)) else 0.0
)
pressures = {
    "boredom": ..., "relational": ..., "retrieval": ...,
    "repo_query": self._last_repo_query_pressure,
}
intents = self._selector.select(pressures, self._affect.state, bias=bias)
intents = self._gate_intents(intents)
```
`action_selection.py`'s `ActionSelector.select()` gained the identical
branch retrieval already has:
```python
repo_query = drive_pressures.get("repo_query", 0.0)
if repo_query > 0.0 and repo_query > frustration:
    chosen.append(Intent(kind=IntentKind.repo_query, payload={"reason": "repo"}))
```
Not a new drive (OUT OF SCOPE) — the SAME frustration-vs-pressure
mechanism `BoredomDrive`/`RelationalDrive`/retrieval already share, reused
for a fourth pressure key. `CandidatePool`/promotion/the harm gate/`facts`
storage are all untouched — this checkpoint's diff never enters
`lyra-memory` at all (`git diff --stat -- lyra-memory/` for this
checkpoint is empty).

`_last_repo_query_pressure` is a new `CognitiveCore` attribute, read by
`runtime.py` the SAME established way it already reads
`_boredom.pressure`/`_relational.pressure` (CP-A.1's own comment: "drive
pressure is read off the core's own drive objects rather than adding a new
accessor there") — not a `tick()` return-signature change, which would
have rippled through every existing caller and test that unpacks
`(intents, affect)`. It lets `runtime.py` distinguish three states without
re-deriving `ActionSelector`'s frustration arithmetic (`effective_weight`
is private to the selector, adjustable by bias, and was deliberately not
exposed — the same restraint CP-D's own retrieval-decline log line already
showed: "valence is logged as the legible reason, not a re-derivation of
the flip's arithmetic"):

| pressure | in `last_intents`? | state |
|---|---|---|
| 0.0 | — | not a candidate this tick |
| > 0.0 | yes | WON |
| > 0.0 | no | LOST |

## Change 3 — a loss is an outcomes row

`CognitiveCore.record_repo_query_loss(user_atom_id, context_log_id,
pressure, affect_valence)` (interface.py): one `outcomes` row,
`valence=0.0` (the candidate never executed — nothing to be true or false
about, only a bid that did not win), `actual="lost"`, `predicted=
"repo_query"`, `environment="kind=repo_query_loss;context_log_id=...;
pressure=...;affect_valence=..."` — the same free-text packing convention
every other outcome row in this checkpoint's lineage uses (OUT OF SCOPE
forbids a schema change). "The losing score" is `pressure` (the constant
`tick()` proposed the candidate at — always 1.0 today, but recorded
literally rather than assumed) and `affect_valence` (`introspect().
valence` at the moment of loss) — together they say what was bid and what
beat it, without exposing `ActionSelector`'s internal `effective_weight`.

`runtime.py`'s `_finish_exchange` calls this in a new `elif repo_query_
pressure > 0.0:` branch, sibling to the existing `if repo_query_intended:`
win branch — mutually exclusive by construction (a candidate cannot both
win and lose the same tick). Logged as `REPO_QUERY_LOST` (new marker,
runtime.py), carrying the outcome id, pressure, and valence, joining the
existing per-turn marker sequence the same way `CITATION_OUTCOME` (CP-H)
already does.

## Change 4/5 — grep audit

`grep -rn "Intent(kind=" lyra_ai/lyra_core/*.py`: exactly two production
matches after this checkpoint's own edit —
1. Every branch inside `ActionSelector.select()` (`action_selection.py`),
   boredom/relational/retrieval/repo_query alike. The one, general
   candidate path this checkpoint's SCOPE asks every site to use.
2. `interface.py`'s `_ingest_outcome()`: `Outcome(intent=Intent(kind=
   IntentKind.noop, payload={}), ...)` — inspected directly, not assumed
   safe. This is a REQUIRED but functionally unused field of `development.
   Outcome` (a dataclass `OutcomeConsolidator.record_outcome()` reads only
   `.success`/`.affect`/`.drive` from — `trait_name_from_outcome()`, its
   one consumer, never touches `.intent`). The `Intent` object built here
   is never gated, never selected, never executed, never leaves this
   function — dead data satisfying a required field, not a live proposal.
   Not converted: there is nothing here that bypasses the selector,
   because nothing here is a candidate for anything.

`grep -rln "MCP\|mcp_server\|mcp\." lyra_ai/lyra_core/*.py lyra_ai/lyra/
*.py`: zero matches. `lyra-mcp` (a separate service, wrapping the OTHER
four services — embodiment/voice/listen/vision — as MCP tools for Claude
Desktop, per CLAUDE.md) has no code presence anywhere inside `lyra_ai`,
the daemon's own package. There is no "MCP call site" inside `CognitiveCore`
/`ActionSelector`/`IntentKind` to find, because that whole capability lives
in a wholly separate process this daemon never imports or calls.

**One structurally similar but deliberately NOT converted site, examined
rather than silently skipped:** `runtime.py`'s vision in-band tool-call
protocol (`_TOOL_TOKENS`, `parse_tool_call()`, `call_vision()`). This is a
genuine "tool call site" in the CLAUDE.md sense — the model emits
`[TOOL:see:screen]`/`[TOOL:see:webcam]` mid-reply and `handle()`'s vision
loop detects and executes it. But it does not construct an `Intent` at
all (confirmed by the grep above — neither token, neither function, appear
anywhere near `Intent(`), never touches `IntentKind`/`ActionSelector`/
`gate.ALLOWED_KINDS`, and is not tick()-driven: it fires by parsing the
MODEL's own already-generated text, synchronously inside the same
LLM-response loop that produced that text, before the turn's affect state
even reflects it. This is not "the same bypass" repo_query had (an
Observation-triggered candidate appended outside the scorer) — it predates
the whole `tick()`/`ActionSelector`/`IntentKind` system this checkpoint's
pattern belongs to, and converting it would mean redesigning vision
requests as multi-turn scored candidates (the model asks, a LATER tick
scores and maybe grants it) instead of the same-turn synchronous exchange
it is today — a materially different feature, not a bypass fix, and not
requested by SCOPE, CHANGES, or DONE-WHEN. `IntentKind.look` (boredom →
look) already goes through `ActionSelector` and is unrelated to this
in-band protocol; the two both concern "vision" conceptually but are
architecturally disjoint systems. Recorded here as examined-and-excluded,
not silently missed — the same standard applied to `lyra/assistant.py`'s
own separate (CLI, non-daemon) tool-call handling, briefly checked and
found to be the identical, older, pre-`CognitiveCore` mechanism.

**Converted sites (change 5): exactly one — `IntentKind.repo_query`.**
Nothing else in the grep needed conversion; the audit's finding IS that
repo_query was the only site with this specific bypass shape.

## DONE-WHEN — evidence

Live `Runtime` (tmp-path store indexed against the real Lyra repository —
161 commits — `LYRA_EMBED_BACKEND=hashed`; scripted backend, same
disclosure as CP-G/H: no LLM API key configured in this environment).

- **Repo-read WINS and still executes correctly (CP-G/H unregressed):**
  turn 1, neutral affect, "what commit closed the intent loop for
  retrieval":
  ```
  REPO_QUERY_PRODUCED turn=1 kind=repo_query
  REPO_QUERY_EXECUTED turn=1 kind=repo_query commit_count=10
  reply: "That was commit [5c8163c]."
  CITATION_OUTCOME turn=1 label='repo citations verified' hits=1 misses=0
  outcomes: (1, 'neither', 0.0, ...retrieval...), (2, 'citations_valid', 1.0, 'kind=repo_query;context_log_id=1;hits=1;misses=0')
  candidates: [('repo citations verified', 'repo_citation', 1)]
  ```
  `5c8163c` verified against real `git log` (unchanged from CP-G's own
  verification) — citation checking still works exactly as CP-G/H built
  it.
- **Repo-read LOSES to a competing pressure, visible in the outcome
  table:** `core._affect._emotion_v` forced to -1.5 between turns
  (verification-only — the same kind of private-state manipulation this
  session's own live-verification scripts have used throughout, not a
  production code change), then turn 2, same keyword, "what commit added
  candidate deduplication":
  ```
  reply: "That was commit [2d7a91f]."
  REPO_QUERY_LOST turn=2 outcome_id=3 pressure=1.000000 valence=-1.437746
  outcomes: (3, 'lost', 0.0, 'kind=repo_query_loss;context_log_id=2;pressure=1.0;affect_valence=-1.4377459330739544')
  ```
  Note the reply still names a real commit — the MODEL's own text is
  unaffected by whether repo_query won; what the loss actually withheld is
  the injected `## Repository history` context block and the citation
  check, not her ability to produce text that happens to look similar. No
  `REPO_QUERY_PRODUCED`/`EXECUTED`/`CITATION_OUTCOME` line for turn 2, and
  `repo_citation` candidates are unchanged from after turn 1 (`[('repo
  citations verified', ..., 1)]` both before and after) — the loss did not
  execute, consolidate, or leave any trace in the candidate pool, only in
  the outcome it is honestly recorded as.
- **Grep confirms the closed set:** see Change 4/5 above — `Intent(kind=`
  has exactly two production occurrences (every `ActionSelector.select()`
  branch, and one dead-data placeholder that is not a live proposal), zero
  MCP call sites exist inside `lyra_ai`, and the one examined-but-different
  site (vision's in-band tool protocol) is recorded with its reasoning
  rather than silently passed over.
- **`report.py` shows produced vs. executed as separate counts,
  unregressed and correctly excluding the loss** (report.py itself was not
  touched this checkpoint — not in FILES, and CP-G's existing fields
  already did what DONE-WHEN asks):
  ```
  repo_query produced 1  (window)
  repo_query executed 1  (window)
  citations checked   1  (window)
  citation false rate 0.0000  (window)
  ```
  Both read 1 (the win only) — the lost turn contributes to neither field,
  which is correct: it was never produced in the sense those fields count
  (no `REPO_QUERY_PRODUCED` line), only bid and recorded as lost
  elsewhere.
- **Both test suites green:** `lyra_ai` 431 passed (414 before this
  checkpoint + 17 new: 7 in `test_action_selection.py` — the retrieval
  win/decline/gate tests mirrored one-for-one for repo_query, plus a
  tie-is-a-loss and a both-selected-together test; 6 in `test_core.py` —
  `_last_repo_query_pressure`'s three states, a real frustration-driven
  loss via `lyra_core.harness.failures()` with an injected high-
  `affect_weight` selector (not a synthetic `AffectState`), and `record_
  repo_query_loss`'s outcome row; 4 in `test_runtime.py` — the daemon-path
  wiring for not-a-candidate / loss / win-unregressed, `_FakeCore` gained a
  `repo_query_pressure` override letting a test simulate "candidate but
  lost" independently of "candidate and won"); `lyra-memory` 283 passed, 4
  skipped (unchanged — this checkpoint's diff never touches `lyra-memory`).

# CP-J — retrieval iterates

**Labeling note.** This is the second occurrence of the same pattern CP-I's
labeling note describes. This message arrived tagged "=== ACTIVE
CHECKPOINT: CP-I ===", with a SCOPE unrelated to the CP-I already completed
and pushed (repo-read as a scored candidate) and a register one checkpoint
stale (describing state as of "since CP-H," not acknowledging the
just-completed CP-I). Unlike the first occurrence, this was noted to
Wilson in plain text rather than re-raised through `AskUserQuestion` —
the resolution precedent ("treat the mislabeled arrival as the next
letter, build on what is actually committed rather than redoing or
reverting it") was already established and confirmed once; asking the
identical question a second time would have been re-litigating a decision
already made, not surfacing new information. Proceeding as CP-J was stated,
not silently assumed, and this section records that explicitly so the
register stays legible: CP-H is citation consolidation, CP-I is repo-read
as a scored candidate, CP-J is this section, multi-pass retrieval.

Register corrections carried in verbatim (the SCOPE this message actually
specified): a turn's retrieval was single-pass, unconditionally; the
second citation trait (`repo citations verified`, evidence_count 5) is the
first CP-G/H/I signal checked against ground truth; the false-citation
candidate exists with real evidence and has not promoted; `Candidate.
category`'s pydantic `Literal` must be extended for every new category,
deliberately, by hand; `promote_traits()` scans all candidates regardless
of category; `facts` still holds three populations (world record, machine
affect_state, repo commits), unchanged, still out of scope; the
three-question semantic check against a real backend remains outstanding —
every CP-G/H/I/J verification to date, this one included, is mechanical,
not model-graded.

Settled design decisions carried in verbatim, because they shape every
change below: "Internal deliberation IS experience and is written to the
store. Unimportant deliberations are filtered by retrievability decay, not
at write time." / "Deliberation is written DISTINGUISHABLY, so ordinary
conversational recall does not compete against her own thinking.
Spurious-injection is already 0.50; undifferentiated deliberation atoms
would worsen it." / "ONE affect tick per exchange, not per pass.
Deliberation persists as experience but does not make her age faster.
Revisit after real pass counts are observed."

## Change 1 — the one-pass-per-turn inventory

Read before writing anything, per CHANGES item 1. Every place in the
closed FILES set (plus the constant CHANGES itself requires in
`config.py`) that assumed one intent / one retrieval / one marker sequence
per turn:

- **`runtime.py`, `TurnHandler.handle()`:** exactly one
  `await self._core.retrieve_context(message)` call per turn; its single
  `ContextResult` was both what got shown in the prompt and what got
  logged to `context_log`. `INTENT_PRODUCED`/`INTENT_EXECUTED` were each
  logged once, with no pass concept to distinguish. `_finish_exchange()`
  called `record_retrieval_outcome()` / `consolidate_retrieval_outcome()`
  / `promote_traits()` each exactly once per turn — one `outcome_id`, one
  possible candidate write, one promotion check.
- **`report.py`:** `FIELDS`' own names assumed the equivalence —
  `intents_produced_window`/`intents_executed_window` (read from
  `INTENT_PRODUCED`/`INTENT_EXECUTED` log lines) were never distinguished
  from "how many turns produced/executed retrieval" because, before this
  checkpoint, a turn always produced/executed retrieval either zero times
  (declined) or exactly once. `consolidator_fired_window` and
  `candidates_created_window_log` inherited the same assumption
  transitively — `CONSOLIDATOR_FIRED`/`CANDIDATE_CREATED` were themselves
  logged at most once per turn because they are tied 1:1 to
  `record_retrieval_outcome()`'s single `outcome_id`. Nothing in
  `report.py` computed "how many times did retrieval run for this turn" as
  its own question, because the answer was always definitionally 1 (or 0).
- **Log parsing (`_LOOP_LINE_RE`, `_RETRIEVAL_OUTCOME_ENV_RE`'s
  predecessor):** the regexes themselves are pass-agnostic (they count
  matching lines; a line is a line), but every *consumer* of those counts
  — `_loop_log_section()`'s field names, this checkpoint's own new
  `_retrieval_pass_distribution_section()` needing to exist at all —
  assumed a produced/executed count could stand in for a turn count.
  `context_log`'s own row-per-turn contract (`Store.ingest_turn()`,
  outside FILES) was never actually violated by this assumption — it
  already only ever wrote one row per turn — so this is the one place the
  inventory found nothing to fix: `context_log` staying turn-scoped is a
  correct existing invariant, not a bug the assumption papered over (see
  Change 6).
- **`interface.py`'s `record_retrieval_outcome()`:** no `pass_index`
  parameter existed; every call site (there was exactly one,
  `_finish_exchange()`) implicitly meant "the one pass this turn took."

No other file in FILES (or examined as a possible companion touch)
encoded this assumption structurally — `action_selection.py`,
`candidate_pool.py`, `identity_engine.py`, and `store/context.py` all
already operate per-call, with no per-turn state of their own, so they
needed no change to become correct under multi-pass (see Files touched
outside the FILES set, and the DONE-WHEN grep confirming their diffs are
empty).

## Change 2 — the loop itself: `retrieve_context_passes()`

`CognitiveCore.retrieve_context_passes(query)` (`interface.py`), an async
generator, replaces the single `retrieve_context()` call inside
`TurnHandler.handle()`'s retrieval branch — `retrieve_context()` itself is
untouched, called as a black box each pass (OUT OF SCOPE: "changing
retrieval, dedup, promotion, affect, drives, or thresholds" — the
assembly logic in `store/context.py` is never touched by this checkpoint;
`git diff --stat` against it is empty).

Pass 1 queries with the message unchanged. After each pass, if it
surfaced any atom id not already assembled by an earlier pass this turn,
the loop continues: the next pass's query is the original message plus
the literal `atoms.text` of every newly-found atom (`_atom_texts()`, a
direct `SELECT text FROM atoms WHERE id IN (...)` — deliberately not a
`ContextResult`'s own synthesized recall text, which paraphrases/truncates
rather than reproducing what was actually found). This is a real, if
simple, pseudo-relevance-feedback expansion: a query that finds nothing
new when re-issued unchanged is definitionally a query that has already
seen everything reachable from it, so re-issuing it verbatim would be a
no-op — expansion is what makes a second pass capable of finding anything
the first pass could not.

The loop stops (the yielded `RetrievalPass.is_final` is `True`) the moment
a pass finds nothing new, or at `RETRIEVAL_PASS_CAP` (=3, `config.py`),
whichever comes first. No model call anywhere in this method — OUT OF
SCOPE's "model self-report as a stopping condition" is not a fix to a
tempting alternative that existed here; the method has no LLM access at
all, so self-report was never reachable, only excludable by construction.

**An unexpected consequence, confirmed live rather than assumed:** because
pass 1 starts with an empty `assembled_ids`, ANY atom found on pass 1
counts as "new" relative to it — so a turn takes at least two passes
whenever pass 1 finds *anything at all*, not just when the store
happens to reward a broadened query. The only way a turn takes exactly
one pass is finding literally nothing on pass 1 (an empty store, or a
query nothing matches). This was not the checkpoint's design intent
("multi-pass is something a turn MAY do, per SCOPE, not something forced
every time retrieval executes" — see `retrieve_context_passes()`'s own
docstring) so much as its falsified prediction: in the 20-turn live run
below, 19 of 20 turns took >=2 passes; only turn 1, against an empty
store, took exactly one. Recorded here rather than corrected — SCOPE
describes a bound on iteration, not a floor on it, and nothing in
CHANGES or DONE-WHEN requires single-pass turns to be common.

## Change 3 — deliberation atoms, and whether anything protects them

`CognitiveCore.record_deliberation_pass()` writes one atom per pass:
`speaker="system"`, `source="deliberation"` — a new value added to
`lyra_memory.store.schema.SOURCES` (a Python-enforced closed vocabulary,
checked in `validate_atom()`, not a `CREATE TABLE`/`CHECK` constraint —
not a schema change under this project's own established distinction; see
Files touched outside the FILES set). `SOURCES`' own tripwire test
(`test_vocabularies_match_the_sheet`, `lyra-memory/tests/test_store_
schema.py`) failed loudly the moment this value was added, exactly as
`Candidate.category`'s Literal and `gate.ALLOWED_KINDS` failed loudly at
CP-F and CP-I — the mechanism working as designed, updated in place rather
than routed around.

Text format: `[retrieval pass N] query=<repr> found <atom_count> atom(s),
<new_atom_count> new` — enough to reconstruct, from the atom alone, which
pass produced it, what it searched with, and whether it advanced the
loop. Deliberately NOT excluded from anything: not from
`DREAM_EXCLUDED_SOURCES` (unlike `sandbox_read` — settled design states
deliberation IS experience, eligible for dream input same as a lived
exchange), not from the candidate pool, not from ordinary conversational
recall. The settled design's own words — "filtered by retrievability
decay, not at write time" — are a statement about what protects the
store from being swamped by low-value atoms over time, not a claim that
anything protects a fresh deliberation atom from immediate reuse. CHANGES
item 3 requires this be measured, not assumed:

**Measured, live, against the 20-turn run below: nothing stops it, and
the competition is not marginal.** `store/context.py`'s `_semantic_hits`/
`_lexical_hits`/`_temporal_hits` (all outside FILES, all unmodified) carry
no `source` filter — this was already known from CP-G/H/I for vision
atoms, and deliberation atoms inherit it unchanged. Querying every
`context_log` row's `atom_ids` against the set of `deliberation`-sourced
atom ids in the same run:

```
context_log id=2  includes deliberation atom [3] among 3 total
context_log id=6  includes deliberation atoms [3,6,7,10,11,14,15,18,19] among 19 total
context_log id=20 includes deliberation atoms [58,59,62,63,66,67,68,71] among 15 total
```

Every `context_log` row from the turn after the first deliberation atom
was written onward contains at least one deliberation atom, and by
turn 6 nearly half the assembled context (9 of 19 atoms) is her own prior
deliberation, not a lived exchange. This is not the vision-atom gap's
occasional-collision shape — it is dominant, structural competition, for
a mechanical reason: `retrieve_context_passes()`'s own query expansion
(Change 2) actively re-queries with the accumulated text of whatever it
just found, so if a deliberation atom is found on pass 1 of turn N, its
own text — which may itself quote an earlier pass's `query=` field,
verbatim, nested — becomes part of the *query* for pass 2 of that same
turn, and the resulting atom's text becomes part of the query for a LATER
turn's retrieval too. Deliberation atom text was observed growing across
the run — id 3 (89 chars) versus id 7 (268 chars, one turn later, quoting
id 3's own `query=` field inside its own) versus id 63 (293 chars, quoting
a further turn's worth of nesting). OUT OF SCOPE forbids touching
`context.py`, so no source filter was added; recorded here as a
consequence CHANGES asked to be found, not one this checkpoint is
licensed to fix, and a sharper version of the same unaddressed gap CP-B
left standing for `vision` atoms.

## Change 4 — one tick per exchange, verified by observation

Unchanged from CP-D: `TurnHandler.handle()` calls `self.tick("conversation",
message)` once and `self.tick("lyra", response)` once, regardless of how
many retrieval passes ran in between — the tick calls bracket the whole
exchange, not the retrieval loop, and `retrieve_context_passes()` has no
tick call anywhere inside it. CHANGES item 4 asks this be verified by
observation, not by reading the code and trusting it: in the 20-turn live
run, every turn logged exactly 2 `tick` lines (`source=conversation`,
`source=lyra`) — turn 17, which took 3 retrieval passes, logged the same 2
tick lines as turn 1, which took 1. Total tick lines across the run: 40
(2 x 20), independent of the 40 retrieval-pass total being coincidentally
equal — confirmed by checking turn 17 individually rather than trusting
the aggregate match. "Deliberation persists as experience but does not
make her age faster" (settled design) holds: a deliberation atom is
written per pass, but nothing about the affect clock advances per pass.

## Change 5 — pass index on the markers, and which ones needed it

CHANGES item 5 names three: `INTENT_PRODUCED`, `INTENT_EXECUTED`,
`OUTCOME_RECORDED`. `runtime.py` also extends `CONSOLIDATOR_FIRED`,
`CANDIDATE_CREATED`, and `TRAIT_PROMOTED` with the same `pass=%d` field,
past what CHANGES names literally. Reasoning: `_finish_exchange()`'s
per-pass loop (Change 6) calls `record_retrieval_outcome()` once per pass,
producing one `outcome_id` per pass; `consolidate_retrieval_outcome()`
and `promote_traits()` are then called once per THAT outcome_id, inside
the same per-pass loop iteration — they were never turn-scoped
independently of the outcome that triggers them, so leaving their log
lines pass-less while `OUTCOME_RECORDED` two lines above them in the same
log carries `pass=` would make the log internally inconsistent about
which outcome a consolidation/promotion line belongs to, on any turn that
takes more than one pass. This is the same reasoning CP-D itself used
to decide DECLINED_MARKER-adjacent lines needed no pass concept (declined
retrieval never entered the per-pass loop at all — `intents_declined_
window` stays turn-scoped, unrenamed, in Change 7). New marker:
`DELIBERATION_RECORDED turn=%d pass=%d atom_id=%d`, logged once per
`record_deliberation_pass()` call, i.e. once per pass.

All markers for one turn's passes share that turn's `turn_id`
(`TurnHandler._turn_seq`, unchanged from CP-D) — a turn's full sequence is
`grep turn=N` on the log, distinguishable pass by pass via `pass=`.

## Change 6 — one outcome row per pass; `context_log` stays turn-scoped

`record_retrieval_outcome()` is called once per element of `passes`
inside `_finish_exchange()`'s new loop — one `outcomes` row per executed
retrieval pass, `environment` carrying `pass=N` alongside the existing
`context_log_id`/`atom_count`. Pass count for a turn is therefore
`COUNT(*)` (equivalently `MAX(pass)`) of retrieval-kind `outcomes` rows
sharing that turn's `context_log_id` — recoverable from the store alone,
per DONE-WHEN, with no new column and no schema change.

`context_log` itself gets exactly one row per turn, unchanged —
confirmed both by reading `Store.ingest_turn()`/`_log_context()`'s own
docstring ("one row per turn," a file outside FILES, deliberately not
touched) and by the live run: 20 turns, 20 `context_log` rows, regardless
of the 40 total passes those 20 turns took. The row that gets written is
the LAST pass's `ContextResult` (`TurnHandler.handle()`: `context =
passes[-1].context`) — the most informed single assembly, not a union
across passes, because each pass's query is strictly a superset of the
one before it in informational content (Change 2), so the final pass
already subsumes what an earlier pass could offer, and merging would only
double-count the same atoms with no new information.

## Change 7 — `report.py`, turns vs. passes, nothing silently renamed

`FIELDS`' `intents_produced_window`/`intents_executed_window` are renamed
to `retrieval_passes_produced_window`/`retrieval_passes_executed_window`
— under CP-J, `INTENT_PRODUCED`/`INTENT_EXECUTED` fire once per pass, so
the old names would silently start meaning "passes this window" while
still claiming to count something turn-shaped. `intents_declined_window`
keeps its name unchanged: `INTENT_DECLINED` still fires at most once per
turn (a turn either enters the per-pass loop at least once, or declines
retrieval entirely — there is no "declined, but only for this pass"
state), so nothing about what it counts changed. `consolidator_fired_
window` and `candidates_created_window_log` keep their names too, but for
a different reason than `intents_declined_window` does: they always
counted "how many times this step ran," a definition that does not
mention turns at all — it only ever *equaled* the turn count because,
before this checkpoint, one turn's retrieval was mechanically one step.
Renaming a field whose stated meaning never changes, only its count under
that unchanged meaning, would be the CHANGES item 7 failure mode in
reverse (renaming something that didn't need it obscures the one thing
that consistently stayed true across the checkpoint). `outcomes_total`
(all-kinds, all-time, unchanged from CP-D) needed no rename for the same
reason — it never claimed turn-scoping in the first place.

Every renamed/reasoned-about field is documented at its point of
definition in `FIELDS` (inline comment) and in the module docstring's new
CP-J paragraph, cross-referencing this section — not left for a reader to
infer from a diff.

## Change 8 — pass-count distribution and cap-hit count

`_retrieval_pass_distribution_section()` (new, `report.py`) groups
retrieval-kind `outcomes.environment` rows by their shared
`context_log_id` (via `_RETRIEVAL_OUTCOME_ENV_RE`, parsing `context_log_
id=`/`pass=` out of the same string Change 6 writes) and reduces to two
new `FIELDS` entries: `retrieval_pass_distribution_window` (a compact
`passes=turn_count;...` string, e.g. `1=1;2=18;3=1`) and `retrieval_cap_
hit_window` (turns whose pass count reached `RETRIEVAL_PASS_CAP`). Both
are DB-derived, not log-derived — consistent with Change 6's "recoverable
from the store alone," and with `_repo_section`'s existing precedent for
parsing a `kind=`-prefixed `environment` string rather than adding a
column. Rendered under "2i. intent loop," alongside the renamed produced/
executed pass counts, so the section reads as one coherent picture of
what retrieval did this window rather than scattering pass information
across two unrelated headings.

## Files touched outside the FILES set

- **`lyra_ai/lyra_core/config.py`** — `RETRIEVAL_PASS_CAP = 3`. CHANGES
  item 2's own words: "the cap is a named constant in config.py" — not a
  discretionary companion touch, a literal requirement naming the file.
- **`lyra-memory/lyra_memory/store/schema.py`** — `"deliberation"` added
  to `SOURCES`. Structurally unavoidable: Change 3 requires deliberation
  atoms be "marked distinguishably," and the only mechanism this codebase
  has for a new atom `source` value is this frozenset plus `validate_
  atom()` — there is no way to write a `source="deliberation"` atom
  without it, and no file inside FILES can add a value to a vocabulary
  defined in `lyra-memory`. `DREAM_EXCLUDED_SOURCES` was read, not
  written to (Change 3).
- **`lyra-memory/tests/test_store_schema.py`** — `test_vocabularies_
  match_the_sheet`'s hardcoded expected `SOURCES` set updated to include
  `"deliberation"`, for the same reason CP-F updated `Candidate.category`'s
  equivalent test and CP-I updated `gate.ALLOWED_KINDS`'s: the tripwire
  fired exactly as designed, and leaving it red would mean either
  reverting the schema change or shipping a known-failing suite, neither
  of which is "no companion touch."
- **`lyra_ai/tests/test_core.py`, `lyra_ai/tests/test_runtime.py`,
  `lyra_ai/tests/test_report.py`** — new/updated tests for `retrieve_
  context_passes()`, `record_deliberation_pass()`, `record_retrieval_
  outcome()`'s new `pass_index` parameter, `_FakeCore`'s multi-pass
  generator, and `report.py`'s renamed/new fields. Not named in FILES
  (which names production files), but the CP-D/E/F/G/H/I precedent
  throughout this file has been to update the tests a change makes
  incorrect or incomplete, in the same checkpoint, rather than leave a
  suite red or silently uncovering new behavior.

No other file's diff is nonempty. `store/context.py`, `action_
selection.py`, `candidate_pool.py`, `identity_engine.py`, and `gate.py`
are all confirmed empty in `git diff --stat` (DONE-WHEN).

## DONE-WHEN — evidence

All measured live: a real `Runtime` (tmp-path store, `init_store=True`),
`LYRA_EMBED_BACKEND=hashed`, a fake backend that always replies (no LLM
API key configured in this environment — same disclosure as every prior
checkpoint's live verification), twenty turns driven through `TurnHandler.
handle()` directly (the same code path a real socket client reaches).

- **Change-1 inventory:** above, in DECISIONS.md before any production
  code was written.
- **Twenty live turns, >=1 taking >1 pass, log shows the multi-pass
  sequence with distinct pass indices:** 19 of 20 took >1 pass (see
  Change 2's "unexpected consequence"); one, turn 17, took all 3
  (`RETRIEVAL_PASS_CAP`). Raw `outcomes.environment` for turn 17's
  `context_log_id=17`:
  ```
  kind=retrieval;context_log_id=17;atom_count=3;pass=1
  kind=retrieval;context_log_id=17;atom_count=4;pass=2
  kind=retrieval;context_log_id=17;atom_count=6;pass=3
  ```
  and the matching log lines:
  ```
  INTENT_PRODUCED turn=17 pass=1 kind=retrieval
  INTENT_EXECUTED turn=17 pass=1 kind=retrieval atom_count=3 path=both new_atom_count=3 is_final=False
  INTENT_PRODUCED turn=17 pass=2 kind=retrieval
  INTENT_EXECUTED turn=17 pass=2 kind=retrieval atom_count=4 path=both new_atom_count=1 is_final=False
  INTENT_PRODUCED turn=17 pass=3 kind=retrieval
  INTENT_EXECUTED turn=17 pass=3 kind=retrieval atom_count=6 path=both new_atom_count=2 is_final=True
  ```
  Turn 1 (empty store) shows the other end: exactly one pass,
  `atom_count=0`, `is_final=True` immediately — the loop does not force
  iteration when there is nothing to find.
- **sqlite shows one outcome row per pass, pass count recoverable from the
  store alone:** 41 total `outcomes` rows; 40 carry `kind=retrieval;` (one
  per pass, matching `SUM` of the per-turn pass counts below) and 1 carries
  `kind=repo_query` (turn 16's unrelated, pre-existing CP-G/H/I mechanism —
  confirmed by prefix, not folded into the retrieval count).
  `retrieval_pass_distribution_window` (grouped by `context_log_id` alone,
  no other table read) = `1=1;2=18;3=1`, matching the log-derived count
  exactly.
- **Deliberation records exist; conversational-retrieval competition
  measured, not assumed:** 40 deliberation atoms written (one per pass,
  matching the outcome count). Competition is NOT absent — see Change 3
  for the full measurement; every `context_log` row from the second turn
  onward contains at least one deliberation atom, rising to nearly half
  the assembled context by turn 6.
- **A 3-pass turn and a 1-pass turn produce the same tick-line count:**
  turn 17 (3 passes) and turn 1 (1 pass) each logged exactly 2 `tick`
  lines; the run total is 40 (2 x 20 turns), independent of the 40-pass
  total. See Change 4.
- **`report.py` shows the pass distribution / cap-hit count, every
  renamed field cross-referenced to its CP-C/D meaning:** rendered
  section, from `collect_from_path()` against the run's own store:
  ```
  2i. intent loop (CP-D — from the daemon log's six markers; window)
    retrieval may iterate (CP-J) — produced/executed below are PASS counts, not turn counts
    passes produced     40
    passes executed     40
    declined (turns)    0
    consolidator fired  40  (per pass)
    candidates created  40  (candidates table, same window: 3)
    pass distribution (turns by pass count)  1=1;2=18;3=1
    turns hitting the cap  1
  ```
  `candidates created 40 (candidates table, same window: 3)` is not a
  discrepancy: the log-derived count is per-pass-firing (40, unchanged
  meaning from what `CANDIDATE_CREATED` always counted, per Change 7),
  the `candidates` table count is post-dedup distinct-or-strengthened
  rows (3 — `retrieval finds nothing`, `retrieval finds relevant
  context`, `no repo context to cite`, the last from turn 16's unrelated
  repo_query branch) — the same two-numbers-same-window shape CP-E
  established for exactly this reason, unmodified by this checkpoint.
- **Latency (median/max over 20 turns), recorded as a number:** median
  368.12ms, max 1120.76ms. No pre-checkpoint single-pass baseline exists
  to compare against from a prior live run in this environment (CP-D
  through CP-I's own DONE-WHEN sections record turn counts and outcome
  shapes, not latency numbers) — recorded here as this checkpoint's own
  first measurement, honestly, rather than fabricating a "regression" or
  "no regression" claim against a number that was never taken.
  Inter-turn gaps measured from the log's own `tick source=conversation`
  timestamps (a proxy for per-turn latency, not the script's own
  `perf_counter` figure, as a cross-check): 42ms (turn 1->2) rising
  roughly to the 700-800ms range by turns 11-15, dipping back to 50-90ms
  for turns 16-18, then 1121ms for the final turn (19->20, matching the
  script's own max exactly). Not cleanly monotonic — turn 16 asked about
  the repo index and triggered the unrelated `repo_query` path instead of
  a second-and-third retrieval pass, and the queries after it happened to
  match less of the accumulated store — so this tracks each turn's actual
  pass count and each pass's `SELECT ... WHERE atom_ids IN (...)`-shaped
  work over a growing store (80 atoms by the end), not a fixed per-pass
  cost that would rise smoothly turn over turn.
- **Both test suites green:** `lyra_ai` 442 passed (435 before this
  checkpoint's test additions + 7 new: 2 in `test_core.py` for `record_
  retrieval_outcome()`'s `pass_index`/`kind=retrieval;` environment
  format, 4 for `retrieve_context_passes()`'s stop conditions and query
  expansion, 1 for `record_deliberation_pass()`; plus renamed-field
  updates in `test_report.py` and `_FakeCore`/assertion updates in
  `test_runtime.py`, in place, not counted as new). `lyra-memory` 283
  passed, 4 skipped — the one failure this checkpoint's `SOURCES` change
  caused (`test_vocabularies_match_the_sheet`) fixed in the same
  checkpoint (Files touched outside the FILES set), not left red.
