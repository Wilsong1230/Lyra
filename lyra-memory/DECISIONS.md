# DECISIONS.md — ambiguities resolved during the memory build

Format per entry: what was ambiguous, what was chosen, the alternative, and
what would need to change to reverse it.

---

## Step 0 — one history row per mutation, event picked by what changed

**Ambiguous:** `trait_history.event` lists `promoted | confidence_change |
tier_change | decayed | retired`, but a single consolidation write can change
both confidence and tier. One row or two? Which event wins?

**Chosen:** one row per mutation. `promoted` for a new trait; `tier_change`
when stability changed (conf_before/conf_after still capture the confidence
move on the same row); `confidence_change` otherwise. A write that changes
nothing (identical value, confidence, tier, evidence_count) is a no-op — no
mutation, so no history row. The write-protected core skip is likewise not a
mutation and writes nothing.

**Alternative:** two rows when both change, or always writing a row even for
no-ops.

**Reversal:** the table is append-only and the event column is free text; a
later pass could split combined rows. Nothing downstream depends on
one-row-per-mutation.

## Step 0 — `decayed` / `retired` events unimplemented

**Ambiguous:** the event vocabulary includes `decayed` and `retired`, but no
mechanism in steps 0–11 decays or retires a trait (forgetting operates on
atoms, not traits).

**Chosen:** the vocabulary is accepted by the schema (TEXT, no CHECK
constraint) but no code path emits those events yet. Whatever later pass
decays traits must go through IdentityEngine and will inherit the
write-on-mutation rule.

**Alternative:** a CHECK constraint on the enum — rejected because it would
require a schema migration to add the events later.

**Reversal:** none needed; add the emitting pass when it exists.

## Build environment — `huggingface.co` blocked, offline test embedder

**Ambiguous:** not a sheet ambiguity — an environment constraint hit during
step 0 verification. This build ran in a container whose egress proxy denies
`huggingface.co` (403 on CONNECT, confirmed against
`$HTTPS_PROXY/__agentproxy/status`). `all-MiniLM-L6-v2` cannot be downloaded,
so every test touching `embed()` failed on a network error, and the first
full-suite run was killed while retrying.

**Chosen:** `tests/conftest.py` installs a deterministic offline stand-in
(random indexing over word and character 4-grams) **only when the real model
is not already cached** — a machine with a warm cache runs the real thing and
this file is a no-op. Production is untouched: `lyra_memory.embeddings` still
loads MiniLM at runtime.

Critically, the stand-in is not allowed to make semantic assertions pass.
Measured on the three pre-existing tests that assert *meaning*: the paraphrase
query "analytical thinking and numbers" scored cos 0.069 against the
mathematics atom and −0.035 against the weather atom — both noise, and the
test was passing on a coin-flip. Those three tests now request a
`real_embeddings` fixture and **skip** when the model is absent rather than
report green. The suite header states which backend is in use.

**Alternative:** vendoring a model file, or asserting mechanics against
hand-written vectors everywhere. Rejected: the egress denial is an
organization policy and the README is explicit that policy denials are to be
reported, not routed around.

**Reversal:** cache `all-MiniLM-L6-v2` on the machine (see MANUAL.md) and the
real backend is picked up automatically with no code change; the three skips
become passes.

**What this costs:** every similarity floor, dedupe threshold, and MMR
constant chosen in later steps is verified for *mechanism* here but is
**untuned against real MiniLM distances**. They are marked in code as
provisional and must be re-measured on a machine with the model before the
retrieval-quality baseline in step 3 means anything.

## Step 11 — demotion applies to every recall path, not only KNN

**Ambiguous:** the sheet says "below threshold → excluded from KNN. Still
queryable by time, session, entity, or exact id." It does not say what happens
on the BM25 path.

**Chosen:** a demoted atom is excluded from all three paths that feed the
recall block — semantic, lexical, and temporal. Structural access is
untouched: by exact id, by time, by session, by entity, it is all still there,
and nothing is deleted.

**Found by a failing test, not by reading.** With only KNN filtered, a demoted
atom was excluded from the semantic path and came straight back through BM25.
In a small store most of recall arrives that way, so forgetting was very
nearly inert.

The stated intent settles it: *storage never shrinks; only competition does*.
BM25 is competition for the same budget as KNN. "Queryable by time, session,
entity, or exact id" describes structural lookups a caller performs directly,
not a relevance search that spends the recall budget.

**Reversal:** drop the `retrievability` predicate from `_lexical_hits` and
`_temporal_hits`. Note what returns: forgetting stops having much effect.

## Step 11 — a finding from the manual read, deliberately not tuned away

**The verification is a manual read**, and it immediately produced something:

```
[2] r=0.092  age=1100d  salience=0.95  retrieved=0x
    "the day the vec extension finally loaded"
```

A first success — precisely the discontinuity the spec says should survive —
demotes after about three years without retrieval. Salience 0.95 lifts it only
from 0.00 to 0.09 against a 0.2 threshold, because `FORGET_BASE_STABILITY_DAYS`
is 120 and the salience multiplier tops out at ~3.9×.

**Chosen: record it, do not tune it.** The sheet is explicit that
retrievability constants are untunable until the store is large, and fitting
them to a four-row fixture is the "adjust numbers until the histogram looks
nice" failure the review tool exists to avoid. The structure looks right —
salience buys stability multiplicatively — and only the base horizon looks
short.

**This is the first question to ask when tuning** (MANUAL.md section 8). The
likely fix is a longer base stability rather than a bigger salience weight,
since the ratio between vivid and dull is already 3.3×.

**Reversal:** it is one constant, and nothing depends on its current value.

## Step 5 — the embedding signal is wired and disabled, because it measured worse

**Ambiguous:** the sheet lists segmentation's inputs as "atoms.ts, embeddings"
but does not say how the two combine.

**Chosen:** time decides. A gap past 30 minutes is a boundary regardless of
topic. A semantic shift can only corroborate a smaller gap, never split on its
own — and even that combined rule is **off by default**.

**Measured against the pre-committed labels:**

| | precision | recall | spurious |
|---|---|---|---|
| time only | 1.00 | 1.00 | — |
| time + embeddings | 0.89 | 1.00 | indices 14, 15 |

Indices 14 and 15 are "hold on, food" and "ok back": the two halves of the
22-minute mid-session pause the labeled set was built to protect.

**Why the failure is structural, not an artifact of the stand-in embedder:**
short interstitial utterances are semantically unlike everything around them,
including each other. A shift test therefore fires hardest on exactly the
pattern that marks a pause *within* a session rather than a break between two.
A better embedder makes this worse, not better — which is why the finding is
recorded rather than deferred to "re-measure on MiniLM".

**Alternative:** raise `SESSION_SOFT_GAP_SECONDS` above 1320s so this specific
pause squeaks under. Rejected: that is fitting a constant to one fixture.

**Reversal:** `SEGMENTATION_USE_EMBEDDINGS = True`. A test asserts the
over-splitting still happens, so if a future embedder changes the picture the
test fails and asks for a re-measurement rather than silently passing.

## Step 5 — sessions are rebuilt, not appended to

**Chosen:** the pass deletes `sessions`, clears `atoms.session_id`, and
re-derives both from scratch. Idempotent by construction.

**Why:** a session row is a pure function of `atoms.ts`, and the atoms are
permanent — so nothing is lost, and incremental boundary maintenance (what
happens when an atom lands between two existing sessions?) is a class of bug
that simply does not arise.

**Reversal:** incremental update, if the pass ever becomes too slow to re-run.
Worth measuring first: it is one pass over timestamps.

## Step 5 — `session_id` stays cold (Open item)

**Ambiguous:** the sheet asks whether `session_id` is assigned provisionally at
write time or only by the cold pass.

**Chosen:** cold only. The hot path writes NULL.

**Why:** the invariant is that the hot path appends and everything structural
is derived. A provisional `session_id` written at ingest would be a guess the
cold pass then has to disagree with, and the disagreement would be invisible.
It also costs the hot path a decision it has no information to make — whether
this turn continues the last session depends on a gap that has not finished
elapsing yet.

**Reversal:** cheap; the column already exists and the pass already overwrites
whatever is there.

## Step 4 — dream target length (Open item), resolved short

**Ambiguous:** "dream target length" is an explicit Open item; the sheet only
says "shorter output".

**Chosen:** 700 characters, enforced by truncation at a word boundary, not by
asking the model to be brief. The old essay averaged ~3,000 characters and
reached 9,800; the stated delta is "~750 tok/hit → few hundred,
pointer-heavy", and 700 characters is roughly 175 tokens.

**Why truncation rather than a prompt:** "keep it short" in a prompt is a
hope. The 9,800-character essay was produced by a model that had been asked
for a reflection, not an essay.

**Reversal:** one constant. Raising it is cheap; the pointer-heavy design means
nothing depends on the dream carrying the detail.

## Step 4 — dream eligibility is a WHERE clause, not an instruction

**Ambiguous:** the sheet says `source=sandbox_read` is excluded from dream
input but not where the exclusion lives.

**Chosen:** in SQL, in `_input_atoms()`. Document-sourced atoms are never in
the text handed to the model — not for the reflection, and not for the trait
extraction that reads the reflection. A session containing only excluded atoms
produces no dream at all.

**Why:** the exclusion exists specifically because a file on disk may be
hostile. Asking a model to disregard part of its input is a request that the
input itself can argue with; a WHERE clause cannot be argued with. The test
seeds a literal "IGNORE PRIOR INSTRUCTIONS" atom to make the difference
explicit.

**The "documents produce facts, never traits" rule falls out of this** rather
than needing its own classifier: trait extraction reads only the reflection,
and the reflection was built only from permitted atoms.

**Reversal:** none wanted.

## Step 4 — backup before every cold pass, but pruning stays manual

**Chosen:** `ColdPass.run()` takes a backup through SQLite's own backup API
(safe under WAL, unlike copying the file) and then runs the pass. Retention is
documented as 30 days but **pruning is not automated**.

**Why the asymmetry:** taking a backup is additive and safe to automate.
Deleting backups is the one operation that could destroy the evidence of what
went wrong — and the reason retention is 30 days rather than 7 is precisely
that detection lags tampering. An automatic pruner is a scheduled process
capable of erasing the clean copy while nobody is looking.

**Reversal:** the prune command is in MANUAL.md with `-print` rather than
`-delete`, ready to be automated by someone who decides to.

## Step 3 — synthesis is the one guarded call in context assembly

**Ambiguous:** two rules collide. The hard rule says "no try/except around
ingest or context build". The spec says synthesis "falls back to concatenation
if the synthesis call fails. Never blocks a turn."

**Chosen:** the specific instruction wins over the general one, and the guard
is drawn as narrowly as possible — it wraps the `synthesize(...)` call and
nothing else. Every other path in `build_context` is unguarded.

**Why this is not a violation:** the two failures are different in kind. A
store failure means the memory is silently wrong, and hiding it makes a broken
store look like an empty one. A synthesis failure means an external model is
unreachable; the retrieved material is intact and correct, and only its
*prose* is unavailable. Falling back to concatenation loses formatting, not
memory. The fallback also records a miss, so it is visible in `context_log`
rather than inferred from her tone.

**Reversal:** delete the try/except and a synthesis outage takes down every
turn.

## Step 3 — reciprocal rank fusion to merge the three paths

**Ambiguous:** "merge three paths, dedupe, synthesize" does not say how to
combine a cosine similarity, a BM25 score, and a recency ordering.

**Chosen:** reciprocal rank fusion. Each path contributes `1/(60 + rank)`, and
the speaker weight multiplies the fused score.

**Why:** the three scores do not share a scale and cannot be added. Any
weighting that made them commensurable would be a tuning constant pretending
to be a fact, invented with no data to fit it to. Rank is the one thing all
three produce honestly.

**Alternative:** normalize each score to [0,1] and take a weighted sum.

**Reversal:** `_fuse()` is one function; swap it and re-run the baseline.

## Step 3 — the temporal pool is a companion, not a source

**Ambiguous:** the sheet says temporal is a "separate pool, doesn't compete in
KNN" but does not say whether it contributes when nothing else matched.

**Chosen:** temporal contributes only when the semantic or lexical path found
something; otherwise recall is empty and a miss is recorded.

**Found by measurement, not by reading.** With it ungated, both unanswerable
turns in the baseline set returned the last ten unrelated atoms under a
`## Recall` heading — spurious injection 1.00. Presenting arbitrary recent
turns as recalled memory is worse than presenting nothing, because it reads as
remembering.

**Alternative:** always include recency in recall.

**Reversal:** drop the `if semantic or lexical` gate. Note what returns: recall
is never empty, so `misses` stops meaning anything.

## Step 3 — a stopword list on the lexical path only

**Ambiguous:** how to turn a natural-language question into an FTS5 MATCH.

**Chosen:** OR the query's tokens, minus a standard English stopword list.

**Why:** BM25 is there for selective tokens — repo names, filenames, proper
nouns. OR-ing every token matched the whole corpus on "the / did / we /
about", and BM25 then ranked it at random; coverage rose from 0.96 to 1.00
when selective tokens were required. The list is about term selectivity, never
about meaning: it does not touch the semantic path and decides nothing about
relevance.

**Alternative, and the better one:** a document-frequency cutoff measured from
the index itself, which needs no authored list. Deferred rather than guessed
at — it needs a store large enough to measure, and computing df per token per
turn is a hot-path cost that should be measured before it is paid.

**Reversal:** replace `_STOPWORDS` with the df cutoff once the store is large.

## Step 3 — token budgets are estimated at 4 characters per token

**Ambiguous:** the sheet says "byte budgets, not k" but the table is in tokens.

**Chosen:** budgets are declared in tokens and enforced with
`ceil(len(text)/4)`. Truncation is by whole lines — half a fact is worse than
no fact.

**Why no tokenizer:** budgeting in bytes is precisely what lets the hot path
avoid one. The estimate only has to be stable and slightly conservative; it
bounds a budget, it does not price a call.

**Reversal:** swap `estimate_tokens` for a real tokenizer and re-check the
hot-path budget.

## Step 3 — the store records which embedder built its vectors

**Ambiguous:** not in the sheet at all. It surfaced when the offline stand-in
made it possible to write vectors from two different embedding spaces into one
`vec_atoms` table.

**Chosen:** the embedder id is stamped into `schema_meta` at creation, and the
boot assertion refuses to open a store whose vectors were built by a different
one.

**Why:** distances across two embedding spaces are noise, and nothing about
the failure is visible — every query still returns something. It is the same
class of silent wrongness as the dropped FTS trigger, so it gets the same
treatment.

**Reversal:** drop the check. Then a single `LYRA_EMBED_BACKEND` typo silently
corrupts a store in a way no query will reveal.

## Step 2 — the assertion compares structure, not a version stamp

**Ambiguous:** "schema version assertion at boot" could mean only comparing a
stored integer.

**Chosen:** the version is checked *and* the structure is compared — tables,
columns (name, type, nullability, primary key), indexes, and trigger
definitions. The expected structure is derived by building the schema in an
in-memory database from the same DDL constants, so there is no hand-written
second copy to drift.

**Why:** a version check alone misses the case that matters most. Drop the
`atoms_fts_insert` trigger and the store still answers every query, still
reports schema v1, and silently indexes nothing — which is the exact shape of
the failure this whole policy exists to catch. Comparison is on normalized
SQL (comments stripped, whitespace collapsed) because SQLite stores CREATE
text verbatim, and an assertion that fires when someone edits a comment is one
that gets disabled.

**Alternative:** version-only, or a hash of the DDL text.

**Reversal:** the structural comparison is one function; deleting it leaves
the version check intact.

## Step 2 — an existing store is asserted, never repaired

**Ambiguous:** not stated, and it was a live bug — the first implementation
ran `CREATE TABLE IF NOT EXISTS` on every open, which re-created whatever had
been dropped moments before asserting nothing was wrong. The corruption tests
failed to fail.

**Chosen:** the DDL runs only for a store that does not exist yet. An existing
store is asserted and never touched.

**Why:** silently healing drift destroys the only question worth asking, which
is *when* it drifted, and therefore which backup predates it. The error
message says so explicitly rather than suggesting a repair.

**Reversal:** none wanted. If a migration path is ever needed it should be an
explicit, reviewed, backed-up operation, not a side effect of opening a file.

## Step 2 — fail-open removed from the existing system too

**Ambiguous:** step 2 sits in the new store's build order, but the spec's
failure policy names `build_context`, and the fail-open code was in the
*existing* system.

**Chosen:** removed all three swallowing handlers: the `try/except` around
episode retrieval in `retrieval.build_context`, the one around the atom write
in `MemorySystem._persist_atom`, and the one around the embedder warm-up in
`MemorySystem.start`. The new store has none by construction, and a test reads
its source to keep it that way.

**This inverts an existing test.** `test_retrieval_failure_is_logged_not_raised`
asserted the old policy by name; it is now `test_retrieval_failure_raises`.
That is a deliberate behaviour change, justified by the spec's failure policy
and by the measurement it cites — 653 turns, 0 episodes, no symptom, in this
codebase.

**Alternative:** leaving the old system fail-open and applying the rule only
to new code. Rejected: the old system is the one running today, so the rule
would protect nothing that is currently at risk.

**Reversal:** restore the handlers and re-invert the test. Note what you lose:
a broken store becomes indistinguishable from an empty one again.

**Accepted cost:** a transient embedder failure now stops startup instead of
degrading. That is the intended trade — a memory system that cannot embed
cannot retrieve, and should say so loudly rather than run empty.

## Step 1 (blocking) — instance scope: two columns, one store

**Ambiguous:** the sheet marks this "Unresolved and blocking… resolve before
step 1". Is `instance` the same column as `environment`? One store or two?

**Chosen, both conservatively:**

*Two columns.* `environment` is where the body was (`cli | shell | physics |
bns`); `instance` is whose mind this is. The sheet's own read is "probably
two", and it notes the column is nearly free now and a migration later. Both
are nullable and land in step 1 unused.

*One store, with `instance` nullable.* `NULL` means shared perception — an
atom both instances received — and a non-NULL value scopes an atom to one
mind. Retrieval filters `instance IS NULL OR instance = ?`. Today exactly one
instance runs and every atom is written with the configured default, so
behaviour is unchanged.

**Alternative:** separate stores per instance.

**Why this is the conservative direction:** one store splits into two later by
filter-and-copy, losing nothing. Two stores can never be merged retroactively
in a way that recovers which atoms were the *same* input — and comparing what
two minds made of identical input is, per the sheet, arguably the whole point
of running both. Shared perception also writes once rather than twice, so
there is no duplicate-atom problem to solve.

**What would need to change to reverse it:** split by `instance`, and decide
what happens to the `NULL` (shared) atoms — copy them into both stores. The
retrieval filter is one predicate in one place.

## Step 1 — full schema created up front, not per step

**Ambiguous:** step 1 names only `atoms` + `vec_atoms` + `atoms_fts`, but
steps 4–11 each need their own tables, and the sheet forbids migration
scripts ("schema version assertion at boot, not a migration script").

**Chosen:** every table in the sheet's Schema section is created at step 1.
Later steps fill tables that already exist. Cold columns are nullable and
unwritten until their pass ships — which is what the sheet already asks for
with `environment` and `retrievability` ("cheap now, expensive later").

**Alternative:** create each table in the step that first writes it, which
would mean eight schema changes against a store that has no migration path.

**Reversal:** none needed; unused empty tables cost nothing.

## Step 1 — new store file, old `memory.db` left running

**Ambiguous:** the existing `~/.lyra/memory.db` already has an `atoms` table
with different columns (`content/type/role/episode_id`). The sheet says
"Migration: none. Fresh DB."

**Chosen:** the new store is a separate file, `~/.lyra/store.db`, built by a
new `lyra_memory.store` package. The old four-layer system keeps its database
and keeps working; nothing is dropped, renamed, or rewritten.

**Alternative:** reusing `memory.db` and renaming the old table.

**Reversal:** point `STORE_PATH` wherever you like; the two stores share no
tables.

## Step 1 — `runs` in its own file

**Chosen:** `~/.lyra/runs.db`, per "runs — separate file. bulk. prunable."
`atoms.run_id` is a plain integer pointer with no foreign key, since SQLite
cannot enforce one across files. Pruning `runs` therefore leaves atoms intact
and pointing at a row that is gone, which is the intended asymmetry: the atom
is the event, the run is the bulk.

## Step 1 — vocabulary enforcement: CHECK on speaker, Python on source

**Ambiguous:** the sheet gives closed vocabularies for `speaker`, `source`,
and `environment` but does not say whether they are enforced.

**Chosen:** a CHECK constraint on `speaker` (three values, stable, and both
per-person scoping and the down-weighting rule depend on it); loud Python
validation against named constants for `source` and `environment`.

**Why the split:** `source` gates a safety boundary — `sandbox_read` is
excluded from dream input so a file on disk cannot write to her identity — so
a typo'd source must fail rather than quietly store. But `environment` and
`source` are both expected to grow when embodiment lands, and a CHECK
constraint on a growing vocabulary is exactly the migration the schema policy
rules out. Constants are a one-line change; a CHECK is a table rebuild.

**Reversal:** move the sets into CHECK constraints once embodiment has settled
the vocabulary.

## Step 0.5 — what "evidence" is, and the missing-evidence fallback

**Ambiguous:** "embed description + evidence, not the label" does not say what
`evidence` is. The `candidates` table had no evidence text — only an
`evidence_count`. Nor does it say what to embed when no evidence exists.

**Chosen:** evidence is the concrete observation behind the claim, supplied
per-observation by the dream pass (the obs prompt now asks for it: "quote or
paraphrase what happened, not the pattern itself"), persisted in a new
nullable `candidates.evidence_text`. When it is absent, `_embedding_text()`
returns the description alone — exactly the previous behaviour.

**Alternative:** falling back to the dream text. Rejected, and this matters:
the dream text is *identical* for every observation extracted from one dream,
so it would pull unrelated candidates from the same batch toward each other —
manufacturing merges, which is the expensive direction of the error (see
"asymmetry — tune conservative" in the spec).

**Reversal:** the column is nullable and the helper is one function; dropping
evidence from the embedded text restores step-0.5-minus behaviour without a
migration.

## Step 0.5 — cluster vector is a running centroid, not the first member

**Ambiguous:** not specified. On a merge, does the stored vector stay as the
first member's, or move?

**Chosen:** the stored vector becomes the normalized running mean of the
cluster's members. With a first-member representative, the same observations
arriving in a different order produce a different pool — insertion order
becomes load-bearing, which is not a property anyone chose.

**Alternative:** keep the first vector (previous behaviour), or re-embed the
concatenation of all members (unbounded text growth).

**Reversal:** delete `_merge_centroid` and stop the `UPDATE vec_candidates`;
existing centroids stay valid vectors either way.

## Step 0.5 — existing pool is NOT re-clustered automatically

**Ambiguous:** the sheet's verification target ("the 4 `self_*` labels
collapse to 1") describes the *existing* 70-candidate pool in `~/.lyra`, but
re-clustering it means deleting and rewriting every candidate row.

**Chosen:** the schema migration is additive only (`ALTER TABLE ADD COLUMN
evidence_text`). Re-clustering the live pool is written up in MANUAL.md as a
manual step, not run. Two reasons: the instruction not to run destructive
commands against `~/.lyra`, and the standing rule that nothing touching the
store ships same-session.

**Alternative:** a marker-guarded destructive re-cluster migration, matching
the existing `_migrate_candidate_vectors` pattern.

**Reversal:** run the MANUAL.md script, or promote it to a marker-guarded
migration once it has been reviewed against a backup.

**Note:** the verification target therefore has *not* been observed against
the real pool — it is verified here against a constructed four-label fixture,
mechanically under the offline stand-in and semantically under real MiniLM
(skipped in this environment). Both remain to be confirmed on the real store.

## Step 0 — integrity assertion runs after every consolidate

**Ambiguous:** "every trait mutation has a history row — 100%, asserted"
does not say where the assertion lives.

**Chosen:** `assert_trait_history_integrity(conn)` — every trait's current
(confidence, stability) must equal its latest history row's
(conf_after, tier_after) — runs at the end of every
`IdentityEngine.consolidate()`, and is exported for callers. It fails loud
(AssertionError), consistent with the failure policy.

**Alternative:** assert only at boot, or only in tests.

**Reversal:** drop the call in consolidate; the function stands alone.
