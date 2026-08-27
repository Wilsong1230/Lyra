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

## 4. Backup before every cold pass

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
