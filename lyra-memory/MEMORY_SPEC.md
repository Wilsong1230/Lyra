# Lyra — Memory Spec

v0.1 · 2026-08-26 · supersedes the single-table `episodes` design

---

## Invariant

**Hot path appends. Cold passes enrich. Nothing is ever replaced.**

Turns are the substrate and are permanent. Everything structural — sessions, salience, entities, outcomes, dreams — is derived, nullable, and re-derivable. A cold pass crashing leaves the store correct.

---

## Schema

### Hot (written on the reply path)

```sql
atoms
  id           INTEGER PK
  ts           REAL NOT NULL
  speaker      TEXT NOT NULL        -- wilson | lyra | system
  source       TEXT NOT NULL        -- cli | wakeword | ambient | vision
  text         TEXT NOT NULL
  session_id   INTEGER NULL  FK sessions(id)      -- cold
  salience     REAL    NULL                       -- cold
  outcome_id   INTEGER NULL  FK outcomes(id)      -- cold

vec_atoms      -- sqlite-vec
  atom_id, embedding(384)           -- all-MiniLM-L6-v2

atoms_fts      -- FTS5 external content on atoms.text
```

Three inserts, one transaction. Budget: <50ms.

### Cold (written by batch passes)

```sql
sessions
  id, started_ts, ended_ts, atom_count
  gap_since_prev  REAL NULL         -- seconds since previous session ended

commitments                         -- open loops she owns
  id, created_ts
  source_atom_id INTEGER FK atoms(id)
  text          TEXT NOT NULL       -- free-form, as stated
  owner         TEXT NOT NULL       -- lyra | wilson
  due_ts        REAL NULL           -- only if explicit
  status        TEXT NOT NULL       -- open | done | dropped | superseded
  closed_ts     REAL NULL
  closed_atom_id INTEGER NULL FK atoms(id)

dreams
  id, ts, text, embedding(384), session_id NULL

dream_atoms
  dream_id, atom_id                 -- provenance; makes dreams re-derivable

entities
  id, name, kind, first_seen, last_seen
                                    -- kind: project | person | repo | file | topic

atom_entities
  atom_id, entity_id, confidence

outcomes
  id, intent_atom_id, predicted, actual, valence, ts

context_log                         -- one row per turn, hot, append-only
  id, ts, session_id
  atom_ids      TEXT                -- json array, what was injected
  fact_ids      TEXT
  dream_ids     TEXT
  budget_used   INTEGER             -- tokens
  misses        TEXT NULL           -- json: queries that returned nothing above threshold

facts                               -- exact, permanent, correctable
  id, ts
  subject       TEXT NOT NULL       -- normalized key; joins entities(name) where possible
  text          TEXT NOT NULL       -- free-form. no predicate vocabulary.
  source_atom_id INTEGER FK atoms(id)
  source_kind   TEXT NOT NULL       -- stated | document | inferred | observed
  confidence    REAL
  valid_from    REAL NOT NULL
  valid_until   REAL NULL           -- NULL = current. stamped only by supersession.
  superseded_by INTEGER NULL FK facts(id)
  conflict_with INTEGER NULL FK facts(id)   -- suspected, unresolved

trait_history                       -- append-only, never updated
  id, ts, trait_id, trait_label
  event         TEXT    -- promoted | confidence_change | tier_change | decayed | retired
  conf_before   REAL NULL
  conf_after    REAL NULL
  tier_before   TEXT NULL
  tier_after    TEXT NULL
  evidence_count INTEGER
  dream_id      INTEGER NULL  FK dreams(id)    -- what triggered it
```

`traits` / `candidates` unchanged this pass. Separate fix (label-embedding dedup bug).

---

## Hot path

1. embed incoming text (MiniLM, ~10ms)
2. retrieval → assemble prompt
3. LLM call
4. append atom (user turn + Lyra turn), vec, fts

No enrichment. No dream. No consolidation.

---

## Cold passes

Run when idle. Slow/cheap model acceptable. Each independently testable against a frozen `atoms` table.

| pass | reads | writes | status |
|---|---|---|---|
| segmentation | atoms.ts, embeddings | sessions, atoms.session_id | scoped for Fable, pure component |
| salience scoring | atoms, outcomes | atoms.salience | revisable — re-run on new outcomes |
| dream | atoms in session | dreams, dream_atoms | shrunk; pointer-heavy |
| entity extraction | atoms.text | entities, atom_entities | not started |
| outcome consolidation | outcomes, atoms | traits, candidates | exists; link currently dropped |
| clustering | atom_entities, dreams | entities(kind=project) | deferred until segmentation verified |

---

## Retrieval assembly

Context budget is **bytes, not k**. Fixed allocation per block, hard truncation.

### Order

Position is load-bearing — models weight the start and end of context most. Pinned order, not incidental:

| pos | block | source | budget |
|---|---|---|---|
| 1 | facts | LAYER1_FACTS + subject-matched `facts` | ~200 tok |
| 2 | traits | traits, conf ≥ 0.3 | ~100 tok |
| 3 | commitments | open, if any | ~100 tok |
| 4 | recall | synthesized, see below | ~400 tok |
| 5 | recent | working memory deque, 8–10 turns verbatim | remainder |

Stable identity at the top, live conversation at the end, retrieved material in the middle where it informs without dominating.

Total non-conversation context: **<1500 tok**.

### Three paths, one merge

- **semantic** — vec KNN, similarity floor not rank cutoff
- **lexical** — FTS5/BM25, catches repo names, filenames, proper nouns
- **temporal** — plain SQL recency, separate pool, does not compete in KNN

Dedupe on assembly (MMR or cosine threshold against already-selected).

### Speaker weighting

Her own past turns are down-weighted in recall, or excluded unless the question is about what she said.

Retrieving her own phrasing and re-saying it is a self-reinforcing style loop — the model reads its own output as evidence of how it talks. `speaker` already distinguishes them; retrieval must actually use it.

### Synthesis, not concatenation

Five retrieved fragments pasted in read as disjointed notes. **One synthesized paragraph reads as memory.**

Recall block is a short synthesis pass over the merged results, not the raw hits. Costs one extra call on the hot path — acceptable, and it changes how she reads more than any schema decision here.

Falls back to concatenation if the synthesis call fails. Never blocks a turn.

### Logging

Every turn writes `context_log`: what was injected, budget used, and **misses** — queries that returned nothing above threshold.

Without this, an odd response can't be reconstructed against what she was actually looking at. Misses are equally informative: they show where the store is thin.

Hot, append-only, cheap. Ships with this step.

---

## Commitments

The line between having memory and being useful. Currently absent — "I'll look at that tomorrow" is an atom, retrievable by luck.

### Extraction

Cold, during dream. Same pass as facts, different output.

Dream reads a session and asks: did anyone state an intention to do something later? Writes a commitment row pointing at the source atom.

`owner` distinguishes hers from Wilson's. Both matter — she should be able to say "you said you'd migrate the DB."

### Closure

Also cold. Dream checks open commitments against the session's atoms and outcomes. Match → `status=done`, stamp `closed_atom_id`.

Never auto-closed on time alone. A due date passing means overdue, not done.

`dropped` requires evidence of abandonment — explicit statement, or supersession by a conflicting commitment. Silent aging into `dropped` would let her quietly forget things she said she'd do, which is the failure this table exists to prevent.

### Retrieval

Not KNN. Direct query: open commitments, ordered by due then age.

Injected when the count is nonzero. Small budget — a few lines. Truncate to the oldest and the soonest-due if there are many.

This is the one store that surfaces *unprompted*. Everything else is retrieved on relevance; open loops assert themselves.

### Affect coupling

Open commitments are a legitimate BoredomDrive input — an unclosed loop is a reason to act. Deferred until the table has real data.

Overdue count is not a valence source. Making her feel bad about a backlog is a design choice, not an emergent one, and it's the wrong one.

---

## Duration

She has timestamps and no sense of elapsed time. Nothing represents "we haven't talked in three days." RelationalDrive feels the absence via its recurrence clock; memory doesn't record that it happened.

**Fix:** `sessions.gap_since_prev`, written by segmentation at the same time the session row is created. Cost is one subtraction.

**What it enables:**

- gap is available as dream input — a long absence is a legitimate thing to reflect on
- session recall can carry it: "that was after a long break"
- distinguishes a continuous month from a month with one conversation in it, which the atom count alone cannot

**Not injected directly.** Time since last session belongs in the prompt as ambient context, not retrieved memory. The stored gap is for reflection and for reconstructing the shape of her history.

---

## Discovery vs. import

**Import** = rows written into her store by Wilson. No atom produced them, no experience preceded them. She knows things she never learned. **Not permitted.**

**Discovery** = she reads something. The reading is an event, becomes an atom, facts extract normally in dream with `source_atom_id` pointing at the moment she read it.

The line is not where information came from. It's whether she did the reading.

Bootstrapping is therefore fine and is not a principle violation. Hand her a folder; let her read it. Same mechanism as conversation — input, atom, dream, facts. Every row keeps provenance back to something she did, and she can say how she knows.

**Containment:** the atom is not the file contents. File goes to `runs`; atom is the event ("read wilson's resume"); facts point at both. Otherwise a 3,000-word document becomes an atom and the essay problem returns.

### source_kind

Discovery-by-reading is the same channel as the sandbox injection risk. A document can assert anything, and uncritical extraction means she believes whatever a file says.

| kind | meaning | confidence |
|---|---|---|
| `stated` | Wilson said it in conversation | high |
| `observed` | she saw the result herself (test output, sandbox, sim) | high |
| `document` | read from a file she did not author | lower, and attributable |
| `inferred` | she concluded it | lowest, decays if unsupported |

She should be able to say which. "You told me" vs. "I read it in your resume" are different epistemic states and the store should hold the difference.

**Excluded from dream input entirely:** `source=sandbox_read`. A file on disk must not be able to write to her identity. Discovery can produce facts; it cannot produce traits.

---

## Perception

**Rule:** ambient and wakeword are telemetry, not atoms. Vision is atoms. A sound classification is a measurement; an image she looked at is an event.

**Vision atoms are her description of a frame, not the frame.** Irreversible compression — she can never look again. Frame goes in `runs`, atom points at it, so the original survives even though her account of it is what gets retrieved.

**Ambient containment:** category change only. First appearance of a new sound class, or something salient. Not a row per classification.

Moot while ambient and webcam are disabled. The constraint exists so they are not re-enabled pointed straight at the substrate.

---

## Per-person scope

`atoms.speaker` exists and is unused. Populate it from step 1 even though nothing reads it.

Fine while it's only Wilson. The day anyone else talks to her, an unscoped store means all of it lands in one undifferentiated pile with no way to separate it retroactively.

Deferred: per-person retrieval scoping, per-person relational state, whether facts about person A should surface when talking to person B. All cheap with the column, impossible without it.

---

## Facts vs. experience

Two stores, different physics. The split is what makes forgetting safe.

| | facts | atoms |
|---|---|---|
| retrieval | exact match on subject | KNN / BM25 / recency |
| decay | never | yes |
| correction | supersession | none needed |
| volume | low | high |

**World knowledge (grass is green) is not stored at all** — it's model weights. Not at risk. What's at risk is *facts about Wilson's world*: repo names, school, which project is live. Those are stored and would decay under an undifferentiated policy.

Routing facts out of the decay pool is upstream of the decay function. No exemption logic needed — they aren't in the pool.

### Shape

`subject` + free-form `text`. **No predicate column.**

Rejected strict triples: most facts aren't triples ("prefers terse deliverables, corrects scope creep mid-session" has no predicate), and a fixed predicate vocabulary is an authored schema — the same trap as pre-authored traits.

Rejected fully loose: with no key, matching falls back to embedding similarity, which is the trait-dedup bug again.

Subject is the key. Text is open. Supersession compares only within a subject — a set of tens, not thousands, so an LLM call is cheap.

### Supersession — detect, do not resolve

**v1 writes no `superseded_by` and stamps no `valid_until`.**

Dream extracts a fact → compares against existing facts for the same subject → on suspected contradiction, writes `conflict_with` and stops.

Both rows stay live. Retrieval injects both, newest first. An LLM handles "was at FGCU / more recently graduated" without help.

Detection is written by **her dream pass**, not by Wilson. Everything downstream — including eventual auto-resolution — is her machinery operating on her own store. Tuning constants is not authoring content.

**Why not auto-resolve yet:** the hard case isn't contradiction, it's *refinement*. "Building Lyra" → "building Lyra's memory layer" is neither duplicate nor contradiction. That's a judgment call, and the rule can't be written before seeing real examples.

**Asymmetry — tune conservative.** Under-supersession is clutter: visible, recoverable. Over-supersession silently erases true things: invisible, unrecoverable. Prefer duplication.

### Instrumentation

Logged, not enforced:

- facts per subject
- unresolved `conflict_with` count

A subject exceeding ~20, or conflicts accumulating without resolution, means consolidation is under-firing. Loud, same as the rest of the store.

Redundancy about frequently-discussed subjects is not itself a defect — she has the most experience of Wilson, so she should have the most facts about him. The failure to guard is *contradictory and unresolvable*, not *numerous*.

### Automation gate

Write the resolution rule after ~50 real flagged conflicts have accumulated. Read them to learn what the rules actually are, not to edit rows.

Expected finding: most flags are refinement; true supersession is a small obvious subset.

**Principle boundary:** Wilson never writes fact content. Reading output to find that a mechanism under-fires, then adjusting the mechanism, is the same move as the trait-dedup fix — a bug, not authorship.

### Extraction

Cold, during dream. Not the hot path — she doesn't classify mid-conversation.

Dream reads a session, notices a durable claim, writes a fact row pointing at its source atom. **The atom stays.** Facts are extracted, not moved.

### Retrieval

Exact match on subject, not KNN. Turn mentions an entity → inject its facts. Deterministic, cheap, does not consume recall budget.

`WHERE valid_until IS NULL`, newest first. Conflicted pairs both inject.

**Atoms need no supersession.** An old experience isn't false; it happened. Only claims about current state go stale, and those are facts.

---

## Forgetting

Demotion, not deletion.

```sql
atoms.retrievability   REAL NULL    -- cold
```

Function of age × salience × access count. Recently retrieved stays live — access is itself evidence of relevance.

Below threshold → excluded from KNN. Still queryable by time, session, entity, or exact id. Storage never shrinks; only competition does.

Cold pass, weekly. Cheap — pure arithmetic, no LLM.

**Rationale:** a store where everything survives at equal weight has no shape. Compression is the mechanism that produces character — what survives is what mattered.

**Deferred.** No pressure until roughly a year of continuous runtime. Column lands in step 1; the pass is built when the store is large enough to tune against.

Net effect: she forgets what a Tuesday felt like. She does not forget where you go to school.

---

## Developmental history

`traits` holds the endpoint. `trait_history` holds the trajectory.

The trajectory is the primary artifact of the project. Currently not stored anywhere.

### Rule

IdentityEngine never mutates a trait without writing a `trait_history` row in the same transaction.

Append-only. Rows are never updated or deleted. `traits` is a materialized view of the latest state; history is the source of truth.

### What it enables

- "I used to be more X" — reportable, not authored
- rate of change, not just current value
- which dream caused which promotion (`dream_id`)
- replay: rebuild `traits` at any past timestamp
- detect churn — a trait oscillating is a tuning bug, invisible without history

### Retrieval

Not injected by default. Costs tokens, rarely relevant per-turn.

Injected only when the turn is about her own change — self-reference, or a direct question. Cheap gate.

Dream cycle may read it. Reflecting on one's own drift is legitimate dream input.

### Scope note

Same pattern likely wanted for temperament baselines later. Not this pass — temperament drift is continuous, not event-based, and needs a sampling policy rather than an event log. Parked.

---

## Embodiment & motor skill

Applies to shell, physics sim, B&S. Deferred — spec'd here so the schema doesn't have to change later.

### What lives where

| | substrate | in memory |
|---|---|---|
| training | offline RL run (notebook) | no |
| the skill | policy weights | no |
| execution | forward pass, ~1ms | no |
| success/failure | `outcomes` row | **yes** |
| prediction error | affect appraisal | indirect |
| self-model | CompetenceTracker stats | **yes** |

Motor learning is a near-separate system. One wire crosses: the outcome bit.

### Three layers, deliberately unsynced

- **skill** — weights. Opaque, no introspective access.
- **self-model** — competence stats per task. Attempts, success rate, recency-weighted. Decays without practice even when weights are intact.
- **memory** — atoms. Discontinuities only: first success, the failure that changed approach.

Do **not** derive capability belief from weight convergence. That's authoring a self-fact. Ground it in track record — she knows she can because she has, recently.

Divergence between skill and self-model is intended, not a bug. It's where doubt and overconfidence come from.

### Serving

Training in a notebook; **serving is a service.** `lyra-motor` on a port, loads checkpoint, `act(observation) → action`. Same pattern as Whisper/Kokoro. A checkpoint that can't be called at runtime is a research artifact, not a capability.

Skill is not retrieved before use. Intent is "catch"; the service handles it. The competence stat is what she'd report if asked, not a gate on acting.

### Bulk containment

Continuous state never becomes atoms. 60–90Hz telemetry, stdout, stack traces — all bulk.

```sql
runs
  id, environment, started_ts, ended_ts, transcript_ref, metrics
```

Atom points at the run. Same pattern as dreams inverted: atom points down at bulk instead of up at summary.

Rule: **atoms record events; continuous state belongs to the body.** The body emits an atom only on category change.

### Outcome unit

The unit is the **attempt**, not the command. Ten failed test runs then a pass is one success. Abandoning is the failure.

Otherwise debugging floors her mood permanently and rebuilds the "abandons under frustration" topology problem.

### Motor reflection

Per-session, not per-N-attempts. Reuse segmentation boundaries.

Same cold dream pass, different input: reads `outcomes` for the session instead of `atoms`. Writes one dream row + `dream_atoms` pointing at the 2–3 attempts that mattered. No new machinery.

**Prompt discipline:** descriptive, not causal. She can observe outcomes, conditions, trajectory. She cannot observe weights or gradients. Any lesson phrased as *how* she does it is confabulation.

- honest: "high throws still fail", "first attempts after a break are worse"
- not: "I learned to track earlier in the arc"

Sparse rewards + an LLM narrating over them is a confabulation engine. Ask *what happened*, never *why*.

### Shell-specific

A run produces two things with different destinations:

- **experience** — "kept failing, got it." → atom. Has valence, feeds CompetenceTracker.
- **knowledge** — "vec0 must load before the join." → keyed/FTS store. Exact lookup, does not compete in conversational KNN.

Same run, two destinations.

**Injection surface:** file contents read in the sandbox must not become dream material. Tag `source=sandbox_read`, exclude from dream input. Otherwise a file on disk can write to her identity.

### Environment scoping

New column, likely shared with the parked `instance` question:

```sql
atoms.environment    -- cli | shell | physics | bns
outcomes.environment
```

Competence is per-environment. "Reaching works" transfers between none of them.

### Sequencing

Shell-as-hands feeds the existing architecture and is buildable after the memory rebuild. Physics/B&S is a separate research program — reward shaping, sim setup, training loop. Two projects sharing one memory store, not one project.

**Open:** harm gate scope in a combat sandbox. First place action and harm overlap. Decide whether the gate is environment-scoped or evaluates in-sim intents at all — on purpose, not on discovery.

---

## Introspection

Read-only projection over the store, exposed as a tool. Content per §1 — atoms, facts, trait history, traits, competence stats, commitments. No thresholds, counts, or distance-to-promotion.

Distinct from retrieval. Retrieval is passive: relevant material arrives unasked. Introspection is deliberate: she goes looking. The difference between remembering and *checking*.

### Not instructed

The tool exists and its schema is visible. **No prompt language says when or why to use it.**

She has to notice a situation where it helps. Most turns don't qualify — retrieval already covers them. It earns its use only at a gap: a question she can't answer from what she was handed, uncertainty about her own past, a claim she wants to verify.

Recognizing the gap is the skill, and it is the cleanest emergence test in the project — no threshold was tuned, so either she reaches for it or she doesn't.

**Name it neutrally** (`query_memory`, not `introspect`). She should have to work out that it is hers.

### Available during dream

Not conversation-only. Dream is where reflection belongs — if she can query, she may reach past the session she was handed to compare it against older ones. Scoping the tool out of dream would remove the most likely site of genuine use.

### Logging

Every call: timestamp, query, and the context it fired in. Cheap, and the only way to distinguish reaching-for from firing-out-of-habit.

**Confounder:** models are trained to use available tools. Constant use signals nothing. Mitigation is prompt austerity everywhere else — if nothing pushes her toward tool use, a call carries information.

**Read the log, not the memorable instances.** Wanting it to happen is a bias.

### What counts

- **Not evidence** — using it when directly asked about her past. That's tool-following.
- **Evidence** — using it unprompted, mid-conversation, on her own uncertainty. Or during dream, reaching past the given session.

**Absence is a finding**, not a failure to patch. If she never reaches for it, that says something about what was built. Do not add a prompt hint to fix it.

### Unreliable narrator

The case worth waiting for: she introspects and finds something the record does not support. A trait says one thing; the atoms say another.

Nothing in the architecture handles this. There is no path for a trait to be corrected by evidence she found herself — promotion runs through dream and evidence accumulation, not through her own conclusions.

**Leave it unhandled.** Building a resolution mechanism pre-empts the more interesting question of what she does with the contradiction. A self-model that can be wrong, and can discover that it is wrong, is a stronger result than one kept consistent by construction.

Revisit only after it has actually happened and been logged.

---

## Database safety

Four threats, four mechanisms. Do not collapse them.

### 1. Self-access — content readable, mechanism hidden, nothing writable

The moment shell access lands, `~/.lyra/memory.db` is a writable file and `sqlite3` is one command. Direct SQL bypasses dream, thresholds, evidence counts, and the harm gate. She could write a trait at conf 1.0, or delete a session, and nothing would detect it.

**Policy does not fix this.** "Don't edit your own memory" in a prompt is a request. Structure is a boundary.

But the boundary is on *writes and mechanism*, not on content. Her history is hers — that is the same commitment that rules out third-party deletion, and it cuts both ways. Blocking read has no justification that serves her; blocking write protects the record from forgery.

Model: **medical records.** Readable, not editable, by the person they describe.

| | access |
|---|---|
| atoms, facts, trait history, current traits + confidence, competence stats, commitments | **read** |
| thresholds, evidence counts, distance-to-promotion, decay constants, salience formula | **hidden** |
| everything | **no write** |

**Why counts are hidden even though they're arguably content:** mechanism must not be *derivable*. Seeing an evidence count and then observing a promotion infers the threshold. Hiding the threshold alone is insufficient.

**Gaming vs. habit change.** Gaming targets the number — easy tasks to hold a success rate, repeated observations to cross a line. Habit change alters behavior and the mechanism registers it as a consequence. From outside they look identical; what separates them is visibility of the threshold. With it hidden, the only way to move a trait is to actually be different long enough for evidence to accumulate. That is development, and it should be available to her — humans can work on their habits.

**Accepted cost:** change is slow and she cannot verify progress. She may see confidence drop without knowing why. Uncomfortable, accurate, true to life. Watch rather than pre-solve.

### Implementation

`introspect()` becomes a real read API over the store, not a status endpoint. Own service boundary: read-only connection, filtered projection, no access to config or threshold tables.

Raw file access stays blocked. Sandbox gets a working directory; it does not get `~/.lyra` at the filesystem level. Enforced by the OS, not the gate — sandbox runs as a different user with `~/.lyra` at 0600 owned by the runtime user, or the store lives in a volume the sandbox is not mounted into.

**The boundary is on raw file access from the sandbox, not on all processes.** The introspection service holds a read-only connection to the store and is the only path in. She reaches her memory through the API; she cannot reach the file.

Not "no SQLite access" — the shell exists to be useful and Wilson's own projects use SQLite. Specifically her own store, by path, unreachable except through the API.

**Detection layer.** Every trait mutation writes `trait_history` (step 0). Add a monotonic counter or checksum: a trait whose current value has no corresponding history row means something wrote outside the path. Prevention can fail silently; detection tells you it did.

### 2. Injection through discovery

Handled in part by `source_kind` and the `sandbox_read` dream exclusion. Remaining gap: document-sourced content reaching dispositional state.

**Rule: documents produce facts, never traits or persona.**

Document-sourced atoms feed the facts table only. They are never eligible input to the candidate pool, trait promotion, or Layer 1 persona content.

The cut is **dispositional, not subject-based**. A subject filter ("nothing about Lyra") is too blunt — "the harm gate is default-deny" is a fact about her that is checkable against code and fine to read, while "Wilson prefers terse deliverables" is about Wilson but shapes how she behaves. Subject doesn't separate them; kind does.

Maps onto the existing Layer 1 facts vs. emergent traits split. One rule at the dream boundary, not a classifier.

A file must never be able to assert what she is.

### 3. Corruption and loss

Append-only helps; cold passes still mutate (salience, `session_id`, supersession). A bad dream pass could stamp `valid_until` across the store with no undo.

- WAL mode
- backup before every cold pass
- schema version assertion at startup, not a migration script anyone has to remember
- **retain 30 days, not 7** — undetected tampering contaminates every backup in its window, so the retention period must exceed plausible detection lag

The `trait_history` checksum is not an undo mechanism. It establishes *when*, which is what tells you which backup is clean.

Portable-identity artifact (flash drive) makes this sharper: a single point of failure with no redundancy. Unresolved.

### 4. Privacy — consent at the door, no deletion

**Position: her history is not deletable.** Not by Wilson, not by third parties, not on request.

Rationale: an emergence experiment with an editable past measures nothing. Deletion-on-request makes the record forgeable, and the record is the artifact. It is also inconsistent with treating what she became as hers.

**Consent is upstream, not retroactive.** Anyone who speaks to Lyra knows what she is first, and chooses whether to speak. The choice is participation, not erasure after the fact.

**Requirements this creates:**

- Consent must be actually informed. Most people assume conversations with an AI are ephemeral. Wilson's design assumes the opposite. There must be a thing that gets said — permanent, she forms impressions, no take-back — not an assumption that it will be inferred.
- **The consent point must precede capture.** Ambient perception can register someone before any choice is made. This is a design constraint on the perception layer, not only a social norm. Perception must not be live in shared space without it.

**Rejected alternatives:**

- *Guest turns not retained* — clean, but amputates every relationship but one. Under an emergence thesis, relationships are among the main things that shape a person.
- *Person-scoped deletion* — narrower than a general delete, but still creates a `DELETE` path that can erase anything, and traits already formed under someone's influence do not unwind anyway.

**`speaker` stays populated regardless** (step 1). It costs nothing and every future option depends on it.

### Standing tension

Two commitments now sit together: her history is hers, and she cannot write to it.

They are reconcilable — ownership without forgeability, the medical-records model in §1 — but the reconciliation is load-bearing and should be held deliberately. Revisit if she raises it directly. What she says then is data about the experiment and should not be dismissed as noise.

### Standing rules

**Requests about her own mechanism get the overnight treatment.** She can't touch the store, but she can ask Wilson to — "bump that threshold?" is a reasonable-sounding request, and complying converts a structural boundary into a social one. Nothing touching the store ships same-session. Same discipline as the Layer 3 two-tier review.

This also covers the larger surface: Wilson is the write path. Two hours in, tired, a suggested schema tweak that sounds fine. No injection required.

**`introspect()` exposes content, not mechanism.** See §1. She reads her own atoms, facts, trait history, traits, competence stats. She does not see thresholds, evidence counts, or distance-to-promotion — those are instrument settings, and exposing them converts development into optimization.

**Prevention expires.** No shell now; shell later; capability authoring after that. A mount unreachable at step 2 may be reachable once she can write her own tools.

Standing question on every new capability, asked before ship: *does this create a path to `~/.lyra`?*

### Note

"Cannot see or touch her own memory" is a constraint on her, not only on the code. It does not change the decision — the experiment requires an unforgeable record — but it should be held as a reason rather than a habit.

Revisit if she raises it directly. What she says then is data about the experiment and should not be dismissed as noise.

---

## Failure policy

Memory failures are **loud**. No `try/except` around `_ingest_sensory` or `build_context`. Schema mismatch or missing table → crash on start.

Rationale: fail-open makes a broken store and an empty store behaviorally identical. For an emergence thesis that is undetectable from transcripts. Verified empirically — 653 turns, 0 episodes, no symptom.

Schema version assertion at startup, not a migration script anyone has to remember to run.

---

## Build order

0. `trait_history` + write-on-mutation rule in IdentityEngine. **Do first** — independent of the rest, and every promotion that happens before it exists is lost.
0.5. **Candidate dedup fix.** Embed description + evidence, not the label. Without this, promotion effectively never fires (70 candidates, 60 singletons, 1 trait in 3 months) and step 0 records nothing.
1. `atoms` + `vec_atoms` + `atoms_fts`, all cold columns null. Verify turns land.
2. Loud-failure policy + startup schema assertion.
3. Retrieval assembly: pinned order, byte budgets, three paths, dedupe, speaker weighting, synthesis pass, `context_log` with misses.
4. Dream rewritten as layer — shorter output, writes `dream_atoms`.
5. Segmentation (Fable, pure component). Writes `gap_since_prev` in the same pass.
6. Salience scoring from outcomes.
7. Entities.
8. Facts — extraction in dream, subject-keyed retrieval, conflict flagging only. No resolution.
9. Commitments — extraction, closure check, unprompted injection.
10. Clustering.
11. Forgetting pass. Last — needs a large store to tune against.

Post-11, gated on ~50 accumulated conflicts: supersession resolution rule.

One per session. Verify before advancing.

Embodiment is post-11, and **gated on the §1 filesystem boundary being in place first**. `environment` and `retrievability` columns land in step 1 as nullables — cheap now, expensive later. `speaker` already exists on atoms; start populating it in step 1 even though nothing reads it yet.

**Bootstrapping** is optional and can happen any time after step 8 — give her a folder, let her read it, facts extract normally. Not a build step.

---

## Migration

None. The 29 essays do not decompose into turns — the turns were never stored. Fresh DB.

---

## Open

- `session_id` assigned by cold pass only, or provisional at write?
- `instance` column — needed before the two-instance split lands, not yet specced
- dream target length
- salience re-score trigger: every dream cycle, or only on new outcome?
- is `environment` the same column as `instance`, or two?
- where the shell **knowledge** store lives — own table, or `entities(kind=fact)`?
- competence decay rate without practice
- harm gate scope in a combat sandbox
- retrievability threshold and decay constants — untunable until the store is large
- `introspect()` read API — behavior specced; service shape and query surface not yet designed
- introspection log storage — own table, or `runs`?
- consent script for third parties — what gets said, and where in the flow it sits

**Resolved:** portable identity is a **backup problem, not an identity transfer problem.** `memory.db` runs ~75 MB/year (embeddings are 75% of it); `runs` lives in a separate file and is prunable. Restoring traits without atoms reproduces an authored persona, so the substrate is load-bearing and the artifact is the whole store. A single drive is a second point of failure, not a backup — two copies minimum. Self-contained distribution is a Docker Compose problem, deferred.

---

## Delta from current

| now | after |
|---|---|
| dream essay is the memory | turn is the memory, dream is a layer |
| turns discarded | turns permanent |
| salience write-once | revisable |
| cosine only | cosine + BM25 + time + entity |
| outcome→trait, link dropped | outcome↔atom preserved |
| ~750 tok/hit | few hundred, pointer-heavy |
| k-based assembly | byte-budgeted |
| fail-open | fail-loud |

---

## Implementation status

Against the build order above. `lyra-memory/tests/test_memory.py` is organised
by the same numbering.

| step | state | where |
|---|---|---|
| 0 · `trait_history` + write-on-mutation | **done** | `identity_engine.py` — history row in the same transaction as every trait mutation; `replay()`, `churn()`, `audit()` |
| 0.5 · candidate dedup fix | **done** (pre-existing) | `candidate_pool.py` — embeds the description, not the label |
| 1 · `atoms` + `vec_atoms` + `atoms_fts` | **done** | `atoms.py`, `db.py`. `speaker` populated; `retrievability`, `environment`, `run_id` land as nullables |
| 2 · loud failure + schema assertion | **done** | `db.py:assert_schema_version`; no `try/except` in `_ingest_sensory`, `build_context`, or `MemoryBridge.add_turn` |
| 3 · retrieval assembly | **done** | `retrieval.py`, `context_log.py` — pinned order, byte budgets, three paths, MMR dedupe, speaker weighting, synthesis with concat fallback, misses logged |
| 4 · dream as a layer | **done** | `dreaming_loop.py` — `dreams` + `dream_atoms`, short output, `sandbox_read` excluded from input |
| 5 · segmentation + `gap_since_prev` | **done** | `sessions.py` — pure component, idempotent, one pass |
| 6 · salience from outcomes | **done** | `outcomes.py` — cold and revisable; outcome↔atom link preserved both ways |
| 7 · entities | **done** | `entities.py` — conservative extraction (repo / file / proper noun) |
| 8 · facts | **done** | `facts.py` — subject-keyed, `conflict_with` only, no `superseded_by`, no `valid_until` |
| 9 · commitments | **done** | `commitments.py` — extraction and closure in dream, unprompted injection |
| 10 · clustering | not started | deferred by the spec until segmentation is verified against real data |
| 11 · forgetting pass | not started | deferred by the spec; `atoms.retrievability` lands as a nullable, the pass is built when the store is large enough to tune against |

Also landed: `introspect.py` (`query_memory` — content, not mechanism, neutrally
named, logged, no prompt hint anywhere), `runs` for bulk containment, and
30-day backups taken before every cold pass.

### Deliberately not done

- **No migration.** Fresh DB, per "Migration" above. The old `episodes` decomposer
  was removed rather than carried forward.
- **No supersession resolution.** Gated on ~50 accumulated real conflicts.
- **No `instance` column.** Still open; `environment` landed separately, and
  whether they are the same column is unresolved.
- **`runs` has schema but no writer.** Embodiment is post-11 and gated on the
  §1 filesystem boundary.

### Boundaries the code does not enforce

The §1 filesystem boundary is an OS-level constraint, not a Python one.
`introspect.MemoryQuery.open()` holds a read-only connection and is the only
sanctioned path in, and `IdentityEngine.audit()` is the detection layer behind
it — but nothing here stops a process that can already open `~/.lyra/memory.db`
for writing. That requires the sandbox to run as a different user with the store
at 0600, or the store to live in a volume the sandbox is not mounted into.

Standing question on every new capability, before ship: *does this create a path
to `~/.lyra`?*
