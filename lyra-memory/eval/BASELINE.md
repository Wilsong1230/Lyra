# Retrieval baseline

Recorded before any tuning, per the sheet: *"Record the numbers before tuning
anything."*

## What this is, and what it is not

**It is a regression baseline.** Run it before and after a retrieval change;
the numbers should not get worse.

**It is not the step 3 verification.** The sheet is explicit:

> Label first, build second. Hand-label a small set from existing `history.db`
> (653 CLI turns) *before* writing the pass that operates on it. Labeling after
> seeing the output is grading your own work.

This corpus is authored, and it was authored *after* the retrieval code was
written — which is the self-grading the sheet warns about. `~/.lyra` is not on
the build machine, so the 653 real turns could not be labeled. The real
baseline is 20 turns hand-labeled from the actual store, before the next
retrieval change. See MANUAL.md section 5.

**Embeddings were the offline stand-in**, not MiniLM (huggingface.co is denied
by egress policy here). The semantic path is therefore surface-level, and BM25
is doing most of the work. Every number below must be re-measured on real
embeddings before it means anything about retrieval quality.

## Numbers

Run: `LYRA_EMBED_BACKEND=hashed python -m lyra_memory.store.evaluate eval/retrieval_baseline.json`

42-atom corpus, 22 labeled turns (20 answerable, 2 not).

| metric | value | reading |
|---|---|---|
| coverage | **1.00** | labeled-relevant material that surfaced |
| leak rate | **0.00** | turns injecting something labeled irrelevant |
| spurious miss rate | **0.20** | a miss fired though the store had the answer |
| spurious injection rate | **0.50** | injected for a query the store cannot answer |

## Reading the two that are not 1.00 / 0.00

**Spurious miss 0.20** — on 4 of 20 answerable turns the *semantic* path
reported nothing above the similarity floor, and BM25 carried the answer. That
is the expected shape under the stand-in embedder, which has no semantics to
speak of. It is the number most likely to improve on real MiniLM, and the one
that says least until then.

**Spurious injection 0.50** — one of the two unanswerable turns still returned
material. The query was "what did we decide about the harm gate in the combat
sandbox"; the corpus contains "source sandbox_read is excluded from dream
input", and FTS5 tokenizes `sandbox_read` into `sandbox` + `read`, so this is
a genuine lexical hit on `sandbox`.

That is arguably correct retrieval against a label that is arguably wrong. It
has deliberately **not** been relabeled: adjusting a label because the output
disagreed with it is precisely the failure mode the sheet names, and the
honest record of this run includes the disagreement.

## What the harness already caught

Two defects, both found by running it rather than by reading the code:

1. **The temporal pool fired unconditionally** (spurious injection was 1.00).
   A query the store could not answer came back with the last ten unrelated
   atoms under a `## Recall` heading — which reads as remembering. Temporal is
   now a companion pool: it contributes only when the semantic or lexical path
   found something, and records a miss when it is withheld.

2. **BM25 was matching on stopwords.** The lexical query OR-ed every token, so
   "what did we decide about…" matched the whole corpus and BM25 ranked it at
   essentially random. Coverage rose from 0.96 to 1.00 once selective tokens
   were required.

Both are now regression tests in `tests/test_context.py`.
