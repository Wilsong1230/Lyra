# MANUAL.md — steps to run by hand

Nothing in this build runs destructive commands against `~/.lyra`, and nothing
here has been run for you. Anything that would touch the live store, or that
could not be done in the build container, lands here.

---

## 1. Cache the embedding model (required for full verification)

The build container's egress proxy denies `huggingface.co` (403 on CONNECT),
so `all-MiniLM-L6-v2` could not be downloaded. Tests fall back to a
deterministic offline stand-in, and the three tests that assert real semantics
**skip** rather than pass falsely.

On a machine with network access to huggingface.co:

```bash
cd lyra-memory
./venv/bin/python -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('all-MiniLM-L6-v2')
print('cached')
"
./venv/bin/python -m pytest -q          # header should read: embeddings: real (all-MiniLM-L6-v2)
```

Expected after caching: 0 skips. Confirm the header says `embeddings: real`
before trusting any retrieval-quality number.

## 2. Re-tune retrieval constants against real MiniLM distances

Every similarity floor and dedupe threshold introduced from step 3 onward was
chosen for mechanism, not calibrated against real embedding distances (see
DECISIONS.md, "Build environment"). After step 1 above, re-measure on the real
store and adjust. They are flagged `PROVISIONAL` in `config.py`.

## 3. Re-cluster the existing candidate pool (step 0.5)

Step 0.5's verification target is the real pool: *the four `self_*` labels
collapse to 1*. That could not be observed here — `~/.lyra` is not on this
machine, and re-clustering deletes and rewrites every candidate row, which is
both destructive and the kind of change that should not ship same-session.

The code fix is live for all *new* observations. Existing rows keep their
label-era vectors until re-clustered. To do that, **after taking a backup**
(section 4) and on a machine with the real model cached:

```bash
cd lyra-memory
./venv/bin/python - <<'PY'
import asyncio, math, struct
from lyra_memory.config import DB_PATH, EMBED_DIM, CANDIDATE_DEDUP_THRESHOLD
from lyra_memory.candidate_pool import _embedding_text, _merge_centroid
from lyra_memory.db import init_db
from lyra_memory.embeddings import embed

THRESHOLD = math.sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)

async def main(apply: bool = False):
    conn = await init_db(DB_PATH)
    rows = await (await conn.execute(
        "SELECT id, trait_name, trait_value, evidence_count, last_seen, category,"
        " evidence_text FROM candidates ORDER BY id")).fetchall()
    kept = []
    for _id, name, value, count, seen, cat, evidence in rows:
        raw = await embed(_embedding_text(value, evidence))
        v = struct.unpack(f"{EMBED_DIM}f", raw)
        best_i = best_d = None
        for i, k in enumerate(kept):
            d = math.dist(v, struct.unpack(f"{EMBED_DIM}f", k["raw"]))
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        if best_d is not None and best_d < THRESHOLD:
            k = kept[best_i]
            k["raw"] = _merge_centroid(k["raw"], raw, k["evidence_count"])
            k["evidence_count"] += count
            k["last_seen"] = max(k["last_seen"], seen)
            k["merged"].append(name)
        else:
            kept.append(dict(trait_name=name, trait_value=value, evidence_count=count,
                             last_seen=seen, category=cat, evidence_text=evidence,
                             raw=raw, merged=[name]))
    print(f"{len(rows)} candidates -> {len(kept)} clusters")
    for k in kept:
        if len(k["merged"]) > 1:
            print("  merge:", ", ".join(k["merged"]))
    if not apply:
        print("\nDRY RUN — nothing written. Re-run with apply=True to commit.")
        await conn.close()
        return
    await conn.execute("DELETE FROM candidates")
    await conn.execute("DELETE FROM vec_candidates")
    for k in kept:
        cur = await conn.execute(
            "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen,"
            " category, evidence_text) VALUES (?,?,?,?,?,?)",
            (k["trait_name"], k["trait_value"], k["evidence_count"], k["last_seen"],
             k["category"], k["evidence_text"]))
        await conn.execute("INSERT INTO vec_candidates(rowid, embedding) VALUES (?, ?)",
                           (cur.lastrowid, k["raw"]))
    await conn.commit()
    await conn.close()

asyncio.run(main(apply=False))
PY
```

It runs as a **dry run** as written. Read the merge list first and confirm the
four `self_*` labels are among them and that nothing distinct got collapsed,
then change `apply=False` to `apply=True`.

## 4. Record the real retrieval baseline (step 3)

`eval/BASELINE.md` holds a baseline against an **authored** corpus, measured
with the **offline stand-in** embedder. It is a regression check. It is not
the step 3 verification, and it should not be treated as one.

The sheet asks for something specific and it has to happen in this order:

> Label first, build second. Hand-label a small set from existing `history.db`
> (653 CLI turns) *before* writing the pass that operates on it.

To do it properly, on a machine with `~/.lyra` and the model cached:

1. Pick 20 turns from the real history. For each, write down — **before**
   running anything — what you would expect recall to surface, and whether the
   store can answer it at all.
2. Put them in the same shape as `eval/retrieval_baseline.json`: `corpus` (or
   point the harness at the real store), and `turns` with `relevant`,
   `irrelevant`, `expect_miss`.
3. Run `python -m lyra_memory.store.evaluate <your-file>.json` and record the
   four numbers **before** changing any constant.
4. Re-run the same 20 after every retrieval change.

Do not adjust a label because the output disagreed with it. One case in the
shipped set already disagrees (see BASELINE.md) and was deliberately left
alone.

## 5. Read ~50 extracted facts (step 8 verification)

Step 8's check is "facts extracted are true — manual read, ~50 rows". There is
no automated version: whether "studies at FGCU" is true is not a property of
the code. The harness puts each claim next to the atom it came from so the
read is quick.

```bash
cd lyra-memory
./venv/bin/python -m lyra_memory.store.review_facts --limit 50
./venv/bin/python -m lyra_memory.store.review_facts --conflicts   # flagged pairs only
```

Read for three things:

1. **Is the claim true?**
2. **Does the source atom actually support it**, or did extraction infer past
   its evidence?
3. **Is `source_kind` right?** A `document` claim recorded as `stated` is the
   failure that matters most — it launders a file's authority into something
   she treats as first-hand.

Record what fraction of the 50 are wrong before changing the extraction
prompt. If the same *kind* of error repeats, that is a prompt problem; if the
errors are scattered, it is a model problem, and the fix is a different model
rather than more prompt.

**Do not edit fact rows.** Wilson never writes fact content — reading output
to find that a mechanism under-fires and then adjusting the mechanism is the
same move as the trait-dedup fix; editing the rows is authorship.

## 6. Re-measure segmentation on real embeddings

`SEGMENTATION_USE_EMBEDDINGS` is off because turning it on scored worse
against the labeled set (precision 1.00 → 0.89, over-splitting a mid-session
pause). That was measured with the stand-in embedder. The argument for why it
would also fail on MiniLM is in `SegmentationPass`'s docstring, but it is an
argument, not a measurement. On a machine with the model:

```bash
cd lyra-memory
./venv/bin/python -m pytest tests/test_segmentation.py -q
```

`test_the_embedding_signal_is_off_and_this_is_why` fails if embeddings stop
over-splitting — which is the signal to re-measure and reconsider the default,
not to delete the test.

## 7. Read what forgetting demoted (step 11 verification)

Step 11's check is "demoted atoms are ones you'd expect". That is a judgement
about her history, not a property of the arithmetic.

```bash
cd lyra-memory
./venv/bin/python -m lyra_memory.store.review_forgetting
./venv/bin/python -m lyra_memory.store.review_forgetting --survivors
```

Each row shows age, salience and retrieval count, so a surprising demotion can
be traced to which input caused it.

**A finding is already on the record, from a four-atom smoke test:**

```
[2] r=0.092  age=1100d  salience=0.95  retrieved=0x
    "the day the vec extension finally loaded"
```

A first success — the exact kind of discontinuity the spec says should survive
— demotes after roughly three years unretrieved. This was deliberately **not**
tuned away, because the sheet says these constants need a large store and
fitting them to four rows is worse than leaving them wrong and labelled.

So the first tuning question is: **is `FORGET_BASE_STABILITY_DAYS` (120) too
short?** The vivid-to-dull ratio is already 3.3×, so the salience weight is
probably not the problem; the base horizon probably is. Decide it against real
data, and read the list before and after.

Nothing here is urgent: there is roughly a year of continuous runtime before
any of it bites.

## 8. Backup before every cold pass

Hard rule: back up before each cold pass, 30-day retention. Not automated
here, and deliberately not run against `~/.lyra` from the build:

```bash
mkdir -p ~/.lyra/backups
sqlite3 ~/.lyra/memory.db ".backup '$HOME/.lyra/backups/memory-$(date +%Y%m%dT%H%M%S).db'"
# retention: 30 days, NOT 7 — undetected tampering contaminates every backup
# in its window, so retention must exceed plausible detection lag.
find ~/.lyra/backups -name 'memory-*.db' -mtime +30 -print   # review, then -delete
```

Review the `-print` output before ever adding `-delete`.
