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
