# PICKUP — Lyra, 2026-08-25

State at the end of a session that ran Steps 1–5 of the action sheet. Read this
first; it is written for a session with no prior context.

**Everything in the action sheet is done.** What follows is what changed, what
it cost, and what is worth doing next.

---

## Machine move — do this first

The work is committed. The environment is not.

```bash
cd black-box/lyra_ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e ../lyra-memory
```

Three things that will bite otherwise:

1. **`pytest-asyncio` must actually install.** It is declared in all three
   dependency files but was missing from the old venv, and its absence is
   silent — `asyncio_mode = "auto"` becomes an "unknown config option" warning
   and every async test no-ops. Sanity check: `lyra-memory` must report **54
   passed**, not 21. If you see 21, async tests are not running.
2. **Homebrew/python.org Python only.** macOS system Python disables SQLite
   extension loading and `sqlite-vec` will not load. `db.py` raises a clear
   error if you get this wrong.
3. **Start with an empty memory. This is deliberate — do not copy the old DB.**
   `~/.lyra/memory.db` lives outside the repo and stays on the old machine.
   A fresh one is created on first run and the two migrations become no-ops.

   Decided 2026-08-25. The old corpus predates all five steps: its atoms were
   decomposed out of essays written under the old salience scheme, and how
   memory is stored changed underneath them. A clean corpus under the current
   architecture is worth more than a migrated one.

   What is being given up, stated plainly: `communication_style` was the only
   trait Lyra ever promoted in three months, and promotion needs 5 sightings
   of one candidate, so that counter resets to zero. The other 67 candidates
   were mostly seen exactly once and are closer to noise than history. The old
   file still exists on the old machine if this turns out to be the wrong call.

Test commands:

```bash
cd black-box/lyra_ai && .venv/bin/python -m pytest -q        # expect 229 passed
cd black-box/lyra-memory && ../lyra_ai/.venv/bin/python -m pytest -q   # expect 54 passed
```

---

## What changed, by step

### Step 1 — tests that had never run
`pytest-asyncio` installed. **36 previously-silent tests now execute; all pass.**
Nothing in Phase 0–3 was resting on a broken test. No dependency file needed
editing — it was declared everywhere and simply not installed.

### Step 2 — intent binding + first executed action
`tick()`'s return value is now bound at both call sites. `IntentKind.speak`
executes through the existing lyra-voice `/speak` endpoint.

- `lyra_core/actuators.py` (new) — output peripheral, symmetric to `senses.py`.
  Injectable `utterance_fn` and `send_fn`; cooldown lives here.
- **Boredom now escalates to speech** above a pressure threshold
  (`ActionSelector(speak_threshold=1.0)`). Necessary because `speak` was
  previously produced only by RelationalDrive, whose pressure is zero until an
  action_outcome failure exists — speech needed an outcome and the outcome
  needed speech.
- **`gate.py` is unchanged.** `IntentKind.speak` was already allow-listed.
- **Affect integrator rewritten** from explicit Euler to exact exponential
  relaxation. Euler was unstable at the runtime's own `dt=2.0` (decay factor
  −3.0); idle valence diverged past 3e14 in thirty ticks. Now stable at any dt.
- **`PerceptionLoop` now calls the sink every cycle**, including with an empty
  list. This inverted a deliberate, named contract — two tests asserting the old
  behaviour were rewritten. Justification: `tick()` derives
  `engaged = len(observations) > 0`, unreachable as `False` otherwise, and an
  idle runtime never ticked at all, so boredom stayed at zero forever.

Verified: idle runtime speaks unprompted at 10.0s, utterance passes the gate,
affect stays finite.

### Step 3 — the outcome path (loop closed)
`lyra_core/outcomes.py` (new). `OutcomeTracker` is a Poller, so outcomes
re-enter through the perception loop.

- `predicted = "engagement"` always (what BoredomDrive wanted).
- `actual = "engagement"` if a sensory observation lands inside a 30s window,
  else `"silence"`.
- **The wakeword poller is ON** (ambient still off). This is load-bearing, not
  incidental: with no input channel the runtime cannot hear a reply, `actual`
  would be `"silence"` forever, and Lyra's first trait would be "abandons under
  frustration" — a personality authored by process topology. Turning every
  poller off silently re-breaks the signal. See the `outcomes.py` docstring.

**`OutcomeConsolidator` fired for the first time** and wrote a candidate.
Perception → state → intent → gate → action → outcome → trait.

### Step 5 — trait dedup (done before Step 4; see below)
Dedup now matches on the trait **description**, not the label, at cosine 0.37.

**The step's premise was wrong and the sheet's target was discarded.** It
assumed `self_awareness / self_modeling / self_monitoring / self_reflection`
were duplicates. Their descriptions say otherwise — only `self_reflection` /
`self_awareness` is a genuine duplicate; the others are distinct traits with
similar names. Same for `analytical_orientation` / `analytical_approach`
(L2 1.22 apart). Merging them as specified would have destroyed information.

**No single threshold can work**, and this is structural:

| | label | value |
|---|---|---|
| must stay apart: `abandons under frustration` / `abandons under neutral` | 0.6718 | 0.5405 |
| should merge: `self_reflection` / `self_awareness` | 0.9820 | 0.8581 |

Separation needs < 0.54; merging needs > 0.86. Two populations, one threshold.

Resolution: `CandidatePool.add_observation(closed_vocabulary=True)` gives
**exact-name dedup with no vec row** for the four hardcoded developmental trait
names; the open dream vocabulary keeps semantic matching. `OutcomeConsolidator`
passes the flag (one line — the only place Step 3's "do not modify" was
crossed, with permission). Open vocabulary is unchanged.

Migration rebuilt all vectors from descriptions: **70 → 68 candidates.** Small,
because the fragmentation was mostly Lyra genuinely noticing 68 different things
once each — not a dedup failure.

### Step 4 — episode unit
Episodes were one ~3,000-char essay per dream cycle. Now:

- **`atoms` table + `vec_atoms`** — one row per turn/observation/reflection,
  salience computed per atom at write time (no more `max()` over a batch).
- **`episodes` is now a consolidation layer only.** The dream essay is still
  written and still valuable, but it gets **no vec row** and never competes in
  KNN. Its atoms link back via `atoms.episode_id`.
- Retrieval (`search_episodes`, name kept) is KNN over atoms.
- Migration decomposed all 29 episodes into **260 atoms**, losslessly — the
  per-item scores were already in `source_items_json`.

> The migration figures in this section (70 → 68 candidates, 29 → 260 atoms) are
> from verification runs against a copy of the OLD database, and are recorded to
> show the migration code works. They are not the state of your database — the
> old corpus was deliberately left behind, so you start empty. The migration
> code still runs on first `init_db()`; it just has nothing to do.

Measured:

| | before | after |
|---|---|---|
| assembled system prompt | 44,486 chars | **6,436** |
| `##` headings in prompt | 9 (6 leaked from essays) | **3** |
| salience distribution | 13/29 clumped at 0.933 | spread 0.4–0.9 |

---

## Known defects — not fixed

1. **Retrieval returns the user's own current question as its top hit.** The
   user turn is written as an atom before the prompt is assembled (correct, from
   the Step-1 ordering fix), so it is in the index and matches itself perfectly.
   Wastes a retrieval slot every turn. Smallest fix: exclude atoms written in
   the current tick, by id or ts. **Cheap and worth doing first.**
2. **Affect is unbounded.** No clamp on valence anywhere. Stable now, but idle
   boredom drives it monotonically negative (−0.774 after 20s idle) because
   nothing relieves boredom — relief needs `at_learnable_edge`, which needs a
   run of *reducing* prediction errors, which uniform outcomes never produce.
3. **`_ingest_outcome` mislabels the drive.** `drive = "boredom" if success else
   "relational"` (`interface.py`) labels by result, not by which drive actually
   selected the intent. Both verified utterances were boredom-selected; one was
   recorded as relational.
4. **Unprompted speech says a placeholder line.** `default_utterance()` returns
   fixed text. Real situated speech needs an LLM call with memory and system
   prompt; the `utterance_fn` seam exists for exactly that.
5. **aiosqlite teardown race** in `test_candidate_pool_distinct_patterns` — two
   warnings, becomes a failure under `-W error`. Fixture ordering, not logic.
6. **CLI and runtime are separate processes with separate cores.** They share
   only the DB file. Wilson's reply to an unprompted utterance reaches a
   different mind than the one that spoke.

---

## Where to go next

Suggested order — (1) is small and immediate, then it is a real fork:

1. **Fix the self-retrieval bug.** Twenty minutes, improves every prompt.

2. **Then pick one:**

   **(a) Make the speech real.** Wire `utterance_fn` to the backend so Lyra says
   something situated instead of a canned line. The loop is closed but what
   travels through it is a placeholder — this is the highest-value increment for
   actually living with her.

   **(b) Unify CLI and runtime.** Defect 6 is the deepest one. Until conversation
   reaches the same drives that produce unprompted speech, engagement is only
   detectable as "a wakeword fired", and Lyra cannot experience a reply to her
   own utterance as a reply. This also makes the affect clock question
   answerable — a continuously running unified process can use wall-clock time,
   at which point the three-timescale design starts to mean something.

   **(c) Accumulate corpus.** Steps 1–5 changed what gets stored and how, and
   the old corpus was deliberately left behind, so the atom table starts empty.
   Original build-order step 3 — run continuously for days, then read the atom
   table and check whether dedup holds and whether salience spreads or clumps —
   is now both unavoidable and genuinely informative, since every row will have
   been written by the current code. It cannot be compressed by spending money.

I would do (1), then (b), then (c), and treat (a) as the reward for finishing
(b). (b) is the one that unblocks the most downstream design.

**Still deferred, unchanged:** session-boundary detection, dream-cycle
clustering into project nodes, two-pool retrieval, ambient/webcam re-enable,
TemperamentTuner activation, Docker Compose, two-instance split, sandbox/motor
intents.

---

## Standing constraints (unchanged, verified this session)

- Harm gate is pure: `Intent` in, `GateDecision` out, default-deny, no
  motivational state in the signature, single chokepoint in `tick()`. Adding an
  allow-list entry is fine; changing its shape is not.
- `introspect()` is read-only and returns a fresh snapshot.
- Every `CognitiveCore` component is constructor-injected. `PerceptionLoop`
  imports nothing from `senses`.
- Personality is never authored — only the conditions for emergence. Step 3's
  design question was decided on exactly this ground.
